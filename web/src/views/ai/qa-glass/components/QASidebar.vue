<script setup lang="ts">
import { computed, nextTick, ref } from 'vue';
import type { AgentSession as ApiSession } from '@/service/api';
import { brand } from '@/constants/brand';
import QAUserMenu from './QAUserMenu.vue';

/** 「专家」体系称谓随品牌变体：standard=助理 / generic=专家 */
const expertLabel = brand.expertLabel;

interface GroupedSessions {
  starred: ApiSession[];
}

const props = defineProps<{
  groupedSessions: GroupedSessions;
  currentSessionKey: string;
  sessions: ApiSession[];
  renamingKey: string;
  renamingTitle: string;
  runningSessions: Record<string, boolean>;
  /** 「今日简报」功能开关（透传给底部用户菜单） */
  briefEnabled: boolean;
  /** 「流式时展开调用过程」偏好（DB 落库，透传给底部用户菜单） */
  toolProcessExpand: boolean;
}>();

// 画板（workflow）会话已融合进本页（挂板徽标，点击载入后自动伸出其板面板），计数同口径
const visibleCount = computed(() => props.sessions.length);

const emit = defineEmits<{
  newSession: [];
  toggleSidebar: [];
  openTasks: [];
  openWorkflow: [];
  /** 数据集独立入口：打开技能面板并直达数据集 tab（kind=dataset 的 MCP 连接器） */
  openDataset: [];
  openSkill: [];
  createSkill: [];
  showSkillIntro: [];
  openSearch: [];
  openProfile: [];
  openBrief: [];
  'update:briefEnabled': [value: boolean];
  'update:toolProcessExpand': [value: boolean];
  loadSession: [key: string];
  startRename: [key: string, title: string, event: MouseEvent];
  commitRename: [];
  cancelRename: [];
  toggleStar: [key: string, event: MouseEvent];
  deleteSession: [key: string, event: MouseEvent];
  'update:renamingTitle': [value: string];
}>();

// 任务区总开关：真正的收起——把整个任务列表全部折起
const navListCollapsed = ref(false);

// 置顶任务（原「收藏」概念）：与「任务」同款的可折叠分组，置于任务列表上方；默认收起
const pinnedCollapsed = ref(true);
const pinnedSessions = computed(() => props.groupedSessions.starred);

// 任务列表扁平化（去掉今日/昨日/更早日期分组），按更新时间倒序；置顶任务不在主列表重复出现
const flatSessions = computed(() => props.sessions.filter(s => !s.isStarred).sort((a, b) => b.updatedAt - a.updatedAt));

// 任务区收折（默认收折）：只铺最近 5 条；展开后铺全量扁平列表。
// 当前会话与运行中的会话不在前 5 时补位，避免「聊着老任务/后台跑着任务时，它从收折列表里消失」。
const navExpanded = ref(false);
const NAV_COLLAPSED_COUNT = 5;

const recentSessions = computed(() => {
  const all = flatSessions.value;
  const top = all.slice(0, NAV_COLLAPSED_COUNT);
  const keys = new Set(top.map(s => s.sessionKey));
  for (const s of all) {
    if ((s.sessionKey === props.currentSessionKey || props.runningSessions[s.sessionKey]) && !keys.has(s.sessionKey)) {
      top.push(s);
      keys.add(s.sessionKey);
    }
  }
  return top;
});

const displaySessions = computed(() => (navExpanded.value ? flatSessions.value : recentSessions.value));

const hiddenCount = computed(() => Math.max(0, flatSessions.value.length - recentSessions.value.length));

// 「查看更多」后回到任务内部滚动块顶部，避免停在点开按钮处、面对翻不完的长列表
const sessionsListEl = ref<HTMLElement | null>(null);
function toggleNavExpanded() {
  navExpanded.value = !navExpanded.value;
  if (navExpanded.value) nextTick(() => sessionsListEl.value?.scrollTo({ top: 0 }));
}

// 任务区总开关：收起整组列表；收起时把「查看更多」的铺开状态一并复位，
// 下次展开回到默认 5 条收折态，而不是停留在上次铺开的全量长列表
function toggleNavListCollapsed() {
  navListCollapsed.value = !navListCollapsed.value;
  if (navListCollapsed.value) navExpanded.value = false;
}

