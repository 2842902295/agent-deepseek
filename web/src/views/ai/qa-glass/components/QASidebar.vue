<script setup lang="ts">
import { computed, ref } from 'vue';
import { brand } from '@/constants/brand';
import type { AgentSession as ApiSession } from '@/service/api';
import QAUserMenu from './QAUserMenu.vue';

interface GroupedSessions {
  starred: ApiSession[];
  today: ApiSession[];
  yesterday: ApiSession[];
  earlier: ApiSession[];
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
}>();

// 画板（workflow）会话不在本页展示，空态/计数与分组列表保持同口径
const visibleCount = computed(() => props.sessions.filter(s => s.source !== 'workflow').length);

const emit = defineEmits<{
  newSession: [];
  openTasks: [];
  openWorkflow: [];
  openSkill: [];
  createSkill: [];
  openSearch: [];
  openProfile: [];
  'update:briefEnabled': [value: boolean];
  loadSession: [key: string];
  startRename: [key: string, title: string, event: MouseEvent];
  commitRename: [];
  cancelRename: [];
  toggleStar: [key: string, event: MouseEvent];
  deleteSession: [key: string, event: MouseEvent];
  'update:renamingTitle': [value: string];
}>();

const starredCollapsed = ref(true);
</script>

<template>
  <aside class="qa-sidebar">
    <div class="sidebar-inner">
      <header class="sidebar-header">
        <div class="brand">
          <span class="brand-mark">§</span>
          <div class="brand-text">
            <div class="brand-title">{{ brand.qaSidebarTitle }}</div>
          </div>
        </div>
      </header>

      <button class="new-chat" @click="emit('newSession')">
        <span class="new-chat-plus">+</span>
        <span>新建对话</span>
        <span class="new-chat-arrow">→</span>
      </button>

      <button class="deep-task" @click="emit('openWorkflow')">
        <span class="deep-task-icon">⬡</span>
        <span class="deep-task-label">深度分析<span class="deep-task-label-dim"> · 人机协作</span></span>
        <span class="deep-task-arrow">→</span>
      </button>

      <button class="side-entry" @click="emit('openTasks')">
        <span class="side-entry-icon">⏱</span>
        <span>定时任务</span>
        <span class="side-entry-arrow">→</span>
      </button>

      <!-- 技能行（拆两段）：左 = 「新建对话」同款渐变按钮，创建技能（AI 凝练，新功能主入口）；
           右 = 素入口，打开技能管理面板 -->
      <div class="skill-row">
        <button
          class="skill-create"
          title="讲出你的经验，AI 自动凝练整理成可复用技能，以后 @ 即用"
          @click="emit('createSkill')"
        >
          <span class="skill-create-spark">✦</span>
          <span>创建技能</span>
        </button>
        <button class="side-entry skill-manage" @click="emit('openSkill')">
          <span class="side-entry-icon">⊞</span>
          <span>技能管理</span>
        </button>
      </div>

      <nav class="sessions-nav">
        <div v-if="groupedSessions.starred.length" class="session-group">
          <div
            :class="['starred-header', { 'starred-header-open': !starredCollapsed }]"
            @click="starredCollapsed = !starredCollapsed"
          >
            <span class="starred-header-star">★</span>
            <span class="starred-header-label">收藏</span>
            <span class="starred-header-count">{{ groupedSessions.starred.length }}</span>
            <span class="group-arrow" :class="{ 'group-arrow-open': !starredCollapsed }">▾</span>
          </div>
          <Transition name="sg-collapse">
            <ul v-if="!starredCollapsed" class="session-list">
            <li
              v-for="s in groupedSessions.starred"
              :key="s.sessionKey"
              :class="{ active: s.sessionKey === currentSessionKey, running: runningSessions[s.sessionKey] }"
              class="session-item"
              @click="renamingKey !== s.sessionKey && emit('loadSession', s.sessionKey)"
            >
              <span v-if="runningSessions[s.sessionKey]" class="orbit-dual orbit-dual--sm">
                <span class="star"/><span class="star"/>
                <span class="inner-ring"><span class="inner-star"/></span>
              </span>
              <span v-else class="session-dot session-dot-star">★</span>
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
              <span v-else class="session-title" @dblclick.stop="emit('startRename', s.sessionKey, s.title, $event)">{{ s.title }}</span>
              <button class="session-star session-star-on" title="取消收藏" @click="emit('toggleStar', s.sessionKey, $event)">★</button>
              <button class="session-del" title="删除" @click="emit('deleteSession', s.sessionKey, $event)">×</button>
            </li>
          </ul>
          </Transition>
        </div>

        <div class="session-group">
          <div class="session-group-label">
            <button class="group-search" title="搜索历史对话" @click="emit('openSearch')">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
            </button>
            <span class="group-line"/>
            <span class="group-text">今&nbsp;日</span>
            <span class="group-count">{{ groupedSessions.today.length }}</span>
          </div>
          <ul v-if="groupedSessions.today.length" class="session-list">
            <li
              v-for="s in groupedSessions.today"
              :key="s.sessionKey"
              :class="{ active: s.sessionKey === currentSessionKey, running: runningSessions[s.sessionKey] }"
              class="session-item"
              @click="renamingKey !== s.sessionKey && emit('loadSession', s.sessionKey)"
            >
              <span v-if="runningSessions[s.sessionKey]" class="orbit-dual orbit-dual--sm">
                <span class="star"/><span class="star"/>
                <span class="inner-ring"><span class="inner-star"/></span>
              </span>
              <span v-else class="session-dot"/>
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
              <span v-else class="session-title" @dblclick.stop="emit('startRename', s.sessionKey, s.title, $event)">{{ s.title }}</span>
              <button class="session-star" title="收藏" @click="emit('toggleStar', s.sessionKey, $event)">☆</button>
              <button class="session-del" title="删除" @click="emit('deleteSession', s.sessionKey, $event)">×</button>
            </li>
          </ul>
        </div>

        <div v-if="groupedSessions.yesterday.length" class="session-group">
          <div class="session-group-label">
            <span class="group-line"/>
            <span class="group-text">昨&nbsp;日</span>
            <span class="group-count">{{ groupedSessions.yesterday.length }}</span>
          </div>
          <ul class="session-list">
            <li
              v-for="s in groupedSessions.yesterday"
              :key="s.sessionKey"
              :class="{ active: s.sessionKey === currentSessionKey, running: runningSessions[s.sessionKey] }"
              class="session-item"
              @click="renamingKey !== s.sessionKey && emit('loadSession', s.sessionKey)"
            >
              <span v-if="runningSessions[s.sessionKey]" class="orbit-dual orbit-dual--sm">
                <span class="star"/><span class="star"/>
                <span class="inner-ring"><span class="inner-star"/></span>
              </span>
              <span v-else class="session-dot"/>
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
              <span v-else class="session-title" @dblclick.stop="emit('startRename', s.sessionKey, s.title, $event)">{{ s.title }}</span>
              <button class="session-star" title="收藏" @click="emit('toggleStar', s.sessionKey, $event)">☆</button>
              <button class="session-del" title="删除" @click="emit('deleteSession', s.sessionKey, $event)">×</button>
            </li>
          </ul>
        </div>

        <div v-if="groupedSessions.earlier.length" class="session-group">
          <div class="session-group-label">
            <span class="group-line"/>
            <span class="group-text">更&nbsp;早</span>
            <span class="group-count">{{ groupedSessions.earlier.length }}</span>
          </div>
          <ul class="session-list">
            <li
              v-for="s in groupedSessions.earlier"
              :key="s.sessionKey"
              :class="{ active: s.sessionKey === currentSessionKey, running: runningSessions[s.sessionKey] }"
              class="session-item"
              @click="renamingKey !== s.sessionKey && emit('loadSession', s.sessionKey)"
            >
              <span v-if="runningSessions[s.sessionKey]" class="orbit-dual orbit-dual--sm">
                <span class="star"/><span class="star"/>
                <span class="inner-ring"><span class="inner-star"/></span>
              </span>
              <span v-else class="session-dot"/>
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
              <span v-else class="session-title" @dblclick.stop="emit('startRename', s.sessionKey, s.title, $event)">{{ s.title }}</span>
              <button class="session-star" title="收藏" @click="emit('toggleStar', s.sessionKey, $event)">☆</button>
              <button class="session-del" title="删除" @click="emit('deleteSession', s.sessionKey, $event)">×</button>
            </li>
          </ul>
        </div>

        <div v-if="visibleCount === 0" class="sessions-empty">尚无历史对话</div>
      </nav>

      <footer class="sidebar-foot">
        <QAUserMenu
          :briefEnabled="briefEnabled"
          @update:briefEnabled="emit('update:briefEnabled', $event)"
          @openProfile="emit('openProfile')"
        />
      </footer>
    </div>
  </aside>
