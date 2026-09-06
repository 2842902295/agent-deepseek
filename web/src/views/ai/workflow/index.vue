<script setup lang="ts">
import {computed, defineAsyncComponent, nextTick, onBeforeUnmount, onMounted, ref} from 'vue';
import {useRoute, useRouter} from 'vue-router';
import {fetchCreateWorkflow, fetchDeleteWorkflow, fetchListWorkflows, type AgentSession, type WorkflowListItem} from '@/service/api';
import AttachmentPreviewModal from '@/components/common/attachment-preview-modal.vue';
import WfIcon from './modules/wf-icon.vue';
import BoardShell, {type QaBridge} from './modules/board-shell.vue';
import {fmtRel} from './modules/fmt-rel';

const QAGlass = defineAsyncComponent(() => import('@/views/ai/qa-glass/index.vue'));

const route = useRoute();
const router = useRouter();

// ── 状态 ──────────────────────────────────────────────────────────────────
const workflowKey = ref(typeof route.query.wk === 'string' ? route.query.wk : '');
const sessionKey = ref(typeof route.query.sid === 'string' ? route.query.sid : '');
const shellRef = ref<InstanceType<typeof BoardShell> | null>(null);
// 画板选中态经壳 expose 取出，随嵌入对话发给 Agent（原 selectedNodeIds 同款语义）
const shellSelectedIds = computed<string[]>(() => shellRef.value?.selectedNodeIds || []);
// 附件全屏预览（页面级挂载：节点缩略图 / 悬浮窗内 qa-glass 共用）；壳内 previewAttachment 经 emit 上报到这里
const previewAtt = ref<{name: string; src: string} | null>(null);

// 工作流列表（无 wk 时显示）
const wfList = ref<WorkflowListItem[]>([]);
const showList = ref(false);
const keyword = ref('');
const listLoading = ref(false);
// 任务库类型筛选：全部 / 流程编排 / 应用制作（列表项带 boardType，本地过滤即输即得）
const libTab = ref<'all' | 'board' | 'html'>('all');
const filteredWfList = computed(() => {
  if (libTab.value === 'all') return wfList.value;
  return wfList.value.filter(w => (w.boardType || 'board') === libTab.value);
});

// 对话悬浮窗（首次打开才挂载，之后 v-show 保持挂载 → 关闭再打开保留会话状态）
const chatOpen = ref(false);
const chatMounted = ref(false);
const qaRef = ref<any>(null);
const sessOpen = ref(false);

// 悬浮窗位置与拖拽
const floatEl = ref<HTMLElement | null>(null);
const floatPos = ref({x: 0, y: 0});
const dragging = ref(false);
const floatInited = ref(false);
let dragOffset = {x: 0, y: 0};
let dragPageRect: DOMRect | null = null;

async function loadList() {
  listLoading.value = true;
  const {data} = await fetchListWorkflows(keyword.value.trim() || undefined);
  wfList.value = data || [];
  listLoading.value = false;
}

let searchTimer: ReturnType<typeof setTimeout> | null = null;
function onSearch() {
  if (searchTimer) clearTimeout(searchTimer);
  searchTimer = setTimeout(loadList, 300);
}

