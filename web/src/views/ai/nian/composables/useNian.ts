import {computed, ref} from 'vue';
import {
  type KBEntry,
  type KBSedimentResult,
  type NianFeedItem,
  type NianFeedToday,
  type NianIdeaStatus,
  fetchKbEntries,
  fetchKbDelete,
  fetchKbUpdate,
  fetchKbTagsStats,
  fetchKbTagsClustered,
  type TagCluster,
  fetchNianFeedRerun,
  fetchNianFeedToday,
  fetchNianIdeaStatus,
  fetchNianInboxCommitStream,
  fetchNianTrack,
} from '@/service/api';

// ── 全局单例 state（多个组件共享同一份） ──────────────────────────────────

const PAGE_SIZE = 30;

const items = ref<NianFeedItem[]>([]);
const loading = ref(false);
const loadingMore = ref(false);
const ranking = ref(false);  // 手动重排进行中
const total = ref(0);
const currentOffset = ref(0);
const hasMore = computed(() => currentOffset.value < total.value);

// 万用收件箱
const inboxOpen = ref(false);
const inboxPrefill = ref('');         // 预填文本（QA 沉淀进来时用）
const inboxSourceHint = ref<string | undefined>(undefined);

// 整理中条目（agent 还没完成时显示占位卡片）
export interface PendingItem {
  id: string;
  text: string;
  submittedAt: number;
}
const pendingItems = ref<PendingItem[]>([]);

// ── 加载 feed ─────────────────────────────────────────────────────────────

async function reloadFeed() {
  loading.value = true;
  try {
    const {data, error} = await fetchNianFeedToday();
    if (error || !data) {
      // 失败回退：拉最近条目（按 updatedAt 倒序），让 UI 不至于空白
      const fb = await fetchKbEntries({limit: PAGE_SIZE, offset: 0});
      if (fb.data?.items) {
        items.value = fb.data.items;
        total.value = fb.data.total ?? fb.data.items.length;
        currentOffset.value = fb.data.items.length;
      }
      return;
    }
    if (data.items?.length) {
      items.value = data.items;
      // daily feed 是一次性全量，不支持增量
      total.value = data.items.length;
      currentOffset.value = data.items.length;
    } else {
      // 空 feed：fallback 到全量列表（按 updatedAt 倒序）
      const fb = await fetchKbEntries({limit: PAGE_SIZE, offset: 0});
      if (fb.data?.items) {
        items.value = fb.data.items;
        total.value = fb.data.total ?? fb.data.items.length;
        currentOffset.value = fb.data.items.length;
      }
    }
  } finally {
    loading.value = false;
  }
}

/** 滚动到底部时增量加载 */
async function loadMore() {
  if (loadingMore.value || !hasMore.value) return;
  loadingMore.value = true;
  try {
    // 增量加载只走 fallback 列表（daily feed 是一次性全量，不需要分页）
    const fb = await fetchKbEntries({limit: PAGE_SIZE, offset: currentOffset.value});
    if (fb.data?.items?.length) {
      items.value = [...items.value, ...fb.data.items];
      total.value = fb.data.total ?? total.value;
      currentOffset.value += fb.data.items.length;
    }
  } finally {
    loadingMore.value = false;
  }
}

/** 服务端标签统计 */
async function loadTagsStats(entryType?: string) {
  const {data, error} = await fetchKbTagsStats({entry_type: entryType, limit: 30});
  if (error || !data) return [];
  return data;
}

/** 服务端标签语义聚类 */
async function loadTagsClustered(entryType?: string): Promise<TagCluster[]> {
  const {data, error} = await fetchKbTagsClustered({entry_type: entryType, threshold: 0.5, limit: 100});
  if (error || !data) return [];
  return data;
}

// ── 万用收件箱 ────────────────────────────────────────────────────────────────

function openInbox(prefill?: string, sourceHint?: string) {
  inboxPrefill.value = prefill || '';
  inboxSourceHint.value = sourceHint;
  inboxOpen.value = true;
}

function closeInbox() {
  inboxOpen.value = false;
  inboxPrefill.value = '';
  inboxSourceHint.value = undefined;
}