</template>

<style scoped>
.qa-sidebar {
  position: relative;
  z-index: 2;
  background: rgba(255, 255, 255, 0.42);
  backdrop-filter: blur(40px) saturate(200%);
  -webkit-backdrop-filter: blur(40px) saturate(200%);
  border-right: none;
  overflow: hidden;
  transition: opacity 0.3s;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.95),
    inset 0 0 0 1px rgba(255, 255, 255, 0.4),
    inset -1px 0 0 rgba(255, 255, 255, 0.6);
}

.sidebar-inner {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-width: 286px;
  padding: 24px 20px 16px;
  gap: 14px;
}

.sidebar-header {
  padding-bottom: 16px;
  border-bottom: 1px solid rgba(30, 64, 175, 0.08);
}

.brand {
  display: flex;
  align-items: center;
  gap: 13px;
}

.brand-mark {
  position: relative;
  width: 34px;
  height: 34px;
  border-radius: 11px;
  background: linear-gradient(110deg, #1e40af 0%, #2563eb 35%, #0ea5e9 70%, #0891b2 100%);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 16px;
  font-weight: 700;
  font-style: normal;
  color: #fff;
  letter-spacing: -0.02em;
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.18);
  margin-bottom: 0;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.5),
    0 4px 14px -4px rgba(30, 64, 175, 0.55);
}