// ── 应用制作列表卡个性化预览 ──────────────────────────────────────────────
// 预览素材（标题/标语/主题色）由后端发布时从 index.html 提取（htmlPreview）；
// 素材缺省时按 workflowKey 哈希派生柱状图高度与主题色——每板长相不同且确定性稳定（刷新列表不闪变）
function wfSeed(key: string): number {
  let h = 2166136261;
  for (let i = 0; i < key.length; i++) {
    h ^= key.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}
function appPreviewBars(item: WorkflowListItem): number[] {
  const s = wfSeed(item.workflowKey);
  return [7, 13, 29, 37].map(k => 32 + ((Math.imul(s, k) >>> 9) % 64));
}
function appPreviewStyle(item: WorkflowListItem): Record<string, string> {
  const accent = item.htmlPreview?.accent || `hsl(${wfSeed(item.workflowKey) % 360} 72% 50%)`;
  return {'--pv-accent': accent};
}
/** 浏览器壳右侧标签：应用真实标题（与卡片标题不同才有信息量）优先，否则统一标识 */
function appTagOf(item: WorkflowListItem): string {
  const t = String(item.htmlPreview?.title || '').trim();
  if (t && t !== String(item.title || '').trim()) return `✦ ${t}`;
  return '✦ 由 Agent 开发';
}

// ── 新手引导：一键示例 ────────────────────────────────────────────────────
// 「不知道怎么用」的最短解法不是说明书，而是给一句能直接发的话——点击示例句 = 建板 + 把它发给 Agent，
// 用户立刻亲眼看到「Agent 把任务拆成卡片落到板上」的完整过程，一遍就会。
// 示例句是可直发的具体任务（覆盖两种板型），不写抽象描述。
const LIB_EXAMPLES: {type: 'board' | 'html'; prompt: string}[] = [
  {type: 'board', prompt: '帮我规划一次标准审查会的准备工作，把步骤拆解放到板上，每步列清需要准备的材料。'},
  {type: 'board', prompt: '我想把两个相近标准做一遍指标对比，帮我拆解对比步骤，最后给出对比结论。'},
  {type: 'html', prompt: '帮我制作一份电动自行车标准族谱 · 交互式研究报告'}
];

// ── 对话悬浮窗 ────────────────────────────────────────────────────────────
function openChatPanel() {
  chatOpen.value = true;
  chatMounted.value = true;
  sessOpen.value = false;
  nextTick(initFloatPos);
}

function toggleChat() {
  if (chatOpen.value) chatOpen.value = false;
  else openChatPanel();
}

function initFloatPos() {
  if (floatInited.value) return;
  const page = floatEl.value?.parentElement;
  const pw = page?.clientWidth || window.innerWidth;
  const ph = page?.clientHeight || window.innerHeight;
  const w = floatEl.value?.offsetWidth || 440;
  const h = floatEl.value?.offsetHeight || 600;
  floatPos.value = {
    x: Math.max(12, pw - w - 24),
    y: Math.min(Math.max(64, 68), Math.max(12, ph - h - 12))
  };
  floatInited.value = true;
}

function onFloatPointerDown(e: PointerEvent) {
  // 点在按钮（会话选择/新建/关闭）上时不拖拽
  if ((e.target as HTMLElement).closest('button')) return;
  const page = floatEl.value?.parentElement;
  if (!page) return;
  dragPageRect = page.getBoundingClientRect();
  dragging.value = true;
  dragOffset = {
    x: e.clientX - dragPageRect.left - floatPos.value.x,
    y: e.clientY - dragPageRect.top - floatPos.value.y
  };
  window.addEventListener('pointermove', onFloatPointerMove);
  window.addEventListener('pointerup', onFloatPointerUp);
}

function onFloatPointerMove(e: PointerEvent) {
  if (!dragging.value || !dragPageRect) return;
  const w = floatEl.value?.offsetWidth || 440;
  const h = floatEl.value?.offsetHeight || 600;
  const maxX = Math.max(8, dragPageRect.width - w - 8);
  const maxY = Math.max(8, dragPageRect.height - h - 8);
  floatPos.value = {
    x: Math.min(Math.max(8, e.clientX - dragPageRect.left - dragOffset.x), maxX),
    y: Math.min(Math.max(8, e.clientY - dragPageRect.top - dragOffset.y), maxY)
  };
}

function onFloatPointerUp() {
  dragging.value = false;
  dragPageRect = null;
  window.removeEventListener('pointermove', onFloatPointerMove);
  window.removeEventListener('pointerup', onFloatPointerUp);
}

// QAGlass 暴露的会话状态与方法
const qaSessions = computed<AgentSession[]>(() => qaRef.value?.sessions ?? []);
const qaCurrentKey = computed<string>(() => qaRef.value?.currentSessionKey ?? '');

const sessList = computed(() => [...qaSessions.value].sort((a, b) => b.updatedAt - a.updatedAt).slice(0, 40));

const currentSessTitle = computed(() => {
  const k = qaCurrentKey.value;
  if (!k) return '新对话';
  return qaSessions.value.find(s => s.sessionKey === k)?.title || '新对话';
});

function newSession() {
  qaRef.value?.startNewSession();
  sessOpen.value = false;
}

function toggleSessMenu() {
  sessOpen.value = !sessOpen.value;
  if (sessOpen.value) qaRef.value?.reloadSessions();
}

function pickSession(key: string) {
  qaRef.value?.loadSession(key);
  sessOpen.value = false;
}

// ── 建板 / 开板（壳只负责加载渲染，建板与 route 读写归宿主） ─────────────
// 默认空白板：不预置开始/结束节点。骨架由 agent 播种（第一纪律）或用户手建，
// start/end 仅确有执行流程时才该出现，不该成为新建板的仪式。
// 品牌专属板型（如分镜段卡）不改变建板方式——就是普通板子，品牌卡由 agent 播种或从添加节点菜单加入
async function createNew(type: 'board' | 'html' = 'board'): Promise<boolean> {
  const {data, error} = await fetchCreateWorkflow({
    title: type === 'html' ? '新应用制作' : '新流程编排',
    nodes: [],
    edges: [],
    boardType: type
  });
  if (error || !data) {
    window.$message?.error('创建失败');
    return false;
  }
  workflowKey.value = data.workflowKey;
  router.replace({query: {...route.query, wk: data.workflowKey}});
  showList.value = false;
  return true;
}

async function openExisting(wk: string) {
  workflowKey.value = wk;
  router.replace({query: {...route.query, wk}});
  showList.value = false;
}

async function doDelete(item: WorkflowListItem) {
  await fetchDeleteWorkflow(item.workflowKey);
  await loadList();
  window.$message?.success?.('已删除');
}

async function goBack() {
  if (workflowKey.value) {
    // 先冲刷保存：未保存的编辑立即落盘，不阻塞返回；空任务不落盘（马上删）
    await shellRef.value?.flushSave();
    // 空任务即时清理：board 型板上没有任何卡片、html 型从未发布过 index.html 就软删（零信息损失，标题可随时重起）。
    // 软删安全：后端写入端点全走 _get_owned（只查 is_deleted=0），挂起的保存只会 4004，板子不会复活。
    // loading 中不删：此时状态为空只代表还没加载完，不代表任务真空
    const emptyWk = shellRef.value && !shellRef.value.loading && shellRef.value.isEmpty() ? workflowKey.value : '';
    workflowKey.value = '';
    sessionKey.value = '';
    chatOpen.value = false;
    sessOpen.value = false;
    router.replace({query: {}});
    showList.value = true;
    // 空板先删、删完再拉列表：避免「板已删但列表慢一步还能看到，点进去报 4004」的时序错位
    if (emptyWk) {
      listLoading.value = true; // 删除请求期间列表先转圈，不闪现含已删板的旧数据
      const {error} = await fetchDeleteWorkflow(emptyWk);
      if (!error) window.$message?.info?.('空板已自动清理');
    }
    await loadList();
  } else {
    router.push({name: 'ai_qa-glass'});
  }
}

// ── 壳事件：页面宿主的应答（route / 对话窗都在宿主手里） ──────────────────
function onShellNotFound(_wk: string) {
  workflowKey.value = '';
  sessionKey.value = '';
  router.replace({query: {}});
  showList.value = true;
  void loadList();
  window.$message?.error('流程编排已不存在（空板会被自动清理）');
}
function onShellRequestChat() {
  openChatPanel();
}
function onShellToggleChat() {
  toggleChat();
}
function onShellFullscreenEnter() {
  chatOpen.value = false;
  sessOpen.value = false;
}
function onShellBoardSwitched(wk: string) {
  void openExisting(wk);
}
async function onShellCreateRequested(type: 'board' | 'html') {
  await createNew(type);
}

// ── 对话桥：把 qaRef 适配成壳消费的 QaBridge（就绪等待收编在这里） ────────
async function waitQaReady(): Promise<boolean> {
  const start = Date.now();
  while (typeof qaRef.value?.sendMessage !== 'function' && Date.now() - start < 3000) {
    await new Promise(r => setTimeout(r, 60));
  }
  return typeof qaRef.value?.sendMessage === 'function';
}
const qaBridge: QaBridge = {
  running: computed(() => !!qaRef.value?.running),
  sendMessage: async (t: string) => {
    if (!chatOpen.value) openChatPanel();
    if (!(await waitQaReady())) return false;
    qaRef.value.sendMessage(t);
    return true;
  },
  currentSessionKey: () => qaRef.value?.currentSessionKey || ''
};

/** 一键示例（列表空态）：新建板 → 示例句直接发给 Agent */
async function tryExample(type: 'board' | 'html', prompt: string) {
  const ok = await createNew(type);
  if (!ok) return;
  if (await waitQaReady()) qaRef.value.sendMessage(prompt);
}

// ── 生命周期 ──────────────────────────────────────────────────────────────
onMounted(async () => {
  if (workflowKey.value) {
    showList.value = false;
  } else {
    showList.value = true;
    await loadList();
  }
});

onBeforeUnmount(() => {
  // 空任务即时清理兜底：不经返回按钮、直接切走路由离开本页时，空任务同样不留（goBack 路径已带提示，这里静默）
  const sh = shellRef.value;
  if (workflowKey.value && sh && !sh.loading && sh.isEmpty()) {
    void fetchDeleteWorkflow(workflowKey.value);
  }
  window.removeEventListener('pointermove', onFloatPointerMove);
  window.removeEventListener('pointerup', onFloatPointerUp);
});

</script>

<template>
  <div class="wf-page">
    <!-- aurora 静态光球（同 nian 背景语言；画布页透明度更低，不干扰节点阅读。静态 = 零合成成本） -->
    <div class="wf-aurora" aria-hidden="true">
      <i class="orb o1" /><i class="orb o2" /><i class="orb o3" />
    </div>
    <!-- 画板成品组件：任务库经默认插槽挂在顶栏与画布之间（原 .wf-lib 位置） -->
    <BoardShell
      ref="shellRef"
      :workflow-key="workflowKey || null"
      :qa-bridge="qaBridge"
      :chat-active="chatOpen"
      :lib-shown="showList"
      @not-found="onShellNotFound"
      @board-switched="onShellBoardSwitched"
      @create-requested="onShellCreateRequested"
      @back="goBack"
      @request-chat="onShellRequestChat"
      @toggle-chat="onShellToggleChat"
      @preview-attachment="att => (previewAtt = att)"
      @fullscreen-enter="onShellFullscreenEnter"
    >
    <div v-if="showList" class="wf-lib">
      <div class="wf-lib-inner">
        <div class="wf-lib-head">
          <div class="wf-lib-heading">
            <span class="wf-lib-overline">Flow Orchestration</span>
            <h2 class="wf-lib-title">深度任务库</h2>
            <p class="wf-lib-sub">
              共 <b>{{ filteredWfList.length }}</b> 个深度任务
              <template v-if="keyword.trim()">（搜索「{{ keyword.trim() }}」）</template>
              · 由你和 Agent 共同维护
            </p>
          </div>
          <div class="wf-lib-actions">
            <label class="wf-lib-search">
              <svg viewBox="0 0 20 20" fill="currentColor" width="15" height="15"><path fill-rule="evenodd" d="M9 3.5a5.5 5.5 0 100 11 5.5 5.5 0 000-11zM2 9a7 7 0 1112.452 4.391l3.328 3.329a.75.75 0 11-1.06 1.06l-3.329-3.328A7 7 0 012 9z" clip-rule="evenodd" /></svg>
              <input v-model="keyword" placeholder="搜索流程编排…" spellcheck="false" @input="onSearch" />
            </label>
            <button class="wf-lib-create" @click="createNew('board')"><WfIcon name="plus" :size="12" />新建流程编排</button>
            <button class="wf-lib-create ghost" title="Agent 像开发者一样为你构建可交互的应用" @click="createNew('html')">
              <WfIcon name="plus" :size="12" />新建应用制作
            </button>
          </div>
        </div>

        <!-- 类型筛选：全部 / 流程编排 / 应用制作 -->
        <div class="wf-lib-tabs">
          <button class="wf-lib-tab" :class="{active: libTab === 'all'}" @click="libTab = 'all'">全部</button>
          <button class="wf-lib-tab" :class="{active: libTab === 'board'}" @click="libTab = 'board'">流程编排</button>
          <button class="wf-lib-tab" :class="{active: libTab === 'html'}" @click="libTab = 'html'">应用制作</button>
        </div>

        <!-- 加载中 -->
        <div v-if="listLoading" class="wf-lib-state">
          <span class="wf-dot" /><span class="wf-dot" /><span class="wf-dot" />
        </div>

        <!-- 空状态 -->
        <div v-else-if="!filteredWfList.length" class="wf-lib-state">
          <div class="wf-empty">
            <svg class="wf-empty-art" viewBox="0 0 168 74" fill="none">
              <rect x="2" y="26" width="44" height="22" rx="7" stroke="rgba(30,64,175,0.35)" stroke-width="1.5" />
              <circle cx="12" cy="37" r="3" fill="#94a3b8" />
              <rect x="62" y="8" width="44" height="22" rx="7" stroke="rgba(30,64,175,0.35)" stroke-width="1.5" />
              <circle cx="72" cy="19" r="3" fill="#d97706" />
              <rect x="62" y="44" width="44" height="22" rx="7" stroke="rgba(30,64,175,0.35)" stroke-width="1.5" />
              <circle cx="72" cy="55" r="3" fill="#059669" />
              <rect x="122" y="26" width="44" height="22" rx="7" stroke="rgba(30,64,175,0.55)" stroke-width="1.5" stroke-dasharray="4 3" />
              <path d="M46 33 L62 21 M46 41 L62 53 M106 19 L122 33 M106 55 L122 41" stroke="rgba(30,64,175,0.28)" stroke-width="1.5" />
            </svg>
            <p class="wf-empty-title">{{ keyword.trim() ? '没有匹配的流程编排' : '还没有流程编排' }}</p>
            <p class="wf-empty-sub">{{ keyword.trim() ? '换个关键词试试，或直接新建一个' : '把一个任务交给 Agent——它会被拆解成卡片持续演进，进展一目了然' }}</p>
            <template v-if="!keyword.trim()">
              <div class="wf-empty-actions">
                <button class="wf-lib-create" @click="createNew('board')"><WfIcon name="plus" :size="12" />新建流程编排</button>
                <button class="wf-lib-create ghost" @click="createNew('html')"><WfIcon name="plus" :size="12" />新建应用制作</button>
              </div>
              <!--
                新手引导：三步用法 + 一键示例。示例句点击即「建板 + 发给 Agent」，
                让用户亲眼看到 Agent 把任务拆解落板的过程——比说明书更直接
              -->
              <div class="wf-empty-guide">
                <div class="wf-guide-steps">
                  <span class="wf-guide-step"><b>1</b>把想做的事说给 Agent（或先建板再说）</span>
                  <span class="wf-guide-step"><b>2</b>Agent 自动拆解成卡片与连线，进展一目了然</span>
                  <span class="wf-guide-step"><b>3</b>板上对话与手工编辑并行，成果沉淀在板上不丢失</span>
                </div>
                <p class="wf-guide-try">不知道从哪开始？点一句试试（自动建任务并发给 Agent）：</p>
                <div class="wf-guide-examples">
                  <button v-for="ex in LIB_EXAMPLES" :key="ex.prompt" class="wf-guide-example" @click="tryExample(ex.type, ex.prompt)">
                    <em class="wf-guide-ex-type">{{ ex.type === 'html' ? '应用制作' : '流程编排' }}</em>{{ ex.prompt }}
                  </button>
                </div>
              </div>
            </template>
          </div>
        </div>

        <!-- 卡片网格 -->
        <div v-else class="wf-lib-grid">
          <div
            v-for="(item, i) in filteredWfList"
            :key="item.workflowKey"
            class="wf-card"
            :style="{'--i': i}"
            @click="openExisting(item.workflowKey)"
          >
            <div class="wf-card-top">
              <h3 class="wf-card-name">{{ item.title }}</h3>
              <span class="wf-card-type" :class="item.boardType === 'html' ? 't-html' : 't-board'">{{ item.boardType === 'html' ? '应' : '流' }}</span>
              <NPopconfirm
                placement="top"
                positive-text="删除"
                negative-text="取消"
                :positive-button-props="{type: 'error'}"
                @positive-click="doDelete(item)"
              >
                <template #trigger>
                  <button class="wf-card-del" title="删除流程编排" @click.stop>×</button>
                </template>
                确定删除「{{ item.title }}」吗？删除后无法恢复。
              </NPopconfirm>
            </div>

            <!-- 流程编排：迷你节点链预览。空间不足整组换行，不强行压缩胶囊；
                 长文本在胶囊内以真省略号截断，悬浮可见完整标签 -->
            <div v-if="item.boardType !== 'html'" class="wf-card-flow">
              <span v-for="(p, pi) in item.preview || []" :key="pi" class="wf-flow-step">
                <span v-if="pi > 0" class="wf-flow-arrow">→</span>
                <span class="wf-flow-pill" :class="`tp-${p.type || 'textNode'}`" :title="p.label">
                  <i class="wf-pill-dot" /><span class="wf-pill-text">{{ p.label }}</span>
                </span>
              </span>
              <span v-if="!item.preview?.length" class="wf-flow-none">暂无节点</span>
              <span v-else-if="(item.nodeCount || 0) > (item.preview || []).length" class="wf-flow-more">
                +{{ (item.nodeCount || 0) - (item.preview || []).length }}
              </span>
            </div>
            <!--
              应用制作：内容是一个可运行的应用，与节点链无关——用「应用窗口」形态预览，与进入看板后的画布占位画呼应。
              预览素材由后端发布时从 index.html 提取（htmlPreview）：真实标语替代骨架行、应用主题色 + 按板派生的
              柱状图高度替代清一色占位图，每张卡各有辨识；素材缺省（未发布/旧数据）退回骨架行 + 派生配色。
              入口就绪与否由后端 entryReady 决定，列表项不带，进任务后画布自行呈现
            -->
            <div v-else class="wf-app-preview" :style="appPreviewStyle(item)" title="由 Agent 开发的可交互应用，画布内直接运行">
              <div class="wf-app-chrome">
                <i /><i /><i />
                <span class="wf-app-tag" :title="appTagOf(item)">{{ appTagOf(item) }}</span>
              </div>
              <div class="wf-app-body">
                <div class="wf-app-lines">
                  <span v-if="item.htmlPreview?.tagline" class="wf-app-tagline">{{ item.htmlPreview.tagline }}</span>
                  <template v-else><i class="l1" /><i class="l2" /></template>
                </div>
                <div class="wf-app-bars">
                  <i v-for="(h, bi) in appPreviewBars(item)" :key="bi" :style="{'--h': `${h}%`}" />
                </div>
              </div>
            </div>

            <div class="wf-card-meta">
              <span class="wf-card-stats">
                {{ item.boardType === 'html' ? `应用制作 · ${item.version || 0} 次编辑` : `${item.nodeCount || 0} 节点 · ${item.edgeCount || 0} 连线 · ${item.version} 次编辑` }}
              </span>
              <span class="wf-card-time">{{ fmtRel(item.updateTime) }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
    </BoardShell>

    <!-- QA 对话悬浮窗（可拖动、不阻塞画布操作） -->
    <Transition name="wf-float-fade">
      <div
        v-if="chatMounted"
        v-show="chatOpen"
        ref="floatEl"
        class="wf-float"
        :style="{transform: `translate(${floatPos.x}px, ${floatPos.y}px)`}"
      >
        <div class="wf-float-card" :class="{dragging: dragging}">
          <!-- 头部：拖拽把手 + 会话切换 + 关闭 -->
          <div class="wf-float-head" @pointerdown="onFloatPointerDown">
            <span class="wf-float-grip" title="拖动窗口">⠿</span>
            <button class="wf-sess-btn" :title="currentSessTitle" @click="toggleSessMenu">
              <WfIcon class="wf-sess-ic" name="chat" :size="13" />
              <span class="wf-sess-name">{{ currentSessTitle }}</span>
              <WfIcon class="wf-sess-caret" :class="{up: sessOpen}" name="chevron" :size="12" />
            </button>
            <button class="wf-new-sess" title="新建对话" @click="newSession">+</button>
            <button class="wf-float-close" title="收起" @click="chatOpen = false">—</button>

            <!-- 会话下拉（历史会话 + 新建） -->
            <div v-if="sessOpen" class="wf-sess-backdrop" @click="sessOpen = false" />
            <Transition name="wf-menu">
              <div v-if="sessOpen" class="wf-sess-menu">
                <button class="wf-sess-new" @click="newSession">+ 新建对话</button>
                <div class="wf-sess-divider" />
                <div class="wf-sess-list">
                  <button
                    v-for="s in sessList"
                    :key="s.sessionKey"
                    class="wf-sess-item"
                    :class="{cur: s.sessionKey === qaCurrentKey}"
                    @click="pickSession(s.sessionKey)"
                  >
                    <span class="wf-sess-item-name">{{ s.title || '新对话' }}</span>
                    <em v-if="s.source === 'workflow'" class="wf-sess-src">画板</em>
                    <span v-if="s.sessionKey === qaCurrentKey" class="wf-sess-cur">●</span>
                  </button>
                  <div v-if="!sessList.length" class="wf-sess-empty">暂无历史会话</div>
                </div>
              </div>
            </Transition>
          </div>

          <!-- 对话主体 -->
          <div class="wf-float-body">
            <Suspense>
              <QAGlass
                ref="qaRef"
                :embedded="true"
                :hide-topbar="true"
                :session-key="sessionKey || undefined"
                :workflow-key="workflowKey || undefined"
                :selected-node-ids="shellSelectedIds"
              />
              <template #fallback>
                <div class="wf-float-loading">
                  <span class="wf-dot" /><span class="wf-dot" /><span class="wf-dot" />
                </div>
              </template>
            </Suspense>
          </div>
        </div>
      </div>
    </Transition>

    <!-- 附件全屏预览（页面级挂载：节点缩略图 / 悬浮窗内 qa-glass 共用；组件内 Teleport 到 body，不受画布 transform 影响） -->
    <AttachmentPreviewModal :att="previewAtt" @close="previewAtt = null" />
  </div>
</template>

<style scoped>
.wf-page {
  /* ── 设计令牌：与 nian 页同源（QA 蓝玻璃风） ── */
  --bg: #f5f7fb;
  --surface: rgba(255, 255, 255, 0.42);
  --surface-strong: rgba(255, 255, 255, 0.66);
  --highlight: rgba(255, 255, 255, 0.9);
  --border: rgba(30, 64, 175, 0.1);
  --border-strong: rgba(30, 64, 175, 0.18);
  --border-glow: rgba(30, 64, 175, 0.28);
  --ink: #0f172a;
  --ink-soft: #334155;
  --ink-mute: #64748b;
  --ink-faint: #94a3b8;
  --c-blue: #1e40af;
  --c-blue-2: #2563eb;
  --c-sky: #0ea5e9;
  --c-cyan: #0891b2;
  --aurora: linear-gradient(110deg, #1e40af 0%, #2563eb 35%, #0ea5e9 70%, #0891b2 100%);
  --shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.04), 0 8px 24px -12px rgba(30, 64, 175, 0.16);
  --shadow-md: 0 1px 2px rgba(15, 23, 42, 0.05), 0 12px 32px -12px rgba(30, 64, 175, 0.22);
  --shadow-glow: 0 8px 32px -10px rgba(30, 64, 175, 0.45);
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: var(--bg);
  overflow: hidden;
  position: relative;
  font-family: 'Plus Jakarta Sans', system-ui, -apple-system, sans-serif;
  color: var(--ink);
  -webkit-font-smoothing: antialiased;
}
.wf-aurora {
  position: absolute;
  inset: 0;
  z-index: 0;
  pointer-events: none;
  overflow: hidden;
}
.wf-aurora .orb {
  position: absolute;
  border-radius: 50%;
  filter: blur(120px);
}
.wf-aurora .o1 {
  width: 640px;
  height: 640px;
  top: -220px;
  right: -140px;
  background: radial-gradient(circle, var(--c-blue) 0%, transparent 65%);
  opacity: 0.14;
}
.wf-aurora .o2 {
  width: 700px;
  height: 700px;
  bottom: -280px;
  left: -180px;
  background: radial-gradient(circle, var(--c-cyan) 0%, transparent 65%);
  opacity: 0.13;
}
.wf-aurora .o3 {
  width: 460px;
  height: 460px;
  top: 28%;
  left: 36%;
  background: radial-gradient(circle, var(--c-sky) 0%, transparent 65%);
  opacity: 0.09;
}
.wf-dots {
  position: absolute;
  left: -50%;
  top: -50%;
  width: 200%;
  height: 200%;
  background-image: radial-gradient(rgba(30, 64, 175, 0.09) 1px, transparent 1.1px);
  pointer-events: none;
  will-change: transform;
}
.wf-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #94a3b8;
  animation: wf-pulse 1.2s ease-in-out infinite;
}
.wf-dot:nth-child(2) { animation-delay: 0.15s; }
.wf-dot:nth-child(3) { animation-delay: 0.3s; }
.wf-lib {
  flex: 1;
  position: relative;
  z-index: 1;
  overflow-y: auto;
  /* 恒定预留滚动条位：切换类型 tab 时卡片数量增减导致滚动条时有时无，
     不加这个内容宽度会跟着变（居中列整体左右位移 + 网格列数跳变）→ 页面左右抖动 */
  scrollbar-gutter: stable;
  background: radial-gradient(rgba(30, 64, 175, 0.05) 1px, transparent 1px);
  background-size: 22px 22px;
}
.wf-lib-inner {
  max-width: 980px;
  margin: 0 auto;
  padding: 36px 28px 56px;
}
.wf-lib-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 26px;
  flex-wrap: wrap;
}
.wf-lib-overline {
  display: inline-block;
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--c-blue);
  opacity: 0.8;
  margin-bottom: 6px;
}
.wf-lib-title {
  margin: 0;
  font-size: 30px;
  font-weight: 800;
  color: var(--ink);
  letter-spacing: -0.02em;
  line-height: 1.2;
}
.wf-lib-sub {
  margin: 7px 0 0;
  font-size: 13px;
  color: var(--ink-mute);
}
.wf-lib-sub b {
  color: var(--c-blue);
  font-weight: 700;
}
.wf-lib-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}
.wf-lib-search {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 8px 13px;
  background: var(--surface-strong);
  border: 1px solid var(--border);
  border-radius: 11px;
  color: var(--ink-faint);
  box-shadow: inset 0 1px 0 var(--highlight);
  transition: all 0.18s;
}
.wf-lib-search:focus-within {
  border-color: var(--c-blue);
  box-shadow: inset 0 1px 0 var(--highlight), 0 0 0 3px rgba(30, 64, 175, 0.08);
  color: var(--c-blue);
}
.wf-lib-search input {
  border: none;
  background: transparent;
  outline: none;
  width: 170px;
  font-family: inherit;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}