async function commitInbox(
  text: string,
  sourceHint?: string,
  attachments?: Array<{name: string; path: string; size: number; isImage: boolean}>
) {
  // SSE：长链路 agent 不会被 60s HTTP 超时掐断，期间 heartbeat 把连接保活。
  // 用户感觉仍然无感（抽屉关掉后悄悄跑），等 done 事件再弹结果。

  // 推入整理中列表，让瀑布流顶部显示占位卡片
  const pendingId = `pending_${Date.now()}`;
  pendingItems.value.unshift({id: pendingId, text, submittedAt: Date.now()});

  let result: KBSedimentResult | null = null;
  let errorMsg = '';
  try {
    await fetchNianInboxCommitStream({text, sourceHint, attachments: attachments || []}, (ev) => {
      if (ev.type === 'done') {
        result = ev.result;
      } else if (ev.type === 'error') {
        errorMsg = ev.message || '未知错误';
      }
      // started / heartbeat：仅维持连接，不打扰用户
    });
  } catch (e: any) {
    errorMsg = e?.message || String(e);
  } finally {
    // 无论成败都移除占位卡片
    pendingItems.value = pendingItems.value.filter(p => p.id !== pendingId);
  }

  if (errorMsg && !result) {
    return null;
  }
  if (!result) {
    return null;
  }

  const data: KBSedimentResult = result;
  if (!data.candidates) {
    // 即便 AI 判定没什么可记，也刷一次列表——后端可能仍写了关联数据，
    // 而且刷新是幂等的，不会破坏用户当前视图（detail-modal 走 id 索引）。
    await reloadFeed();
    return data;
  }
  await reloadFeed();
  return data;
}

// ── 卡片操作 ──────────────────────────────────────────────────────────────

async function deleteEntry(id: string) {
  const {error} = await fetchKbDelete(id);
  if (error) {
    return;
  }
  items.value = items.value.filter(x => x.id !== id);
}

async function trackOpen(id: string) {
  // fire-and-forget；失败也不影响 UX
  fetchNianTrack(id, 'opened').catch(() => {});
}

async function setIdeaStatus(id: string, status: NianIdeaStatus) {
  // 走专用端点：不刷 updated_at，状态变化不会把这条灵感顶到列表前。
  // 但响应回来后必须把新数据合并回 items，列表 UI 才会重新渲染（灰色 + 删除线）。
  const {data, error} = await fetchNianIdeaStatus(id, status);
  if (error || !data) {
    return null;
  }
  const idx = items.value.findIndex(x => x.id === id);
  if (idx >= 0) {
    items.value.splice(idx, 1, {...items.value[idx], ...data});
  }
  return data;
}

// ── 编辑（更新整条） ──────────────────────────────────────────────────────

async function updateEntry(id: string, patch: Parameters<typeof fetchKbUpdate>[1]) {
  const {data, error} = await fetchKbUpdate(id, patch);
  if (error || !data) {
    return null;
  }
  const idx = items.value.findIndex(x => x.id === id);
  if (idx >= 0) {
    items.value.splice(idx, 1, {...items.value[idx], ...data});
  }
  return data;
}

// ── 手动重排 ──────────────────────────────────────────────────────────────

async function rerunRanking() {
  if (ranking.value) return;
  ranking.value = true;
  try {
    const {data, error} = await fetchNianFeedRerun();
    if (error || !data) {
      return;
    }
    await reloadFeed();
  } finally {
    ranking.value = false;
  }
}

// ── helpers ───────────────────────────────────────────────────────────────

function entryById(id: string): KBEntry | undefined {
  return items.value.find(x => x.id === id);
}

export function useNian() {
  return {
    // state
    items,
    loading,
    loadingMore,
    ranking,
    total,
    hasMore,
    inboxOpen,
    inboxPrefill,
    inboxSourceHint,
    pendingItems,
    // actions
    reloadFeed,
    loadMore,
    loadTagsStats,
    loadTagsClustered,
    openInbox,
    closeInbox,
    commitInbox,
    deleteEntry,
    trackOpen,
    setIdeaStatus,
    updateEntry,
    rerunRanking,
    entryById,
  };
}

export type {NianFeedItem, NianFeedToday};