// 相对创建时间（任务行右侧常驻，行右绝对定位；列表靠行距/字号弱化，不靠删时间）
function relTime(ts: number) {
  if (!ts) return '';
  const diff = Date.now() - ts;
  if (diff < 60_000) return '刚刚';
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}分钟前`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}小时前`;
  if (diff < 30 * 86_400_000) return `${Math.floor(diff / 86_400_000)}天前`;
  if (diff < 365 * 86_400_000) return `${Math.floor(diff / (30 * 86_400_000))}个月前`;
  return `${Math.floor(diff / (365 * 86_400_000))}年前`;
}
</script>

<template>
  <aside class="qa-sidebar">
    <div class="sidebar-inner">
      <header class="sidebar-header">
        <div class="brand">
          <img class="brand-mark" src="/logo-cesi.png" alt="logo" draggable="false" />
        </div>
        <!-- logo 行右侧：搜索 + 收起（搜索从「今日」分组头挪上来，与收起钮并排） -->
        <span class="sidebar-header-actions">
          <button class="side-search-btn" title="搜索任务" @click="emit('openSearch')">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7" /><line x1="21" y1="21" x2="16.65" y2="16.65" /></svg>
          </button>
          <button class="side-collapse-btn" title="收起侧栏" @click="emit('toggleSidebar')">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
              <rect x="3" y="4" width="18" height="16" rx="3" />
              <path d="M9.5 4v16" />
            </svg>
          </button>
        </span>
      </header>

      <button class="new-chat" @click="emit('newSession')">
        <svg class="new-chat-plus" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
          <circle cx="12" cy="12" r="9" />
          <path d="M12 8v8M8 12h8" />
        </svg>
        <span>新建任务</span>
      </button>

      <button class="side-entry" @click="emit('openTasks')">
        <svg class="side-entry-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round">
          <circle cx="12" cy="13" r="8" />
          <path d="M12 9v4l2.5 2.5M9 2h6" />
        </svg>
        <span>定时任务</span>
      </button>

      <!-- 功能导航行：统一 .side-entry 一套样式 + 18px 线性 SVG 图标 -->
      <button class="side-entry" @click="emit('openWorkflow')">
        <svg class="side-entry-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 3l7 4v10l-7 4-7-4V7z" />
          <path d="M12 12l7-4M12 12v9M12 12L5 8" />
        </svg>
        <span>深度任务</span>
      </button>

      <!-- 技能行：创建技能（AI 凝练，新功能主入口）+ 技能商店（商店本体 + 我的技能/上架管理子页面） -->
      <div class="skill-row">
        <div class="side-entry-wrap">
          <button
            class="side-entry"
            title="讲出你的经验，AI 自动凝练整理成可复用技能，以后 @ 即用"
            @click="emit('createSkill')"
          >
            <svg class="side-entry-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round">
              <path d="M12 3l2.1 6.9L21 12l-6.9 2.1L12 21l-2.1-6.9L3 12l6.9-2.1z" />
            </svg>
            <span>创建技能</span>
          </button>
          <!-- 「?」：随时重新查看创建技能说明弹窗（不受首次知晓标记限制） -->
          <button class="side-entry-help" title="查看「创建技能」说明" @click="emit('showSkillIntro')">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
              <circle cx="12" cy="12" r="9.2" />
              <path d="M9.4 9.2a2.6 2.6 0 1 1 3.7 2.3c-.8.4-1.1.9-1.1 1.8" />
              <path d="M12 16.6v.1" />
            </svg>
          </button>
        </div>
        <button class="side-entry" @click="emit('openSkill')">
          <svg class="side-entry-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7">
            <rect x="3" y="3" width="7" height="7" rx="1.5" />
            <rect x="14" y="3" width="7" height="7" rx="1.5" />
            <rect x="3" y="14" width="7" height="7" rx="1.5" />
            <rect x="14" y="14" width="7" height="7" rx="1.5" />
          </svg>
          <span>{{ expertLabel }} · 技能 · 连接器</span>
        </button>
      </div>

      <!-- 数据集独立入口（kind=dataset 的 MCP 连接器，比连接器更重视故单拎一行）：直达面板数据集 tab -->
      <button class="side-entry" @click="emit('openDataset')">
        <svg class="side-entry-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
          <ellipse cx="12" cy="6" rx="8" ry="3" />
          <path d="M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6" />
          <path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3" />
        </svg>
        <span>数据集</span>
      </button>

      <nav class="sessions-nav">
        <!-- 置顶任务分组（原「收藏」概念改名置顶）：与「任务」同款的可折叠分组头，置于任务列表上方 -->
        <div
          v-if="pinnedSessions.length"
          class="sessions-head"
          role="button"
          tabindex="0"
          @click="pinnedCollapsed = !pinnedCollapsed"
          @keydown.enter="pinnedCollapsed = !pinnedCollapsed"
        >
          <span class="sessions-head-label">置顶任务</span>
          <span class="sessions-head-count">（{{ pinnedSessions.length }}）</span>
          <span class="group-arrow" :class="{ 'group-arrow-open': !pinnedCollapsed }">▾</span>
        </div>
        <div v-if="pinnedSessions.length && !pinnedCollapsed" class="session-group session-group-pinned sessions-scroll">
          <ul class="session-list">
            <li
              v-for="s in pinnedSessions"
              :key="s.sessionKey"
              :class="{ active: s.sessionKey === currentSessionKey, running: runningSessions[s.sessionKey] }"
              class="session-item"
              @click="renamingKey !== s.sessionKey && emit('loadSession', s.sessionKey)"
            >
              <span v-if="runningSessions[s.sessionKey]" class="orbit-dual orbit-dual--sm">
                <span class="star" /><span class="star" />
                <span class="inner-ring"><span class="inner-star" /></span>
              </span>
              <span v-else class="session-dot" />
              <template v-if="renamingKey === s.sessionKey">
                <input
                  :data-key="s.sessionKey"
                  :value="renamingTitle"
                  class="session-rename-input"
                  @input="emit('update:renamingTitle', ($event.target as HTMLInputElement).value)"
                  @keydown.enter="emit('commitRename')"
                  @keydown.esc="emit('cancelRename')"
                  @blur="emit('commitRename')"
                  @click.stop
                />
              </template>
              <span v-else class="session-title" :title="s.title" @dblclick.stop="emit('startRename', s.sessionKey, s.title, $event)">{{ s.title }}</span>
              <!-- 会话板徽标（看/析）暂时全部不展示（眼花、无需区分）；恢复时改回 v-if="s.workflowKey" -->
              <em
                v-if="false"
                class="session-board-badge"
                :class="s.boardType === 'html' ? 'sb-html' : 'sb-board'"
                :title="s.boardType === 'html' ? '应用制作任务' : '流程编排任务'"
              >{{ s.boardType === 'html' ? '应' : '流' }}</em>
              <span class="session-time">{{ relTime(s.createdAt) }}</span>
              <button class="session-star session-star-on" title="取消置顶" @click="emit('toggleStar', s.sessionKey, $event)">
                <svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 17v5" /><path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z" /></svg>
              </button>
              <button class="session-del" title="删除" @click="emit('deleteSession', s.sessionKey, $event)"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M5.5 5.5l13 13M18.5 5.5l-13 13" /></svg></button>
            </li>
          </ul>
        </div>

        <!-- 任务分组（扁平列表，不再按今日/昨日/更早分组）：点头部收起整个任务列表 -->
        <div
          v-if="visibleCount > 0"
          class="sessions-head"
          role="button"
          tabindex="0"
          @click="toggleNavListCollapsed"
          @keydown.enter="toggleNavListCollapsed"
        >
          <span class="sessions-head-label">任务</span>
          <span class="sessions-head-count">（{{ flatSessions.length }}）</span>
          <span class="group-arrow" :class="{ 'group-arrow-open': !navListCollapsed }">▾</span>
        </div>
        <template v-if="!navListCollapsed">
          <!-- 收折态（默认）只铺最近 10 条，「展开更多」后铺全量扁平列表 -->
          <div v-if="displaySessions.length" ref="sessionsListEl" class="session-group sessions-scroll">
            <ul class="session-list">
              <li
                v-for="s in displaySessions"
                :key="s.sessionKey"
                :class="{ active: s.sessionKey === currentSessionKey, running: runningSessions[s.sessionKey] }"
                class="session-item"
                @click="renamingKey !== s.sessionKey && emit('loadSession', s.sessionKey)"
              >
                <span v-if="runningSessions[s.sessionKey]" class="orbit-dual orbit-dual--sm">
                  <span class="star" /><span class="star" />
                  <span class="inner-ring"><span class="inner-star" /></span>
                </span>
                <span v-else class="session-dot" />
                <template v-if="renamingKey === s.sessionKey">
                  <input
                    :data-key="s.sessionKey"
                    :value="renamingTitle"
                    class="session-rename-input"
                    @input="emit('update:renamingTitle', ($event.target as HTMLInputElement).value)"
                    @keydown.enter="emit('commitRename')"
                    @keydown.esc="emit('cancelRename')"
                    @blur="emit('commitRename')"
                    @click.stop
                  />
                </template>
                <span v-else class="session-title" :title="s.title" @dblclick.stop="emit('startRename', s.sessionKey, s.title, $event)">{{ s.title }}</span>
                <!-- 会话板徽标（看/析）暂时全部不展示（眼花、无需区分）；恢复时改回 v-if="s.workflowKey" -->
                <em
                  v-if="false"
                  class="session-board-badge"
                  :class="s.boardType === 'html' ? 'sb-html' : 'sb-board'"
                  :title="s.boardType === 'html' ? '应用制作任务' : '流程编排任务'"
                >{{ s.boardType === 'html' ? '应' : '流' }}</em>
                <!-- 相对创建时间常驻行右，hover 时隐去让位给置顶/删除钮 -->
                <span class="session-time">{{ relTime(s.createdAt) }}</span>
                <button class="session-star" title="置顶" @click="emit('toggleStar', s.sessionKey, $event)">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 17v5" /><path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z" /></svg>
                </button>
                <button class="session-del" title="删除" @click="emit('deleteSession', s.sessionKey, $event)"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M5.5 5.5l13 13M18.5 5.5l-13 13" /></svg></button>
              </li>
            </ul>
            <!-- 查看更多：放进内部滚动块、紧随列表末尾（而非被滚动块撑到视口底部）；仅收折态出现；展开后想收起直接点上方「任务」头部 -->
            <button v-if="hiddenCount > 0 && !navExpanded" class="sessions-more" type="button" @click="toggleNavExpanded()">
              查看更多（{{ hiddenCount }}）
            </button>
          </div>
        </template>

        <div v-if="visibleCount === 0" class="sessions-empty">尚无任务</div>
      </nav>

      <!-- 今日简报灰卡（样张 .side-brief 同款卡位：会话列表与用户行之间，双主题统一入口） -->
      <button v-if="briefEnabled" class="side-brief-card" type="button" @click="emit('openBrief')">
        <svg class="sbc-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" width="18" height="18">
          <path d="M12 3a6 6 0 0 1 3.6 10.8c-.7.6-1.1 1.3-1.3 2.2h-4.6c-.2-.9-.6-1.6-1.3-2.2A6 6 0 0 1 12 3Z" />
          <path d="M10 19h4m-3 3h2" />
        </svg>
        <span class="sbc-text">
          <span class="sbc-title">今日简报</span>
          <span class="sbc-sub">AI 已整理今日重要信息</span>
        </span>
        <span class="sbc-arrow">›</span>
      </button>

      <footer class="sidebar-foot">
        <QAUserMenu
          :brief-enabled="briefEnabled"
          :tool-process-expand="toolProcessExpand"
          @update:brief-enabled="emit('update:briefEnabled', $event)"
          @update:tool-process-expand="emit('update:toolProcessExpand', $event)"
          @open-profile="emit('openProfile')"
        />
      </footer>
    </div>
  </aside>