.brand-title {
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-weight: 700;
  font-size: 16px;
  color: var(--ink);
  letter-spacing: -0.02em;
  line-height: 1.2;
  background: linear-gradient(110deg, #1e40af 0%, #2563eb 35%, #0ea5e9 70%, #0891b2 100%);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}

.new-chat {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  background: linear-gradient(110deg, #1e40af 0%, #2563eb 35%, #0ea5e9 70%, #0891b2 100%);
  color: #fff;
  border: none;
  border-radius: 11px;
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s ease;
  letter-spacing: 0.005em;
  position: relative;
  overflow: hidden;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.4),
    0 4px 14px -2px rgba(30, 64, 175, 0.45);
}

.new-chat:hover {
  transform: translateY(-1px);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.4),
    0 8px 24px -4px rgba(30, 64, 175, 0.55);
}

.new-chat > * {
  position: relative;
  z-index: 1;
}

.new-chat-plus {
  font-size: 18px;
  line-height: 1;
  font-weight: 300;
}

.new-chat-arrow {
  margin-left: auto;
  font-family: var(--font-display);
  font-size: 16px;
  opacity: 0.6;
  transition: transform 0.3s, opacity 0.3s;
}

.new-chat:hover .new-chat-arrow {
  transform: translateX(4px);
  opacity: 1;
}

/* ─── 侧栏入口通用行（定时任务 / 技能管理共用；技能管理不带箭头） ─── */
.side-entry {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 14px;
  background: var(--surface, rgba(255, 255, 255, 0.42));
  color: var(--ink-2, #334155);
  border: 1px solid rgba(30, 64, 175, 0.10);
  border-radius: 11px;
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s ease;
  margin-top: 0;
}

.side-entry:hover {
  background: var(--surface-strong, rgba(255, 255, 255, 0.62));
  color: var(--ink, #0f172a);
  border-color: rgba(30, 64, 175, 0.20);
}

.side-entry-icon {
  font-size: 15px;
  line-height: 1;
}

.side-entry-arrow {
  margin-left: auto;
  font-size: 14px;
  opacity: 0.35;
  transition: transform 0.3s, opacity 0.3s;
}

.side-entry:hover .side-entry-arrow {
  transform: translateX(3px);
  opacity: 0.7;
}

/* ─── 技能行：左「创建技能」渐变按钮（新建对话同款）+ 右「技能管理」素入口 ─── */
.skill-row {
  display: flex;
  gap: 10px;
}

.skill-create {
  flex: 1 1 0;
  min-width: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 8px 12px;
  background: linear-gradient(110deg, #1e40af 0%, #2563eb 35%, #0ea5e9 70%, #0891b2 100%);
  color: #fff;
  border: none;
  border-radius: 11px;
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.005em;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.2s ease;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.4),
    0 4px 14px -2px rgba(30, 64, 175, 0.45);
}

.skill-create:hover {
  transform: translateY(-1px);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.4),
    0 8px 24px -4px rgba(30, 64, 175, 0.55);
}

.skill-create-spark {
  font-size: 14px;
  line-height: 1;
}

/* 右侧管理入口：与左按钮等宽平分（flex: 1 1 0 同基准），内容居中成对；
   创建按钮靠渐变突出，而不是靠块头 */
.skill-manage {
  flex: 1 1 0;
  min-width: 0;
  justify-content: center;
  margin-top: 0;
}

/* ─── 深度任务：重磅功能入口 —— 流光玻璃卡，和亮色玻璃侧栏同语言，但高一档 ─── */
.deep-task {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 10px 14px;
  margin-top: 4px;
  border-radius: 13px;
  border: 1.5px solid transparent;
  background:
    linear-gradient(115deg, rgba(255, 255, 255, 0.8) 0%, rgba(230, 241, 255, 0.62) 100%) padding-box,
    linear-gradient(120deg, #2563eb 0%, #0ea5e9 55%, #06b6d4 100%) border-box;
  cursor: pointer;
  position: relative;
  overflow: hidden;
  transition: transform 0.22s ease, box-shadow 0.22s ease;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.9),
    0 4px 16px -6px rgba(37, 99, 235, 0.3);
}

/* hover 高光扫过 */
.deep-task::after {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: -70%;
  width: 45%;
  background: linear-gradient(105deg, transparent, rgba(255, 255, 255, 0.6), transparent);
  transform: skewX(-20deg);
  transition: left 0.55s ease;
  pointer-events: none;
}
.deep-task:hover::after {
  left: 130%;
}

.deep-task:hover {
  transform: translateY(-1px);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.95),
    0 8px 24px -6px rgba(37, 99, 235, 0.45);
}

.deep-task-icon {
  flex-shrink: 0;
  width: 26px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  font-size: 13px;
  line-height: 1;
  color: #fff;
  background: linear-gradient(135deg, #2563eb, #0891b2);
  text-shadow: 0 1px 2px rgba(13, 42, 110, 0.35);
  animation: deep-icon-breathe 3.4s ease-in-out infinite;
  transition: transform 0.25s ease;
  position: relative;
  z-index: 1;
}

.deep-task:hover .deep-task-icon {
  transform: rotate(30deg) scale(1.05);
}

@keyframes deep-icon-breathe {
  0%, 100% { box-shadow: 0 2px 8px -2px rgba(14, 116, 233, 0.45), inset 0 1px 0 rgba(255, 255, 255, 0.4); }
  50% { box-shadow: 0 2px 14px 0 rgba(14, 165, 233, 0.7), inset 0 1px 0 rgba(255, 255, 255, 0.4); }
}

.deep-task-label {
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 13.5px;
  font-weight: 700;
  letter-spacing: 0.01em;
  color: #1e3a8a;
  line-height: 1.15;
  min-width: 0;
  position: relative;
  z-index: 1;
}

/* 后缀「人机协作」弱化处理：字号降一档 + 去粗 + 同色相压淡，明确从属于主文案 */
.deep-task-label-dim {
  font-size: 12px;
  font-weight: 400;
  color: rgba(30, 58, 138, 0.38);
}

.deep-task-arrow {
  margin-left: auto;
  font-size: 15px;
  color: rgba(37, 99, 235, 0.5);
  transition: transform 0.3s ease, color 0.3s ease;
  position: relative;
  z-index: 1;
}

.deep-task:hover .deep-task-arrow {
  transform: translateX(4px);
  color: rgba(14, 116, 233, 0.9);
}

.sessions-nav {
  flex: 1;
  overflow-y: auto;
  margin: -4px -8px;
  padding: 4px 8px;
}

/* ─── 今日分隔线左侧的搜索按钮 ─── */
.group-search {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  padding: 0;
  flex-shrink: 0;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--ink-3);
  cursor: pointer;
  transition: all 0.18s ease;
}

.group-search svg {
  width: 13px;
  height: 13px;
}

.group-search:hover {
  color: var(--accent);
  background: var(--accent-soft);
  transform: scale(1.12);
}

.group-search:active {
  transform: scale(0.96);
}

.sessions-nav::-webkit-scrollbar {
  width: 5px;
}

.sessions-nav::-webkit-scrollbar-track {
  background: transparent;
}

.sessions-nav::-webkit-scrollbar-thumb {
  background: rgba(30, 64, 175, 0.12);
  border-radius: 4px;
}

.session-group {
  margin-bottom: 18px;
}

.session-group-label {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 4px 8px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  letter-spacing: 0.06em;
  color: var(--ink-3);
  font-weight: 600;
}

.session-group-toggle {
  cursor: pointer;
  user-select: none;
  transition: color 0.15s;
}
.session-group-toggle:hover {
  color: var(--ink-2);
}

/* ─── 收藏折叠头部 ─── */
.starred-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 4px 6px;
  padding: 5px 10px;
  cursor: pointer;
  user-select: none;
  border-radius: 8px;
  background: rgba(234, 179, 8, 0.06);
  border: 1px solid rgba(234, 179, 8, 0.12);
  transition: all 0.2s ease;
}
.starred-header:hover {
  background: rgba(234, 179, 8, 0.10);
  border-color: rgba(234, 179, 8, 0.20);
}
.starred-header-open {
  margin-bottom: 4px;
  background: transparent;
  border-color: transparent;
  padding-left: 0;
}
.starred-header-star {
  font-size: 12px;
  color: #eab308;
  line-height: 1;
}
.starred-header-label {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.06em;
  color: var(--ink-3);
}
.starred-header-count {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  font-weight: 600;
  color: var(--ink-4);
}