.wf-lib-search input::placeholder {
  color: var(--ink-faint);
  font-weight: 500;
}
.wf-lib-create {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 9px 20px;
  border: 1px solid transparent;
  background: var(--aurora);
  color: #fff;
  border-radius: 11px;
  font-family: inherit;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.005em;
  cursor: pointer;
  transition: all 0.18s;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.4), 0 4px 14px -2px rgba(30, 64, 175, 0.45);
  white-space: nowrap;
}
.wf-lib-create:hover {
  transform: translateY(-1px);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.5), 0 6px 20px -2px rgba(30, 64, 175, 0.55);
}
.wf-lib-create.ghost {
  background: var(--surface-strong);
  border-color: var(--border);
  color: var(--c-blue);
  box-shadow: inset 0 1px 0 var(--highlight);
}
.wf-lib-create.ghost:hover {
  border-color: rgba(30, 64, 175, 0.4);
  box-shadow: inset 0 1px 0 var(--highlight), 0 0 0 3px rgba(30, 64, 175, 0.08);
}
.wf-empty-actions {
  display: flex;
  gap: 10px;
}
.wf-lib-tabs {
  display: inline-flex;
  gap: 2px;
  padding: 3px;
  margin-bottom: 18px;
  background: var(--surface-strong);
  border: 1px solid var(--border);
  border-radius: 11px;
  box-shadow: inset 0 1px 0 var(--highlight);
}
.wf-lib-tab {
  padding: 6px 16px;
  border: none;
  background: transparent;
  border-radius: 8px;
  font-family: inherit;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ink-mute);
  cursor: pointer;
  transition: all 0.18s;
}
.wf-lib-tab:hover {
  color: var(--ink);
}
.wf-lib-tab.active {
  background: var(--aurora);
  color: #fff;
  box-shadow: 0 2px 8px -2px rgba(30, 64, 175, 0.4);
}
.wf-lib-state {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 70px 0;
  gap: 6px;
}
.wf-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
}
.wf-empty-art {
  width: 168px;
  height: 74px;
  margin-bottom: 20px;
  opacity: 0.9;
}
.wf-empty-title {
  margin: 0 0 6px;
  font-size: 15px;
  font-weight: 700;
  color: var(--ink-soft);
}
.wf-empty-sub {
  margin: 0 0 20px;
  font-size: 13px;
  color: var(--ink-faint);
}
.wf-empty-guide {
  max-width: 560px;
  margin-top: 30px;
  padding: 16px 20px 14px;
  text-align: left;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  box-shadow: inset 0 1px 0 var(--highlight), var(--shadow-sm);
}
.wf-guide-steps {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.wf-guide-step {
  display: flex;
  align-items: center;
  gap: 9px;
  font-size: 12.5px;
  font-weight: 500;
  color: var(--ink-soft);
}
.wf-guide-step b {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 17px;
  height: 17px;
  border-radius: 50%;
  background: rgba(30, 64, 175, 0.09);
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  font-weight: 700;
  color: var(--c-blue);
}
.wf-guide-try {
  margin: 13px 0 8px;
  font-size: 11.5px;
  color: var(--ink-faint);
}
.wf-guide-examples {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.wf-guide-example {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.7);
  font-size: 12.5px;
  font-weight: 500;
  color: var(--ink-soft);
  text-align: left;
  cursor: pointer;
  transition: all 0.18s;
}
.wf-guide-example:hover {
  border-color: var(--border-glow);
  background: var(--surface-strong);
  color: var(--ink);
  box-shadow: 0 4px 14px -6px rgba(30, 64, 175, 0.3);
}
.wf-guide-ex-type {
  flex-shrink: 0;
  padding: 1px 7px;
  border-radius: 999px;
  background: rgba(30, 64, 175, 0.08);
  font-style: normal;
  font-family: 'JetBrains Mono', monospace;
  font-size: 9.5px;
  font-weight: 700;
  letter-spacing: 0.03em;
  color: var(--c-blue);
}
.wf-lib-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
}
.wf-card {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px 16px 13px;
  background: rgba(255, 255, 255, 0.72);
  border: 1px solid var(--border);
  border-radius: 16px;
  box-shadow: inset 0 1px 0 var(--highlight), var(--shadow-sm);
  cursor: pointer;
  transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease;
  animation: wf-card-in 0.4s cubic-bezier(0.16, 1, 0.3, 1) both;
  animation-delay: calc(min(var(--i), 12) * 45ms);
}
@keyframes wf-card-in {
  from { opacity: 0; transform: translateY(14px); }
  to { opacity: 1; transform: translateY(0); }
}
.wf-card:hover {
  transform: translateY(-3px);
  border-color: var(--border-glow);
  box-shadow: inset 0 1px 0 var(--highlight), 0 1px 2px rgba(15, 23, 42, 0.05), 0 24px 48px -16px rgba(30, 64, 175, 0.24);
}
.wf-card-top {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}
.wf-card-name {
  flex: 1;
  min-width: 0;
  margin: 0;
  font-size: 15px;
  font-weight: 700;
  letter-spacing: -0.01em;
  color: var(--ink);
  line-height: 1.45;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.wf-card:hover .wf-card-name {
  color: var(--c-blue);
}
.wf-card-del {
  flex-shrink: 0;
  width: 26px;
  height: 26px;
  border: none;
  background: transparent;
  border-radius: 8px;
  color: #cbd5e1;
  font-size: 17px;
  line-height: 1;
  cursor: pointer;
  opacity: 0;
  transition: all 0.15s;
}
.wf-card:hover .wf-card-del {
  opacity: 1;
}
.wf-card-del:hover {
  background: rgba(239, 68, 68, 0.09);
  color: #ef4444;
}
.wf-card-type {
  flex-shrink: 0;
  margin-top: 2px;
  padding: 1px 8px;
  border-radius: 999px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.04em;
  line-height: 1.7;
}
.wf-card-type.t-board {
  background: rgba(148, 163, 184, 0.14);
  color: var(--ink-mute);
}
.wf-card-type.t-html {
  background: rgba(30, 64, 175, 0.08);
  color: var(--c-blue);
}
.wf-card-flow {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 5px 4px;
  min-height: 24px;
}
.wf-flow-step {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}
.wf-flow-pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
  /* 上限按「最窄卡片(248px)也能并排两个满宽胶囊 + 箭头」校准：108 + 4 + (10+4+108) = 234 */
  max-width: 108px;
  padding: 3px 9px;
  border-radius: 999px;
  border: 1px solid rgba(148, 163, 184, 0.3);
  background: rgba(255, 255, 255, 0.85);
  font-size: 11px;
  font-weight: 600;
  color: var(--ink-mute);
}
.wf-pill-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.wf-pill-dot {
  flex-shrink: 0;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #94a3b8;
}
.wf-flow-pill.tp-startNode {
  border-color: rgba(30, 64, 175, 0.28);
  background: rgba(30, 64, 175, 0.05);
  color: #1e40af;
}
.wf-flow-pill.tp-startNode .wf-pill-dot {
  background: linear-gradient(110deg, #1e40af, #0ea5e9);
}
.wf-flow-pill.tp-endNode {
  border-color: rgba(16, 185, 129, 0.3);
  background: rgba(16, 185, 129, 0.05);
  color: #047857;
}
.wf-flow-pill.tp-endNode .wf-pill-dot { background: #10b981; }
.wf-flow-pill.tp-fileNode {
  border-color: rgba(124, 58, 237, 0.3);
  background: rgba(124, 58, 237, 0.05);
  color: #6d28d9;
}
.wf-flow-pill.tp-fileNode .wf-pill-dot { background: #7c3aed; }
.wf-flow-pill.tp-reviewNode {
  border-color: rgba(217, 119, 6, 0.32);
  background: rgba(217, 119, 6, 0.06);
  color: #b45309;
}
.wf-flow-pill.tp-reviewNode .wf-pill-dot { background: #d97706; }
.wf-flow-pill.tp-taskNode {
  border-color: rgba(37, 99, 235, 0.3);
  background: rgba(37, 99, 235, 0.05);
  color: #1d4ed8;
}
.wf-flow-pill.tp-taskNode .wf-pill-dot { background: #2563eb; }
.wf-flow-pill.tp-dataNode {
  border-color: rgba(8, 145, 178, 0.32);
  background: rgba(8, 145, 178, 0.05);
  color: #155e75;
}
.wf-flow-pill.tp-dataNode .wf-pill-dot { background: #0891b2; }
.wf-flow-pill.tp-conclusionNode {
  border-color: rgba(30, 58, 138, 0.4);
  background: #0f172a;
  color: #e2e8f0;
}
.wf-flow-pill.tp-conclusionNode .wf-pill-dot { background: #38bdf8; }
.wf-flow-pill.tp-segNode {
  border-color: rgba(14, 116, 144, 0.32);
  background: rgba(14, 116, 144, 0.05);
  color: #155e75;
}
.wf-flow-pill.tp-segNode .wf-pill-dot { background: #0e7490; }
.wf-flow-arrow {
  flex-shrink: 0;
  font-size: 10px;
  color: #cbd5e1;
  line-height: 1;
}
.wf-flow-more {
  flex-shrink: 0;
  font-family: 'JetBrains Mono', monospace;
  font-size: 10.5px;
  font-weight: 600;
  color: var(--ink-faint);
  padding: 0 2px;
}
.wf-flow-none {
  flex-shrink: 0;
  font-size: 11.5px;
  color: #b6c2d4;
}
.wf-app-preview {
  border: 1px solid rgba(30, 64, 175, 0.14);
  border-radius: 10px;
  background: linear-gradient(160deg, rgba(30, 64, 175, 0.05), rgba(14, 165, 233, 0.07));
  overflow: hidden;
  transition: border-color 0.18s ease;
}
.wf-card:hover .wf-app-preview {
  border-color: rgba(30, 64, 175, 0.3);
}
.wf-app-chrome {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 9px;
  border-bottom: 1px solid rgba(30, 64, 175, 0.09);
  background: rgba(255, 255, 255, 0.55);
}
.wf-app-chrome i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: rgba(30, 64, 175, 0.28);
}
.wf-app-chrome i:nth-child(2) {
  background: rgba(30, 64, 175, 0.18);
}
.wf-app-chrome i:nth-child(3) {
  background: rgba(30, 64, 175, 0.1);
}
.wf-app-tag {
  margin-left: auto;
  max-width: 72%; /* 真实应用标题可能较长：单行省略，hover 由 title 给全文 */
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: 'JetBrains Mono', monospace;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.03em;
  color: var(--c-blue);
  opacity: 0.75;
}
.wf-app-body {
  display: flex;
  align-items: stretch;
  gap: 10px;
  padding: 9px 10px 10px;
}
.wf-app-lines {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 6px;
}
.wf-app-lines i {
  height: 6px;
  border-radius: 3px;
  background: rgba(30, 64, 175, 0.13);
}
.wf-app-lines .l1 {
  width: 88%;
}
.wf-app-lines .l2 {
  width: 62%;
  background: rgba(30, 64, 175, 0.09);
}
.wf-app-tagline {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  font-size: 10.5px;
  font-weight: 600;
  line-height: 1.55;
  color: var(--ink-soft);
}
.wf-app-bars {
  flex-shrink: 0;
  display: flex;
  align-items: flex-end;
  gap: 3px;
  height: 34px;
}
.wf-app-bars i {
  width: 7px;
  height: var(--h);
  border-radius: 2px;
  /* 主题色：发布时提取的应用主色（--pv-accent），缺省由前端按 workflowKey 派生——每板各不相同 */
  background: var(--pv-accent, #0ea5e9);
  opacity: 0.75;
  transition: opacity 0.18s ease;
}
.wf-card:hover .wf-app-bars i {
  opacity: 1;
}
.wf-card-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-top: auto; /* 预览行数不同时，同行卡片底部元信息依然对齐 */
  padding-top: 10px;
  border-top: 1px dashed rgba(30, 64, 175, 0.12);
  font-size: 11.5px;
  color: var(--ink-faint);
}
.wf-card-stats {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.wf-card-time {
  flex-shrink: 0;
  font-family: 'JetBrains Mono', monospace;
  font-size: 10.5px;
  color: #b6c2d4;
}
.tp-text { color: #64748b; }
.tp-work { color: #2563eb; }
.tp-data { color: #0891b2; }
.tp-conc { color: #1e3a8a; }
.tp-file { color: #7c3aed; }
.tp-review { color: #d97706; }
.tp-start { color: #2563eb; }
.tp-end { color: #0e7490; }

/* ── 切割补丁：共享/漏归规则 ── */
@keyframes wf-pulse {
  0%, 80%, 100% { opacity: 0.3; transform: scale(0.8); }
  40% { opacity: 1; transform: scale(1); }
}
@keyframes wf-pop {
  from {
    opacity: 0;
    transform: translateX(-50%) translateY(6px);
  }
  to {
    opacity: 1;
    transform: translateX(-50%) translateY(0);
  }
}
</style>

<style>
.wf-float {
  position: absolute;
  left: 0;
  top: 0;
  z-index: 120;
  width: min(440px, calc(100% - 24px));
  height: min(660px, calc(100% - 96px));
  min-height: 320px;
  will-change: transform;
  font-family: 'Plus Jakarta Sans', system-ui, -apple-system, sans-serif;
}
.wf-float-card {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  /* 悬浮窗整块叠在画布上：半透明 = 画布每帧重绘不能裁剪 + blur 重采样。
     2026-08-17 掉帧整治：去 backdrop-filter + 不透明底（实测不规则平移 25→60 的关键之一） */
  background: #fff;
  border: 1px solid rgba(30, 64, 175, 0.12);
  border-radius: 16px;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.9),
    0 1px 2px rgba(15, 23, 42, 0.04),
    0 16px 48px -12px rgba(30, 64, 175, 0.28);
  overflow: hidden;
  transition: box-shadow 0.18s ease;
  animation: wf-float-in 0.22s cubic-bezier(0.16, 1, 0.3, 1);
}
.wf-float-card.dragging {
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.9),
    0 24px 64px -12px rgba(30, 64, 175, 0.4),
    0 6px 16px rgba(15, 23, 42, 0.1);
}
@keyframes wf-float-in {
  from { opacity: 0; transform: translateY(10px) scale(0.98); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}
.wf-float-fade-enter-active { transition: opacity 0.18s ease; }
.wf-float-fade-leave-active { transition: opacity 0.14s ease; }
.wf-float-fade-enter-from, .wf-float-fade-leave-to { opacity: 0; }
.wf-float-head {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 8px 9px 8px 10px;
  flex-shrink: 0;
  background: rgba(255, 255, 255, 0.55);
  border-bottom: 1px solid rgba(30, 64, 175, 0.08);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.9);
  position: relative;
  cursor: grab;
  user-select: none;
  touch-action: none;
}
.wf-float-head:active { cursor: grabbing; }
.wf-float-grip {
  flex-shrink: 0;
  color: #94a3b8;
  font-size: 13px;
  line-height: 1;
  letter-spacing: 1px;
  cursor: grab;
}
.wf-float-close {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  border: 1px solid transparent;
  background: transparent;
  border-radius: 8px;
  font-size: 15px;
  color: #64748b;
  cursor: pointer;
  transition: all 0.15s;
}
.wf-float-close:hover {
  background: rgba(255, 255, 255, 0.85);
  border-color: rgba(30, 64, 175, 0.15);
  color: #1e40af;
}
.wf-sess-btn {
  display: flex;
  align-items: center;
  gap: 7px;
  flex: 1;
  min-width: 0;
  padding: 6px 11px;
  border: 1px solid rgba(30, 64, 175, 0.14);
  background: rgba(255, 255, 255, 0.9);
  border-radius: 9px;
  font-family: inherit;
  font-size: 13px;
  font-weight: 500;
  color: #1e293b;
  cursor: pointer;
  transition: all 0.15s;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.9);
}
.wf-sess-btn:hover {
  border-color: rgba(30, 64, 175, 0.3);
  background: rgba(30, 64, 175, 0.06);
  color: #1e40af;
}
.wf-sess-ic { display: flex; flex-shrink: 0; color: #2563eb; }
.wf-sess-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 600;
  text-align: left;
}
.wf-sess-caret {
  color: #94a3b8;
  transition: transform 0.18s;
  flex-shrink: 0;
}
.wf-sess-caret.up { transform: rotate(180deg); }
.wf-new-sess {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  border: 1px dashed rgba(30, 64, 175, 0.35);
  background: transparent;
  border-radius: 9px;
  font-size: 15px;
  color: #1e40af;
  cursor: pointer;
  transition: all 0.15s;
}
.wf-new-sess:hover {
  background: rgba(30, 64, 175, 0.07);
  border-style: solid;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.9), 0 4px 12px -4px rgba(30, 64, 175, 0.3);
}
.wf-sess-backdrop {
  position: fixed;
  inset: 0;
  z-index: 1000;
}
.wf-sess-menu {
  position: absolute;
  top: calc(100% + 6px);
  left: 8px;
  right: 8px;
  z-index: 1001;
  max-height: 400px;
  display: flex;
  flex-direction: column;
  background: rgba(255, 255, 255, 0.88);
  backdrop-filter: blur(32px) saturate(180%);
  -webkit-backdrop-filter: blur(32px) saturate(180%);
  border: 1px solid rgba(30, 64, 175, 0.12);
  border-radius: 13px;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.95),
    0 24px 64px -20px rgba(30, 64, 175, 0.35);
  overflow: hidden;
  transform-origin: top center;
}
.wf-menu-enter-active { transition: all 0.16s cubic-bezier(0.16, 1, 0.3, 1); }
.wf-menu-leave-active { transition: all 0.12s ease-in; }
.wf-menu-enter-from, .wf-menu-leave-to { opacity: 0; transform: scale(0.96); }
.wf-sess-new {
  padding: 11px 14px;
  border: none;
  background: transparent;
  text-align: left;
  font-family: inherit;
  font-size: 13px;
  font-weight: 600;
  color: #1e40af;
  cursor: pointer;
  transition: background 0.15s;
  flex-shrink: 0;
}
.wf-sess-new:hover { background: rgba(30, 64, 175, 0.07); }
.wf-sess-divider {
  height: 1px;
  background: rgba(30, 64, 175, 0.08);
  flex-shrink: 0;
}
.wf-sess-list {
  overflow-y: auto;
  padding: 6px;
}
.wf-sess-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 9px 10px;
  border: none;
  background: transparent;
  border-radius: 8px;
  text-align: left;
  font-family: inherit;
  font-size: 13px;
  color: #334155;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.wf-sess-item:hover { background: rgba(30, 64, 175, 0.07); }
.wf-sess-item.cur { background: rgba(30, 64, 175, 0.09); color: #1e40af; font-weight: 600; }
.wf-sess-item-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.wf-sess-src {
  flex-shrink: 0;
  font-style: normal;
  font-size: 10px;
  font-weight: 600;
  line-height: 1;
  padding: 2px 5px;
  border-radius: 5px;
  background: rgba(37, 99, 235, 0.1);
  border: 1px solid rgba(37, 99, 235, 0.18);
  color: #2563eb;
}
.wf-sess-cur {
  font-size: 8px;
  color: #1e40af;
  flex-shrink: 0;
}
.wf-sess-empty {
  padding: 18px;
  text-align: center;
  font-size: 12px;
  color: #94a3b8;
}
.wf-float-body {
  flex: 1;
  min-height: 0;
  position: relative;
}
.wf-float-body .qa-shell {
  height: 100%;
  border-radius: 0;
}
.wf-float-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  gap: 6px;
}
</style>