</template>

<style scoped>
.qa-sidebar {
  position: relative;
  z-index: 2;
  background: var(--side-bg, rgba(241, 244, 250, 0.55));
  backdrop-filter: var(--side-blur, none);
  -webkit-backdrop-filter: var(--side-blur, none);
  border-right: 1px solid var(--side-border, transparent);
  overflow: hidden;
  transition: opacity 0.3s;
  box-shadow: var(--side-shadow, none);
}

/* 与 ink-theme-preview.html 侧栏逐值对齐 */
.sidebar-inner {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-width: 272px;
  padding: 0;
  gap: 0;
}

.sidebar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 16px 12px;
  border-bottom: none;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
}

.brand-mark {
  position: relative;
  width: 34px;
  height: 34px;
  object-fit: contain;
  flex-shrink: 0;
  margin-bottom: 0;
  user-select: none;
}

/* 渐变文字在 kimi 主题（--grad-brand 为实色）下自动呈现为实色字 */
.brand-title {
  font-family: var(--font-body);
  font-weight: 700;
  font-size: 15px;
  color: var(--ink);
  letter-spacing: -0.02em;
  line-height: 1.2;
  background: var(--grad-brand);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}

/* logo 行右侧操作组：搜索 + 收起（双主题通用，同款方形图标钮） */
.sidebar-header-actions {
  display: inline-flex;
  align-items: center;
  gap: 2px;
}