.group-arrow {
  font-size: 9px;
  margin-left: auto;
  transition: transform 0.2s ease;
  display: inline-block;
  opacity: 0.5;
}
.group-arrow-open {
  transform: rotate(0deg);
}
.group-arrow:not(.group-arrow-open) {
  transform: rotate(-90deg);
}

.sg-collapse-enter-active { transition: all 0.2s ease; }
.sg-collapse-leave-active { transition: all 0.15s ease; }
.sg-collapse-enter-from, .sg-collapse-leave-to {
  opacity: 0;
  max-height: 0;
  overflow: hidden;
}
.sg-collapse-enter-to, .sg-collapse-leave-from {
  opacity: 1;
  max-height: 500px;
  overflow: hidden;
}

.group-line {
  flex: 1;
  height: 1px;
  background: rgba(30, 64, 175, 0.08);
}

.group-count {
  color: var(--ink-4);
  font-weight: 500;
  font-size: 8px;
}

.session-list {
  list-style: none;
  padding: 0;
  margin: 0;
}

.session-item {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 9px 12px;
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

.session-item:hover {
  background: rgba(30, 64, 175, 0.06);
  border-left-color: transparent;
}

.session-item.active {
  background: rgba(255, 255, 255, 0.62);
  border-left-color: transparent;
  color: var(--ink);
  font-weight: 600;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.95),
    0 1px 2px rgba(15, 23, 42, 0.04),
    0 4px 12px -4px rgba(30, 64, 175, 0.12);
}

.session-dot {
  width: 5px;
  height: 5px;
  background: var(--ink-4);
  border-radius: 50%;
  flex-shrink: 0;
  transition: background 0.18s;
}

.session-item.active .session-dot {
  background: #1e40af;
  box-shadow: 0 0 6px rgba(30, 64, 175, 0.3);
}

.session-title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  position: relative;
  z-index: 1;
}

.session-del {
  background: none;
  border: none;
  font-size: 16px;
  line-height: 1;
  color: var(--ink-4);
  cursor: pointer;
  padding: 2px 5px;
  border-radius: 6px;
  opacity: 0;
  transition: all 0.15s;
  position: relative;
  z-index: 1;
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
  background: none;
  border: none;
  font-size: 13px;
  line-height: 1;
  color: var(--ink-4);
  cursor: pointer;
  padding: 2px 4px;
  border-radius: 6px;
  opacity: 0;
  transition: all 0.15s;
  position: relative;
  z-index: 1;
}

.session-item:hover .session-star {
  opacity: 0.6;
}

.session-star:hover {
  color: #f5a623;
  opacity: 1 !important;
}

.session-star-on {
  opacity: 1 !important;
  color: #f5a623;
}

.session-dot-star {
  color: #f5a623;
  font-size: 11px;
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

.sessions-empty {
  font-family: var(--font-display);
  font-size: 13px;
  color: var(--ink-3);
  font-style: italic;
  padding: 30px 12px;
  text-align: center;
}

.sidebar-foot {
  display: flex;
  align-items: center;
  padding-top: 8px;
  border-top: 1px solid rgba(30, 64, 175, 0.08);
}

/* 用户信息条撑满侧栏宽度，hover 区域与上方功能按钮对齐 */
.sidebar-foot > * {
  flex: 1;
  min-width: 0;
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
</style>