.side-collapse-btn,
.side-search-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border: none;
  border-radius: 9px;
  background: transparent;
  color: var(--ink-3);
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}

.side-collapse-btn:hover,
.side-search-btn:hover {
  background: var(--fill-hover);
  color: var(--ink);
}

/* 新建会话：玻璃 = 渐变实底；kimi = 白色浮卡 + Ctrl K 键帽（同一结构，变量换皮） */
.new-chat {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 52px;
  padding: 0 14px;
  margin: 2px 12px 14px;
  background: var(--newchat-bg, var(--grad-brand));
  color: var(--newchat-ink, #fff);
  border: 1px solid var(--card-border);
  border-radius: var(--r-newchat, 14px);
  font-family: var(--font-body);
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s ease;
  letter-spacing: 0.005em;
  position: relative;
  overflow: hidden;
  box-shadow: var(--card-shadow);
}

.new-chat:hover {
  transform: translateY(-1px);
  box-shadow: var(--shadow-md, var(--card-shadow));
}

.new-chat > * {
  position: relative;
  z-index: 1;
}

.new-chat-plus {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
}

.new-chat-kbd {
  display: flex;
  gap: 4px;
  margin-left: auto;
}

/* 样张语言：键帽取代箭头 */
.new-chat-arrow {
  display: none;
}

.kbd {
  padding: 2px 6px;
  border-radius: 6px;
  background: var(--kbd-bg, rgba(255, 255, 255, 0.22));
  font-family: 'JetBrains Mono', monospace;
  font-size: 10.5px;
  color: var(--kbd-ink, rgba(255, 255, 255, 0.9));
}

/* ─── 侧栏入口通用行：扁平导航行（Kimi 行语言，双主题通用） ─── */
/* 功能导航行：一套样式管全部入口 */
.side-entry {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  height: 44px;
  padding: 0 20px;
  background: transparent;
  color: var(--ink, #0f172a);
  border: none;
  border-radius: 10px;
  font-family: var(--font-body);
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.14s ease;
  margin-top: 0;
  text-align: left;
}

.side-entry:hover {
  background: var(--fill-hover);
  color: var(--ink, #0f172a);
}

.side-entry-icon {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
  color: var(--ink-2, #334155);
}

.side-entry:hover .side-entry-icon {
  color: var(--ink, #0f172a);
}

/* ─── 技能行：两条扁平导航行（创建技能 / 技能商店） ─── */
.skill-row {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

/* 「创建技能」行右侧 ? 说明钮：与会话行 hover 显操作钮同一语言——
   默认隐藏，hover/聚焦行时浮现，双主题走通用令牌 */
.side-entry-wrap {
  position: relative;
}

.side-entry-help {
  position: absolute;
  top: 50%;
  right: 12px;
  transform: translateY(-50%);
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  padding: 0;
  color: var(--ink-4);
  background: transparent;
  border: none;
  border-radius: 50%;
  cursor: pointer;
  opacity: 0;
  transition: all 0.15s ease;
}

.side-entry-wrap:hover .side-entry-help,
.side-entry-help:focus-visible {
  color: var(--ink-3);
  opacity: 0.7;
}

.side-entry-help:hover {
  color: var(--ink);
  background: var(--fill-2);
  opacity: 1;
}

/* 触屏没有 hover：保持弱可见，保证移动端可点 */
@media (hover: none) {
  .side-entry-help {
    opacity: 0.55;
  }
}

/* 任务区外层不再整块滚动：纵向 flex 布局，置顶 / 任务两个列表各自成内部滚动块 */
.sessions-nav {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  margin: 0;
  padding: 0 10px 10px;
}

/* 内部滚动块通用：滚动条样式与留槽防抖跟着滚块走。
   隐蔽式滚动条需双轨覆盖：标准属性（Firefox，压过 global.css 的 * 通配）+ webkit 伪元素（Chrome/Edge） */
.sessions-scroll {
  overflow-y: auto;
  min-height: 0;
  scrollbar-gutter: stable; /* 滚动条出现时不挤压列表、不抖动 */
  scrollbar-width: thin;
  scrollbar-color: transparent transparent;
}

.sessions-scroll:hover {
  scrollbar-color: rgba(30, 64, 175, 0.16) transparent;
}

.sessions-scroll::-webkit-scrollbar {
  width: 2px;
}

.sessions-scroll::-webkit-scrollbar-track {
  background: transparent;
}

/* 隐蔽式滚动条：平时完全透明，悬停列表块时才浮现一根淡痕 */
.sessions-scroll::-webkit-scrollbar-thumb {
  background: transparent;
  border-radius: 4px;
  transition: background 0.2s ease;
}

.sessions-scroll:hover::-webkit-scrollbar-thumb {
  background: rgba(30, 64, 175, 0.16);
}

.session-group {
  flex: 1;
  margin-bottom: 0;
  padding-bottom: 6px; /* 滚到底时末条不与「展开更多」贴边 */
}

/* ─── 置顶任务分组：内部滚动块限高，不侵占下方任务区；不加分隔线，留白区隔即可 ─── */
.session-group-pinned {
  flex: none;
  max-height: 38%;
  margin-bottom: 2px;
}

/* 收折箭头默认隐藏，hover 分组头时才浮现（触屏常驻弱显，保证可点感知） */
.group-arrow {
  font-size: 9px;
  margin-left: auto;
  transition: transform 0.2s ease, opacity 0.15s ease;
  display: inline-block;
  opacity: 0;
}

.sessions-head:hover .group-arrow,
.sessions-head:focus-visible .group-arrow {
  opacity: 0.5;
}

@media (hover: none) {
  .group-arrow {
    opacity: 0.4;
  }
}
.group-arrow-open {
  transform: rotate(0deg);
}
.group-arrow:not(.group-arrow-open) {
  transform: rotate(-90deg);
}

.session-list {
  list-style: none;
  padding: 0;
  margin: 0;
}

.session-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  font-size: 13px;
  color: var(--ink-2);
  cursor: pointer;
  border-left: none;
  border-radius: 10px;
  margin-bottom: 2px;
  transition: all 0.18s ease;
  position: relative;
  background: transparent;
}

/* 悬停稍明显：比全局 --fill-hover（玻璃 6% / ink 4.5%）加深一档，双主题各自微调 */
.session-item:hover {
  background: rgba(30, 64, 175, 0.09);
  border-left-color: transparent;
}

.session-item.active {
  background: var(--active-bg);
  border-left-color: transparent;
  color: var(--active-ink, var(--ink));
  font-weight: 600;
  box-shadow: var(--active-shadow, none);
}

/* 样张行首无圆点：置顶行同样不留行首图标（置顶语义由行尾常驻图钉表达） */
.session-dot {
  display: none;
}

.session-title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  position: relative;
  z-index: 1;
}

/* 相对创建时间：常驻行右（绝对定位不占流式宽度），hover 时隐去让位给置顶/删除钮；
   pointer-events 穿透，不挡行点击 */
.session-time {
  position: absolute;
  right: 12px;
  top: 50%;
  transform: translateY(-50%);
  font-size: 11px;
  color: var(--ink-4);
  white-space: nowrap;
  pointer-events: none;
  transition: opacity 0.15s;
}

.session-item:hover .session-time {
  opacity: 0;
}

/* 行尾置顶/删除两钮：同款 20×20 底座 + 同量级图标，负外边距抵消行 gap 收紧间距 */
.session-del {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  padding: 0;
  margin-left: -6px;
  background: none;
  border: none;
  line-height: 1;
  color: var(--ink-4);
  cursor: pointer;
  border-radius: 6px;
  opacity: 0;
  transition: all 0.15s;
  position: relative;
  z-index: 1;
}

/* 叉用 SVG 精确控制尺寸（文本 × 字形偏小且随字体漂移）：与置顶图钉同 15px 底座 */
.session-del svg {
  width: 15px;
  height: 15px;
  display: block;
}

.session-item:hover .session-del {
  opacity: 0.7;
}

.session-del:hover {
  color: var(--accent);
  opacity: 1 !important;
  background: var(--accent-soft);
}

.session-star {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  padding: 0;
  background: none;
  border: none;
  line-height: 1;
  color: var(--ink-4);
  cursor: pointer;
  border-radius: 6px;
  opacity: 0;
  transition: all 0.15s;
  position: relative;
  z-index: 1;
}

.session-star svg {
  width: 15px;
  height: 15px;
  display: block;
}

.session-item:hover .session-star {
  opacity: 0.6;
}

.session-star:hover {
  color: #f5a623;
  opacity: 1 !important;
}

/* 置顶态：只保留颜色区分；与普通置顶钮一样默认隐藏、悬停行时显形 */
.session-star-on {
  color: #f5a623;
}

.session-rename-input {
  flex: 1;
  min-width: 0;
  background: var(--surface-1);
  border: 1px solid var(--accent);
  border-radius: 6px;
  color: var(--ink-1);
  font-size: 12px;
  font-family: var(--font-sans);
  padding: 1px 5px;
  outline: none;
}

/* 板徽标：画板会话融合进侧栏后的类型标识（双主题走通用令牌） */
.session-board-badge {
  flex-shrink: 0;
  font-style: normal;
  font-size: 9px;
  font-weight: 700;
  line-height: 1;
  padding: 2.5px 4px;
  border-radius: 4px;
  letter-spacing: 0.02em;
}

.session-board-badge.sb-board {
  color: var(--c-blue, #1e40af);
  background: rgba(37, 99, 235, 0.12);
}

.session-board-badge.sb-html {
  color: #0e7490;
  background: rgba(8, 145, 178, 0.12);
}

/* ink 主题：徽标塌成墨灰阶 */
:root[data-qa-theme='ink'] .session-board-badge.sb-board,
:root[data-qa-theme='ink'] .session-board-badge.sb-html {
  color: var(--ink-2);
  background: var(--fill-2);
}

.sessions-empty {
  font-family: var(--font-display);
  font-size: 13px;
  color: var(--ink-3);
  font-style: italic;
  padding: 30px 12px;
  text-align: center;
}

/* 任务区 / 置顶区可折叠分组头（点击收起整组列表）：
   弱化为安静的分区标签——小字淡色、无整行底色填充，hover 仅文字微深 + 箭头浮现 */
.sessions-head {
  flex: none;
  min-width: 0;
  display: flex;
  align-items: center;
  padding: 6px 12px 2px;
  border: none;
  border-radius: 10px;
  background: transparent;
  font-family: inherit;
  cursor: pointer;
  user-select: none;
  transition: background 0.15s ease;
}

/* 分组头安静感靠默认态表达，悬停仍给轻底色，可点性更易见 */
.sessions-head:hover {
  background: var(--fill-hover);
}

.sessions-head:hover .sessions-head-label,
.sessions-head:focus-visible .sessions-head-label {
  color: var(--ink-3);
}

.sessions-head-label {
  font-size: 11px;
  font-weight: 500;
  color: var(--ink-4);
  letter-spacing: 0.02em;
  transition: color 0.15s ease;
}

.sessions-head-count {
  font-family: var(--font-body);
  font-size: 10px;
  font-weight: 400;
  color: var(--ink-4);
}

/* 任务区收折开关：紧随列表末尾的「查看更多」（收起由「任务」分组头承担，故无「收起」钮）；
   居左排列，文字起点与列表行标题对齐 */
.sessions-more {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 4px;
  width: calc(100% - 8px);
  margin: 2px 4px 6px;
  padding: 6px;
  border: none;
  border-radius: 8px;
  background: transparent;
  font-family: inherit;
  font-size: 11.5px;
  color: var(--ink-4);
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}

.sessions-more:hover {
  background: var(--fill-hover);
  color: var(--ink-2);
}

.sidebar-foot {
  display: flex;
  align-items: center;
  padding: 4px 12px 14px;
  border-top: none;
}

/* 用户信息条撑满侧栏宽度，hover 区域与上方功能按钮对齐 */
.sidebar-foot > * {
  flex: 1;
  min-width: 0;
}

/* 样张同款头像瓦片（白底发丝边方圆） */
.sidebar-foot :deep(.text-icon-large) {
  width: 34px;
  height: 34px;
  border-radius: 10px;
  background: var(--card-bg, #fff);
  border: 1px solid var(--line-hair);
  box-shadow: var(--card-shadow, none);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  color: var(--ink-2);
}

/* 侧栏底部弱化用户按钮：淡色小字，融入玻璃面板，不抢视觉 */
.sidebar-foot :deep(.n-button) {
  width: 100%;
  justify-content: flex-start;
  color: var(--ink-3);
}

.sidebar-foot :deep(.n-button:hover) {
  color: var(--ink-2);
}

.sidebar-foot :deep(.text-icon-large) {
  font-size: 17px;
  color: var(--ink-3);
}

.sidebar-foot :deep(.text-16px) {
  font-size: 13px;
  font-weight: 500;
  color: var(--ink-3);
}

.sidebar-foot :deep(.n-button:hover .text-icon-large),
.sidebar-foot :deep(.n-button:hover .text-16px) {
  color: var(--ink-2);
}

.orbit-dual--sm {
  width: 14px;
  height: 14px;
  position: relative;
  border-radius: 50%;
  border: 1px dashed rgba(58, 91, 217, 0.2);
  animation: orbit-spin 4s linear infinite;
  flex-shrink: 0;
}

.orbit-dual--sm .star {
  position: absolute;
  width: 3px;
  height: 3px;
  border-radius: 50%;
  top: -1.5px;
  left: 50%;
  margin-left: -1.5px;
  background: #3a5bd9;
  box-shadow: 0 0 4px rgba(58, 91, 217, 0.5);
}

.orbit-dual--sm .star:nth-child(2) {
  top: auto;
  bottom: -1.5px;
  background: #d94f7a;
  box-shadow: 0 0 4px rgba(217, 79, 122, 0.5);
}

.orbit-dual--sm .inner-ring {
  position: absolute;
  inset: 3px;
  border-radius: 50%;
  border: 1px dashed rgba(123, 94, 167, 0.2);
  animation: orbit-spin 3s linear infinite reverse;
}

.orbit-dual--sm .inner-star {
  position: absolute;
  width: 2px;
  height: 2px;
  border-radius: 50%;
  top: -1px;
  left: 50%;
  margin-left: -1px;
  background: #7b5ea7;
  box-shadow: 0 0 3px rgba(123, 94, 167, 0.5);
}

@keyframes orbit-spin {
  to { transform: rotate(360deg); }
}

.session-item.running .session-title {
  color: var(--accent);
  opacity: 0.85;
}

/* ─── 今日简报灰卡（样张 .side-brief 逐值对齐，双主题渲染） ─── */
.side-brief-card {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 8px 12px 8px;
  padding: 12px 14px;
  border: none;
  border-radius: var(--r-card, 14px);
  background: var(--fill-2);
  font-family: var(--font-body);
  text-align: left;
  cursor: pointer;
  transition: background 0.15s ease, filter 0.15s ease;
}

.side-brief-card:hover {
  filter: brightness(0.98);
}

.sbc-icon {
  color: var(--ink-2);
  flex-shrink: 0;
}

.sbc-text {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.sbc-title {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--ink);
}

.sbc-sub {
  font-size: 11.5px;
  color: var(--ink-3);
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sbc-arrow {
  margin-left: auto;
  color: var(--ink-4);
  font-size: 15px;
}

:root[data-qa-theme='ink'] .session-item:hover {
  background: rgba(23, 24, 26, 0.07);
}

:root[data-qa-theme='ink'] .session-star-on {
  color: var(--ink-2);
}

:root[data-qa-theme='ink'] .session-star:hover {
  color: var(--ink);
}

</style>
