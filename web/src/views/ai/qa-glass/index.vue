<script setup lang="ts">
import {computed, defineAsyncComponent, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch} from 'vue';
import {useRoute, useRouter} from 'vue-router';
import {marked} from 'marked';
import {brand} from '@/constants/brand';
import {getServiceBaseURL} from '@/utils/service';
import {downloadText, fileExt, fileExtGroup, formatFileSize, isCsvFile, isImageFile, isMarkdownFile, isOfficePreviewable, isVideoFile, sanitizeFilename} from '@/utils/attachment';
import {getBrandVariant} from '@/utils/brand-config';
import {useAuthStore} from '@/store/modules/auth';
import {localStg} from '@/utils/storage';
import ArtifactList from './components/artifact-list.vue';
import HtmlRender from './components/html-render.vue';
import QASidebar from './components/QASidebar.vue';
import QATopBar from './components/QATopBar.vue';
import QAComposer from './components/QAComposer.vue';
import AttachmentPreviewModal from '@/components/common/attachment-preview-modal.vue';
import SvgIcon from '@/components/custom/svg-icon.vue';
import TaskDrawer from './components/TaskDrawer.vue';
import SessionSearchModal from './components/SessionSearchModal.vue';
import OnboardingModal from './components/OnboardingModal.vue';
import ProfileModal from './components/ProfileModal.vue';
import SkillIntroModal from './components/skill/SkillIntroModal.vue';
import ProcessTimeline from './components/process-timeline.vue';
import SurveyCard from './components/survey-card.vue';
import WelcomeShowcase from './components/WelcomeShowcase.vue';
import { customIconHtml } from './components/skill/skill-icon';
import {
  type AgentArtifact,
  type AgentMessage as ApiMessage,
  type AgentSession as ApiSession,
  type AgentConnector,
  type AgentExpert,
  type AgentSkill,
  type AgentToolStep,
  type ProcessStep,
  type QuickAction,
  type QuickActionExample,
  type QuickActionGroup,
  fetchAgentMessages,
  fetchAgentSessions,
  fetchAgentConnectors,
  fetchAgentExperts,
  fetchAgentSkills,
  fetchCreateWorkflow,
  fetchListWorkflows,
  fetchTruncateAgentSession,
  fetchCreateAgentSession,
  fetchDeleteAgentSession,
  fetchDistillSkillStream,
  fetchKbSedimentSessionStream,
  fetchQAChatStream,
  fetchQAStop,
  fetchQuickActions,
  fetchExpertOnboarding,
  fetchCompleteExpertOnboarding,
  fetchUpdateAgentSession,
  fetchUploadFile,
  fetchDailyBriefStream,
  fetchForkQuickActionExample,
  fetchStandardBatchVerify,
  fetchChatModePref,
  fetchSetChatModePref,
  type DailyBriefEvent,
  type QAEvent,
  type WorkflowListItem
} from '@/service/api';
import StdDetailDrawer from '../standard-base-info/modules/std-detail-drawer.vue';
import {exportConversationAsImage} from './modules/export-conversation';
import {applyQaTheme} from './qa-theme';
import type {QaBridge} from '@/views/ai/workflow/modules/board-shell.vue';

// 伴侣面板（流程编排/应用制作挂进对话页）：懒加载，vue-flow+dagre 不进 QA 路由首屏 chunk
const BoardPanel = defineAsyncComponent(() => import('./components/BoardPanel.vue'));

const props = defineProps<{
  /** 嵌入抽屉时传入的预填文本（知识标题） */
  prefill?: string;
  /** 嵌入模式（抽屉内渲染）：禁用路由同步、全局快捷键、侧栏 */
  embedded?: boolean;
  /** 嵌入时指定加载的会话 key（工作流页面用） */
  sessionKey?: string;
  /** 用户当前在工作流画板打开的工作流 key（注入 Agent 上下文，让 Agent 知道"当前工作流"） */
  workflowKey?: string;
  /** 用户当前在画板选中的节点 id 列表（随消息发给 Agent 的上下文） */
  selectedNodeIds?: string[];
  /** 隐藏内置顶栏（工作流弹窗自带会话切换头部，避免双顶栏） */
  hideTopbar?: boolean;
}>();

marked.setOptions({breaks: true});

function escapeHtmlForCode(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

marked.use({
  hooks: {
    postprocess(html: string) {
      return html.replace(/<a (?![^>]*\btarget=)/g, '<a target="_blank" rel="noopener noreferrer" ');
    }
  },
  renderer: {
    code({text, lang}: {text: string; lang?: string; escaped?: boolean}) {
      const langLabel = (lang || '').trim();
      const langClass = langLabel ? ` class="language-${escapeHtmlForCode(langLabel)}"` : '';
      const langTag = langLabel
        ? `<span class="code-block-lang">${escapeHtmlForCode(langLabel)}</span>`
        : '';
      const body = escapeHtmlForCode(text);
      return `<div class="code-block">${langTag}<button type="button" class="code-copy-btn" aria-label="复制代码" title="复制代码"><span class="ccb-icon">⧉</span><span class="ccb-text">复制</span></button><pre><code${langClass}>${body}</code></pre></div>`;
    }
  }
});

// ───── Types ─────────────────────────────────────────────────────────────
type MessageAttachment = {
  name: string;
  path: string;
  size: number;
  isImage: boolean;
};

type Message = {
  id: number;
  serverId?: number | null;
  role: 'user' | 'assistant';
  content: string;
  contentHtml?: string;
  contentSegments?: ContentSegment[] | null;
  /** 过程时间线条目（dsh 两段式：过程时间线 + 结果；历史消息由 thinking/toolSteps 兼容转换而来） */
  process?: ProcessStep[];
  /** 时间线折叠态（done 后自动收起；aborted/error 保持展开） */
  processCollapsed?: boolean;
  /** 回合时长（首个 process 事件 → done，摘要行展示） */
  processDurationMs?: number | null;
  /** 首个 process 事件到达时刻（内部计时用） */
  processStartedAt?: number;
  loading?: boolean;
  currentTool?: string;
  /** 打开中的尾部 text 条目 id（正由结果区流式渲染，时间线隐藏它） */
  openTextId?: string | null;
  error?: string | null;
  /** 用户主动停止（非异常，中性提示） */
  stopped?: boolean;
  artifacts?: AgentArtifact[];
  attachments?: MessageAttachment[];
};

// ───── Skills (from backend) ─────────────────────────────────────────────
const skills = ref<AgentSkill[]>([]);

async function reloadSkills() {
  // 商店模型：@ 候选 = 已上架 或 本人创建的未上架技能（include_disabled=false 即统一尺子，
  // 后端 list_skills 默认分支返回）；个人「未添加/禁用」的再在 filteredSkills 里过滤
  const {data, error} = await fetchAgentSkills(false);
  if (!error && data) skills.value = data;
}

// ───── 连接器（MCP）：底栏在用指示 + 面板数据源 ─────────────────────────────
const connectors = ref<AgentConnector[]>([]);

async function loadConnectors() {
  // include_disabled=true：面板上架管理页需要未上架行；底栏在用集由 computed 过滤
  const {data, error} = await fetchAgentConnectors(true);
  if (!error && data) connectors.value = data;
}

/** 我的连接器（composer 图标堆展示）：已添加 且（已上架 或 本人创建）；
 * 禁用态也在列表里（弹层内可重新启用），但圆片堆只渲染启用态，全禁用时塌成纯插头入口 */
const activeConnectors = computed(() => {
  const uid = authStore.userInfo?.userId;
  const myUid = uid != null && uid !== '' ? Number(uid) : null;
  return connectors.value.filter(
    c => c.isAdded && (c.isEnabled || (myUid != null && c.userId === myUid))
  );
});

/** 技能/连接器/专家面板内任何数据变更：三侧候选一并刷新 */
function onSkillPanelChange() {
  reloadSkills();
  loadConnectors();
  reloadExperts();
}

// ───── 个人知识库 ─────────────────────────────────────────────────────────
const kbToast = ref<{visible: boolean; message: string; tone: 'info' | 'ok' | 'err'}>({
  visible: false,
  message: '',
  tone: 'info',
});
let kbToastTimer: ReturnType<typeof setTimeout> | null = null;
const sedimentingKb = ref(false);
const sedimentMenuOpen = ref(false);

function closeSedimentMenu() {
  sedimentMenuOpen.value = false;
}

function onPickSediment(kind: 'kb' | 'skill') {
  sedimentMenuOpen.value = false;
  if (kind === 'kb') handleSedimentSession();
  else handleDistill();
}

// v-click-outside 简易指令：点击元素外部关闭
const vClickOutside = {
  mounted(el: HTMLElement, binding: { value: () => void }) {
    (el as any).__clickOutsideHandler__ = (e: MouseEvent) => {
      if (!el.contains(e.target as Node)) binding.value();
    };
    document.addEventListener('click', (el as any).__clickOutsideHandler__);
  },
  unmounted(el: HTMLElement) {
    document.removeEventListener('click', (el as any).__clickOutsideHandler__);
  }
};

function showKbToast(message: string, tone: 'info' | 'ok' | 'err' = 'info', durationMs = 3200) {
  kbToast.value = {visible: true, message, tone};
  if (kbToastTimer) clearTimeout(kbToastTimer);
  kbToastTimer = setTimeout(() => {
    kbToast.value.visible = false;
  }, durationMs);
}

async function handleSedimentSession() {
  if (sedimentingKb.value) return;
  if (!currentSessionKey.value) {
    showKbToast('当前任务还没生成，再聊几句吧', 'err');
    return;
  }
  if (messages.value.length < 2) {
    showKbToast('任务太短，没什么可沉淀的', 'err');
    return;
  }
  sedimentingKb.value = true;
  showKbToast('已交给 AI 整理…', 'info');
  try {
    await fetchKbSedimentSessionStream(
      {session_key: currentSessionKey.value},
      ev => {
        if (ev.type === 'done') {
          const result = ev.result;
          if (!result?.candidates) {
            showKbToast(result?.summary || '本次任务没什么可记的', 'info');
            return;
          }
          const titles = (result.results || [])
            .filter(r => r.action !== 'skipped')
            .map(r => r.title)
            .filter(Boolean);
          const tip = titles.length
            ? `已记下「${titles.slice(0, 2).join('」「')}」${titles.length > 2 ? ` 等 ${titles.length} 条` : ''}`
            : `已记下 ${result.candidates} 条`;
          showKbToast(tip, 'ok', 3000);
        } else if (ev.type === 'quota_exceeded') {
          showKbToast(ev.message || '积分余额不足，请联系管理员', 'err');
        } else if (ev.type === 'error') {
          showKbToast(`整理失败：${ev.message || '未知错误'}`, 'err');
        }
      }
    );
  } catch (err: any) {
    if (err?.name !== 'AbortError') {
      showKbToast(`整理失败：${err?.message || '请求失败'}`, 'err');
    }
  } finally {
    sedimentingKb.value = false;
  }
}

// ───── State ─────────────────────────────────────────────────────────────
const sessions = ref<ApiSession[]>([]);
const currentSessionKey = ref<string>('');

// ─── 标准编号点击 ────────────────────────────────────
const showStdDetail = ref(false);
const taskDrawerOpen = ref(false);
const selectedStdId = ref('');
const stdNoCache = new Map<string, {id: string; exists: boolean}>();

// 按 sessionKey 隔离的消息容器，让流式请求即使切走会话也能继续往原会话写
const sessionMessages = reactive<Record<string, Message[]>>({});
// 正在拉取历史的 sessionKey 集合：加载期间抑制首屏渲染，避免慢切换时闪现 welcome
const loadingKeys = reactive(new Set<string>());
const draftMessages = ref<Message[]>([]);
const sessionMsgIdCounter = reactive<Record<string, number>>({});
let draftMsgIdCounter = 0;

const messages = computed<Message[]>(() => {
  const key = currentSessionKey.value;
  if (!key) return draftMessages.value;
  if (!sessionMessages[key]) sessionMessages[key] = [];
  return sessionMessages[key];
});

function getMessageList(key: string | null | undefined): Message[] {
  if (!key) return draftMessages.value;
  if (!sessionMessages[key]) sessionMessages[key] = [];
  return sessionMessages[key];
}

function setMessageList(key: string | null | undefined, list: Message[]) {
  if (!key) {
    draftMessages.value = list;
    draftMsgIdCounter = list.reduce((mx, x) => Math.max(mx, x.id), 0);
    return;
  }
  sessionMessages[key] = list;
  sessionMsgIdCounter[key] = list.reduce((mx, x) => Math.max(mx, x.id), 0);
}

function nextMsgId(key: string | null | undefined): number {
  if (!key) {
    draftMsgIdCounter += 1;
    return draftMsgIdCounter;
  }
  sessionMsgIdCounter[key] = (sessionMsgIdCounter[key] || 0) + 1;
  return sessionMsgIdCounter[key];
}

const inputText = ref('');

// 按会话保留输入草稿：切会话时先把当前输入框存进对应 key（''=新会话草稿态），再恢复目标会话的草稿
const inputDrafts = reactive<Record<string, string>>({});
function switchInputDraft(toKey: string) {
  inputDrafts[currentSessionKey.value || ''] = inputText.value;
  inputText.value = inputDrafts[toKey || ''] ?? '';
}

/** 嵌入抽屉时，通过 prefill prop 预填输入框（仅响应变化，初始值由 onMounted 处理） */
watch(() => props.prefill, async (val) => {
  if (val) {
    await startNewSession();
    inputText.value = val;
    nextTick(() => composerRef.value?.focus());
  }
});

const sidebarOpen = ref(!props.embedded);
const scrollEl = ref<HTMLElement | null>(null);
const composerRef = ref<{
  focus: () => void;
  setCaretPos: (pos: number) => void;
  resetHeight: () => void;
  openSkillPanel: (tab?: 'expert' | 'skill' | 'connector' | 'dataset') => void;
  readonly selectionStart: number | null;
} | null>(null);

// ───── 对话模式偏好（模式 × 思考强度，所有用户可选；composer 切换钮数据源） ─────
const chatModePref = ref<Api.AI.ChatModePref | null>(null);

async function loadChatModePref() {
  const {data, error} = await fetchChatModePref();
  if (!error && data) chatModePref.value = data;
}

/** 模式/强度切换统一入口：PUT 成功后本地静默更新（不弹提示——高频微调操作，
    弹层内的选中态/滑块位就是反馈）；后端已弹出旧 agent 实例，
    下一条消息按新形态重建（新运行时会话 + DB 历史注入续接） */
async function applyChatModePref(patch: Api.AI.ChatModePrefUpdate) {
  const {error} = await fetchSetChatModePref(patch);
  if (error) return; // 失败提示由全局请求拦截器统一弹出（后端 msg）
  if (patch.mode && patch.mode !== chatModePref.value?.mode) {
    // 切模式必须重拉 GET：新档 = 后端按「当前存档位 × 新模式有效块白名单」平滑迁移的
    // 权威结果。本地快照 modes[].levelDefault 是页面加载时的陈旧值（之后拖过滑块
    // 不会刷新），直接拿来当滑块位会出现「从 none 跳到 max」的错档
    await loadChatModePref();
    return;
  }
  if (chatModePref.value) {
    chatModePref.value = {...chatModePref.value, ...patch};
  }
}

// 按 sessionKey 隔离的单条流 abort 句柄；草稿态暂存到 '' key，发出后迁移到真实 sessionKey
const activeChatAborts = reactive<Record<string, AbortController>>({});
const runningSessions = reactive<Record<string, boolean>>({});

const running = computed<boolean>(() => !!runningSessions[currentSessionKey.value || '']);

function setRunning(key: string, value: boolean) {
  if (value) runningSessions[key] = true;
  else delete runningSessions[key];
}

// ───── 问题轨道（minimap）─────────────────────────────────────────────
const activeQuestionId = ref<number | null>(null);
const trackHover = ref(false);
const railEl = ref<HTMLElement | null>(null);

function handleRailEnter() {
  trackHover.value = true;
  scrollRailToActiveQuestion();
}

// 浮出时把 rail 定位到当前正在看的问题（居中）：rail 是滚动容器，scrollTop
// 会一直停留在上次收折前的位置，对话多时浮出看到的和当前上下文对不上，反直觉
function scrollRailToActiveQuestion() {
  nextTick(() => {
    const rail = railEl.value;
    if (!rail) return;
    const tick = rail.querySelector<HTMLElement>('.qa-track-tick.active');
    if (!tick) return;
    const top = tick.offsetTop - (rail.clientHeight - tick.offsetHeight) / 2;
    rail.scrollTo({top: Math.max(0, top), behavior: 'auto'});
  });
}

// active 刻度必须始终留在 rail 可视区内（收起态也要能看到当前位置）：
// 切换会话 / 新问题到来都会改 activeQuestionId，但 rail 的 scrollTop 不跟着走，
// 高亮点会被"藏"出可视区（长会话切换时尤其明显），必须 hover 才纠正。
// 只在越出可视区时滚动，展开态手动滚 rail 时不强行拽回
function keepActiveTickVisible() {
  nextTick(() => {
    const rail = railEl.value;
    if (!rail) return;
    const tick = rail.querySelector<HTMLElement>('.qa-track-tick.active');
    if (!tick) return;
    const viewTop = rail.scrollTop;
    const viewBottom = viewTop + rail.clientHeight;
    const tickTop = tick.offsetTop;
    if (tickTop >= viewTop && tickTop + tick.offsetHeight <= viewBottom) return;
    const top = tickTop - (rail.clientHeight - tick.offsetHeight) / 2;
    rail.scrollTo({top: Math.max(0, top), behavior: 'auto'});
  });
}

watch(activeQuestionId, keepActiveTickVisible);

const questionList = computed(() =>
  messages.value
    .filter(m => m.role === 'user')
    .map((m, idx) => ({
      id: m.id,
      seq: idx + 1,
      text: (m.content || '').trim() || '（空）'
    }))
);

function jumpToQuestion(id: number) {
  if (!scrollEl.value) return;
  const el = scrollEl.value.querySelector<HTMLElement>(`[data-msg-id="${id}"]`);
  if (!el) return;
  const top = el.offsetTop - 24;
  scrollEl.value.scrollTo({top, behavior: 'smooth'});
  activeQuestionId.value = id;
}

function updateActiveQuestion() {
  if (!scrollEl.value) return;
  const list = questionList.value;
  if (!list.length) {
    if (activeQuestionId.value !== null) activeQuestionId.value = null;
    return;
  }
  // 贴底 = 焦点必在最新一轮：刚发出的问题 / 流式跟随期间，新问题头顶压着上一条
  // 长回答，其 top 长期停在判定线下方，只走判定线会一直误认成上一条问题
  if (isNearBottom()) {
    const lastId = list[list.length - 1].id;
    if (lastId !== activeQuestionId.value) activeQuestionId.value = lastId;
    return;
  }
  const containerTop = scrollEl.value.getBoundingClientRect().top;
  const probe = containerTop + 80; // 视为"已看到"的判定线
  const nodes = scrollEl.value.querySelectorAll<HTMLElement>('[data-msg-id]');
  let current: number | null = null;
  for (const n of Array.from(nodes)) {
    const r = n.getBoundingClientRect();
    if (r.top <= probe) {
      const id = Number(n.dataset.msgId);
      if (!Number.isNaN(id)) current = id;
    } else break;
  }
  if (current === null) current = list[0].id;
  if (current !== activeQuestionId.value) activeQuestionId.value = current;
}

// handleFeedScroll 见下面「流式跟随滚动」一节统一管理

// ───── 流式跟随滚动 ─────────────────────────────────────────────────────
// 三个状态分工：
//   followBottom         — 用户当前是否贴底（按距离阈值；手势驱动）。
//   turnSkipped          — 本轮是否已经放弃跟随（被 anchor 让位 / 用户手动滚开）。
//                          用户滑回贴底 / 点底部按钮 / 新一轮 都会清零。
//   anchorCheckExhausted — 本轮 anchor 让位检查是否已用过。
//                          关键：一旦让位过一次，本轮就别再 anchor 检查了——
//                          否则用户滑回贴底重新进入跟随，下一片内容立刻又
//                          跑 anchor 检查，发现 anchor 早就跑出顶部，立即又
//                          turnSkipped=true，循环卡死。
//                          只在新一轮（scrollToBottom）清零；按钮点击也置 true，
//                          含义：「用户主动选择了无脑跟到底」。
const NEAR_BOTTOM_THRESHOLD_DESKTOP = 32;
const NEAR_BOTTOM_THRESHOLD_MOBILE = 28;
const followBottom = ref(true);
const turnSkipped = ref(false);
const anchorCheckExhausted = ref(false);
// 程序化滚动锁：smooth scroll 在飞的几百毫秒里 scroll 事件会一路抛出，handleFeedScroll
// 每拍读 isNearBottom() 都会拿到"还没到底"的中间值，把 followBottom 翻回 false——
// 等动画落地时间窗内若有新内容进来，followScrollIfNeeded 看到 shouldFollow=false 就
// 跳过跟随，导致按钮"点了没用"。这里用一个时间窗忽略本期间的 scroll 评估。
let programmaticScrollUntil = 0;
let userScrollIntentUntil = 0;

// 流式跟随的实际开关 = 贴底 && 本轮未让位
const shouldFollow = computed(() => followBottom.value && !turnSkipped.value);

function nearBottomThreshold(): number {
  return isMobile.value ? NEAR_BOTTOM_THRESHOLD_MOBILE : NEAR_BOTTOM_THRESHOLD_DESKTOP;
}

function isNearBottom(): boolean {
  const el = scrollEl.value;
  if (!el) return true;
  return el.scrollHeight - el.scrollTop - el.clientHeight <= nearBottomThreshold();
}

// 当前轮的首行节点：取容器里最后一个带 data-msg-id 的 user message。
function currentTurnAnchor(): HTMLElement | null {
  const el = scrollEl.value;
  if (!el) return null;
  const nodes = el.querySelectorAll<HTMLElement>('[data-msg-id]');
  return nodes.length ? nodes[nodes.length - 1] : null;
}

// 假设这一拍跟随到底（scrollTop = scrollHeight - clientHeight）后，user message
// 是否会被完全推到容器顶上方 + 上面再压一行余量。判据保守一些：anchor 整个还
// 完整在视野里、且头顶留出至少一行高度的呼吸位时才允许继续跟。
function turnAnchorWouldEscape(): boolean {
  const el = scrollEl.value;
  const anchor = currentTurnAnchor();
  if (!el || !anchor) return false;
  const containerTop = el.getBoundingClientRect().top;
  const anchorRect = anchor.getBoundingClientRect();
  const wouldScrollMore = el.scrollHeight - el.scrollTop - el.clientHeight;
  const anchorBottomAfter = (anchorRect.bottom - containerTop) - wouldScrollMore;
  return anchorBottomAfter < anchorRect.height + 30;
}

function handleFeedScroll() {
  // 程序化滚动期间的 scroll 事件不参与"是否贴底"评估——动画中间值会误把
  // followBottom 翻回 false，导致按钮点击立刻又被踢出跟随。
  if (Date.now() < programmaticScrollUntil) {
    updateActiveQuestion();
    return;
  }
  followBottom.value = isNearBottom();
  // 用户手动滑回贴底 → 重新进入跟随。anchorCheckExhausted 不动：
  // 让"已经让位过"在本轮始终保持，避免下一片内容来又触发让位循环。
  if (followBottom.value && turnSkipped.value) {
    turnSkipped.value = false;
  }
  updateActiveQuestion();
}

function handleFeedWheel(e: WheelEvent) {
  // 用户主动向上滚时，立刻断开自动跟随，不等 scroll 事件异步触发
  if (e.deltaY < 0 && Date.now() >= programmaticScrollUntil) {
    followBottom.value = false;
    userScrollIntentUntil = Date.now() + 3000;
  }
}

let touchStartY = 0;
function handleFeedTouchStart(e: TouchEvent) {
  touchStartY = e.touches[0]?.clientY ?? 0;
}
function handleFeedTouchMove(e: TouchEvent) {
  const dy = (e.touches[0]?.clientY ?? 0) - touchStartY;
  if (dy > 0 && Date.now() >= programmaticScrollUntil) {
    followBottom.value = false;
    userScrollIntentUntil = Date.now() + 3000;
  }
}

function followScrollIfNeeded() {
  if (!shouldFollow.value) return;
  // 只有本轮 anchor 检查还没用过时才检查；用过一次就别再问了
  if (!anchorCheckExhausted.value && turnAnchorWouldEscape()) {
    turnSkipped.value = true;
    anchorCheckExhausted.value = true;
    return;
  }
  nextTick(() => {
    const el = scrollEl.value;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  });
}

function scrollFeedToBottom(smooth = false) {
  // smooth 滚一般 ~300ms 完成；给 700ms 余量足够覆盖移动端慢一点的实现。
  if (smooth) programmaticScrollUntil = Date.now() + 700;
  nextTick(() => {
    const el = scrollEl.value;
    if (!el) return;
    if (smooth) el.scrollTo({top: el.scrollHeight, behavior: 'smooth'});
    else el.scrollTop = el.scrollHeight;
    followBottom.value = true;
    turnSkipped.value = false;
    // 按钮点击：用户主动选择无脑跟到底，本轮不再做 anchor 检查
    anchorCheckExhausted.value = true;
  });
}

// 提供给底部按钮：一键回到底 + 重置跟随
function jumpToBottomAndFollow() {
  scrollFeedToBottom(true);
}

// ───── 标准编号检测 & 链接化 ──────────────────────────────────────────────
// 匹配中国/国际标准编号：GB/T 12345-2020、GJB 1234、ISO 9001 等
// 标准号正则：通用匹配 2-5个大写字母（+可选/后缀）+ 数字，排除常见误匹配词
// 连接号/斜杠/小数点/数字兼容全半角变体：LLM 输出常混入 en dash –、em dash —、
// 全角连字符 －、减号 −、全角斜杠 ／、全角数字 ５２７７ 等肉眼难以分辨的字符（详见 normalizeStdNo）
const STD_NO_RE = /(?<![A-Za-z/])((?:(?!(?:HTML|HTTP|HTTPS|UUID|CSS|XML|JSON|API|SQL|URL|RGB|ISBN|PDF|SVG|PNG|CPU|GPU|LED|LCD|RFC|IT\b|PC\b))[A-Z]{2,5})(?:[/／][A-Z]+)?\s?[0-9０-９]+(?:[.．][0-9０-９]+)*(?:\s*[-–—―－−‑]\s*[0-9０-９]{4})?|T[/／][A-Z]+\s?[0-9０-９]+(?:[.．][0-9０-９]+)*(?:\s*[-–—―－−‑]\s*[0-9０-９]{4})?)/g;

// 将标准编号归一化为规范形式（如 QB/T 5277-2018）：
// 全角数字/斜杠/小数点 → 半角，各种连字符变体 → 半角连字符，
// 不间断空格/全角空格/零宽字符 → 普通空格并折叠，去掉连字符两侧空格。
// 后端 batch-verify 对 standard_no 精确匹配，查询/缓存 key 必须先归一化，
// 否则同一编号会因字形差异查不到（显示上也顺带完成自动修正）。
function normalizeStdNo(raw: string): string {
  return raw
    .trim()
    .replace(/[０-９]/g, c => String.fromCharCode(c.charCodeAt(0) - 0xFEE0)) // ０-９ → 0-9
    .replace(/／/g, '/') // ／ → /
    .replace(/．/g, '.') // ．→ .
    .replace(/[‐-―−－]/g, '-') // ‐‑‒–—―−－ → -
    .replace(/\s+/g, ' ') // NBSP/全角空格等统一折叠为普通空格（JS 的 \s 均已覆盖）
    .replace(/\s*-\s*/g, '-');
}

async function linkifyStandardNos(html: string): Promise<string> {
  // 同步版：直接包裹标准号，不做异步验证
  // 1. 先保护 <code>/<pre>/<a> 标签内容，避免替换内部文本
  const protectedBlocks: string[] = [];
  let safe = html.replace(/<(code|pre|a)\b[^>]*>[\s\S]*?<\/\1>/gi, (m) => {
    protectedBlocks.push(m);
    return `\x00PROTECTED_${protectedBlocks.length - 1}\x00`;
  });

  // 2. 替换为标准号 span（全部强制可点击），文本与 data 属性均用归一化后的编号
  STD_NO_RE.lastIndex = 0;
  safe = safe.replace(STD_NO_RE, (_match, raw: string) => {
    const stdNo = normalizeStdNo(raw);
    return `<span class="std-no-link std-no-active" data-std-no="${stdNo}">${stdNo}</span>`;
  });

  // 3. 还原被保护的标签
  safe = safe.replace(/\x00PROTECTED_(\d+)\x00/g, (_m, idx) => protectedBlocks[Number(idx)]);

  // 4. 后台验证存在性，更新 DOM 和缓存（不阻塞渲染）
  const toCheck = new Set<string>();
  STD_NO_RE.lastIndex = 0;
  let m2: RegExpExecArray | null;
  while ((m2 = STD_NO_RE.exec(safe)) !== null) {
    toCheck.add(normalizeStdNo(m2[1]));
  }
  // 过滤出未命中缓存的编号，一次批量请求替代 N 次单查
  const uncached = [...toCheck].filter(stdNo => !stdNoCache.has(stdNo));
  if (uncached.length > 0) {
    fetchStandardBatchVerify(uncached).then(({data, error}) => {
      if (error || !data) return;
      for (const stdNo of uncached) {
        const entry = data[stdNo];
        if (entry?.exists) {
          stdNoCache.set(stdNo, {id: entry.id, exists: true});
        } else {
          stdNoCache.set(stdNo, {id: '', exists: false});
        }
        // 更新 DOM 中对应 span 的 data-std-id
        const spans = document.querySelectorAll<HTMLElement>(`.std-no-active[data-std-no="${stdNo}"]`);
        const cached = stdNoCache.get(stdNo);
        if (cached?.id) {
          spans.forEach(s => { s.dataset.stdId = cached.id; });
        } else {
          // 不存在则移除可点击样式
          spans.forEach(s => s.classList.remove('std-no-active'));
        }
      }
    }).catch(() => {
      uncached.forEach(stdNo => stdNoCache.set(stdNo, {id: '', exists: false}));
    });
  }

  return safe;
}

// ───── 流式渲染（直接 append，无节流）────────────────────────────────
// 历史上手机端做过打字机节流，体验上更像演示而不是工具，已移除。后端来一片
// 渲染一片，靠 LLM 自身节奏走。
function appendChunkSmooth(msg: Message, chunk: string) {
  if (!chunk) return;
  msg.content = (msg.content || '') + chunk;
  extractAndRefreshInline(msg);
  msg.contentHtml = marked.parse(stripArtifactMarkers(msg.content, true)) as string;
}

// ───── 过程时间线（dsh 两段式）─────────────────────────────────────
// D2 无跳动设计：流式期未闭合的尾部 text 直接渲染在结果区（由 openTextId 驱动）；
// tool_call 到达 = 该条目定性为叙述，留在时间线，结果区重算为空（无字符串回滚）；
// done 到达 = 无搬移，被提升的条目从时间线剔除并折叠为摘要行。
type ProcessEvent = Extract<QAEvent, { type: 'process' }>;

/** 正文分段占位符：[artifact:ID]（产物）与 [questionnaire:qid]（问卷）两类 */
const SEG_MARKER_RE = /^\[(?:artifact:(-?\d+)|questionnaire:(qn\d+))\]$/gm;
/** 流式 text 增量里出现问卷占位符即需重建分段 */
const QN_INLINE_RE = /^\[questionnaire:qn\d+\]$/m;

function ensureProcessItem(msg: Message, id: string, kind: ProcessStep['kind']): ProcessStep {
  if (!msg.process) msg.process = [];
  let it = msg.process.find(i => i.id === id);
  if (!it) {
    it = {id, kind};
    msg.process.push(it);
  }
  return it;
}

function handleProcessEvent(msg: Message, event: ProcessEvent) {
  if (msg.processStartedAt == null) msg.processStartedAt = Date.now();
  if (!msg.process) msg.process = [];

  switch (event.kind) {
    case 'reasoning': {
      // 后端已攒批（0.4s），这里按 item_id 追加即可
      const it = ensureProcessItem(msg, event.item_id, 'reasoning');
      it.content = (it.content || '') + event.content;
      break;
    }
    case 'text': {
      const it = ensureProcessItem(msg, event.item_id, 'text');
      if (event.replace) it.content = event.content;
      else it.content = (it.content || '') + event.content;
      // 未闭合尾部 text 驱动结果区（与旧 answer_chunk 体验一致）
      msg.openTextId = event.item_id;
      msg.content = it.content || '';
      extractAndRefreshInline(msg);
      msg.contentHtml = msg.content ? (marked.parse(stripArtifactMarkers(msg.content, true)) as string) : '';
      // 问卷占位符出现即重建分段（卡片随流式就位；无占位符时不打扰纯文本流）
      if (QN_INLINE_RE.test(msg.content)) refreshContentSegments(msg);
      msg.currentTool = '';
      break;
    }
    case 'tool_call': {
      // 定性：未闭合尾部 text 转叙述（条目已在时间线）→ 结果区清空重算
      msg.openTextId = null;
      msg.content = '';
      msg.contentHtml = '';
      msg.contentSegments = null;
      msg.process.push({
        id: event.item_id,
        kind: 'tool_call',
        tool: event.tool,
        tool_display: event.tool_display,
        args: event.args,
        is_subagent: event.is_subagent,
        in_subagent: event.in_subagent
      });
      // Agent 操作某块板（板工具带 workflow_key）：自动挂板伸出面板
      autoAttachBoardFromTool(event.tool || '', event.args || {});
      msg.currentTool = event.tool_display || event.tool;
      break;
    }
    case 'tool_result': {
      msg.process.push({
        id: event.item_id,
        kind: 'tool_result',
        tool: event.tool,
        tool_display: event.tool_display,
        content: event.content,
        is_error: event.is_error,
        in_subagent: event.in_subagent
      });
      // Agent 新建了板：结果里带回新板 key——挂板伸面板（建板唤起的落点）
      autoAttachBoardFromCreateResult(event.tool || '', event.content || '');
      msg.currentTool = '';
      break;
    }
    case 'todo': {
      // 固定 id：整快照原位替换，避免长任务清单刷屏
      const it = ensureProcessItem(msg, event.item_id, 'todo');
      it.todos = event.todos;
      break;
    }
    case 'questionnaire': {
      // 问卷载荷入 process（与落库同形）；正文 [questionnaire:<id>] 占位符驱动结果区分段渲染
      const it = ensureProcessItem(msg, event.item_id, 'questionnaire');
      it.questions = event.questions;
      break;
    }
    case 'compaction': {
      msg.process.push({id: event.item_id, kind: 'compaction'});
      break;
    }
    case 'reset': {
      // in-band 重试 / 升代续跑：后端清空一切累积，前端同步清场
      msg.process = [];
      msg.openTextId = null;
      msg.content = '';
      msg.contentHtml = '';
      msg.contentSegments = null;
      msg.processStartedAt = Date.now();
      break;
    }
  }
}

watch(
  () => questionList.value.length,
  () => nextTick(updateActiveQuestion)
);

// ───── 附件上传 ─────────────────────────────────────────────────────────
interface AttachedFile {
  id: string;
  name: string;
  path: string;
  size: number;
  uploading: boolean;
  progress: number;
  error: string;
  /** 图片本地预览 URL（object URL），选择即可回显，无需等上传完成 */
  previewUrl?: string;
}

const attachedFiles = ref<AttachedFile[]>([]);
const fileInputEl = ref<HTMLInputElement | null>(null);
const dragOver = ref(false);
let dragCounter = 0;

function triggerFileInput() {
  fileInputEl.value?.click();
}

async function handleFileSelect(e: Event) {
  const input = e.target as HTMLInputElement;
  if (input.files) {
    await uploadFiles(Array.from(input.files));
    input.value = '';
  }
}

function handleDragOver(e: DragEvent) {
  e.preventDefault();
  dragCounter++;
  dragOver.value = true;
}

function handleDragLeave() {
  dragCounter--;
  if (dragCounter <= 0) {
    dragCounter = 0;
    dragOver.value = false;
  }
}

async function handleDrop(e: DragEvent) {
  e.preventDefault();
  dragCounter = 0;
  dragOver.value = false;
  if (e.dataTransfer?.files?.length) {
    await uploadFiles(Array.from(e.dataTransfer.files));
  }
}

async function handlePaste(e: ClipboardEvent) {
  const items = e.clipboardData?.items;
  if (!items) return;
  const files: File[] = [];
  for (let i = 0; i < items.length; i++) {
    const item = items[i];
    if (item.kind === 'file') {
      const file = item.getAsFile();
      if (file) files.push(file);
    }
  }
  if (files.length > 0) {
    e.preventDefault();
    await uploadFiles(files);
  }
}

function buildAttachmentUrl(path: string): string {
  const isHttpProxy = import.meta.env.DEV && import.meta.env.VITE_HTTP_PROXY === 'Y';
  const {baseURL} = getServiceBaseURL(import.meta.env, isHttpProxy);
  const uid = authStore.userInfo.userId;
  const uidPart = uid ? `&user_id=${encodeURIComponent(uid)}` : '';
  return `${baseURL}/ai/agent/uploads/download?path=${encodeURIComponent(path)}${uidPart}`;
}

async function uploadFiles(files: File[]) {
  if (!currentSessionKey.value) {
    const {data, error} = await fetchCreateAgentSession();
    if (error || !data) {
      window.$message?.error?.('创建任务失败，无法上传文件');
      return;
    }
    sessions.value.unshift(data);
    currentSessionKey.value = data.sessionKey;
  }
  for (const file of files) {
    const item: AttachedFile = {
      id: `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
      name: file.name,
      path: '',
      size: file.size,
      uploading: true,
      progress: 0,
      error: '',
      previewUrl: isImageFile(file.name) ? URL.createObjectURL(file) : undefined
    };
    attachedFiles.value.push(item);
    const idx = attachedFiles.value.length - 1;
    try {
      const result = await fetchUploadFile(file, currentSessionKey.value, pct => {
        attachedFiles.value[idx].progress = pct;
      });
      attachedFiles.value[idx].path = result.path;
      attachedFiles.value[idx].uploading = false;
      attachedFiles.value[idx].progress = 100;
    } catch (err: any) {
      attachedFiles.value[idx].uploading = false;
      attachedFiles.value[idx].error = err?.message || '上传失败';
    }
  }
}

function removeAttachment(id: string) {
  const target = attachedFiles.value.find(f => f.id === id);
  if (target?.previewUrl) URL.revokeObjectURL(target.previewUrl);
  attachedFiles.value = attachedFiles.value.filter(f => f.id !== id);
}

// ───── 附件统一预览（共享组件 attachment-preview-modal，与工作流画板复用） ─────
const attPreview = ref<{name: string; src: string} | null>(null);

function openAttachmentPreview(att: {name: string; path: string}) {
  attPreview.value = {name: att.name, src: buildAttachmentUrl(att.path)};
}

// 输入框附件预览：图片由 composer 内的 n-image 直接放大，这里只处理文件类（与对话同一套逻辑/组件）
function handleAttachmentPreview(att: AttachedFile) {
  if (att.uploading || att.error || !att.path) return;
  if (isMarkdownFile(att.name) || isOfficePreviewable(att.name) || isCsvFile(att.name) || isVideoFile(att.name)) openAttachmentPreview(att);
}

// 通用图片放大预览（案例大图等）
const imgLightbox = ref<{ visible: boolean; src: string; name: string }>({ visible: false, src: '', name: '' });

function openImgLightbox(src: string, name: string) {
  imgLightbox.value = { visible: true, src, name };
}

function closeImgLightbox() {
  imgLightbox.value.visible = false;
}

const skillPopupOpen = ref(false);
const skillQuery = ref('');
const skillActiveIndex = ref(0);
let skillTriggerPos = -1;

const filteredSkills = computed(() => {
  const q = skillQuery.value.trim().toLowerCase();
  // 商店模型：@ 候选 = 已添加 且 启用 的技能（未添加/禁用的完全剔除，与后端 @ 注入口径一致）
  const list = skills.value.filter(s => s.isAdded && s.userEnabled);
  const matched = q
    ? list.filter(
        s =>
          s.skillKey.toLowerCase().includes(q) ||
          s.name.toLowerCase().includes(q) ||
          (s.description ?? '').toLowerCase().includes(q)
      )
    : [...list];
  // 排序与技能商店（SkillListView）一致：已上架置顶 > 我的技能 > 他人共享；稳定排序保留组内原序
  // （authStore 声明在文件后段，这里就近取 store 实例避免 use-before-define）
  const myUid = useAuthStore().userInfo?.userId;
  const rank = (s: AgentSkill) => (s.isEnabled ? 0 : myUid != null && s.userId != null && String(s.userId) === String(myUid) ? 1 : 2);
  return matched.sort((a, b) => rank(a) - rank(b));
});



// abort/msgId 改为按 sessionKey 隔离，已迁移到 activeChatAborts / sessionMsgIdCounter

const currentSession = computed(() => sessions.value.find(s => s.sessionKey === currentSessionKey.value));

// 侧栏任务列表已扁平化（不再按今日/昨日/更早分组）：这里只挑出置顶（is_starred）任务，
// 其余任务由 QASidebar 基于 sessions 按 updatedAt 倒序自行铺排。
const groupedSessions = computed(() => ({
  starred: [...sessions.value].sort((a, b) => b.updatedAt - a.updatedAt).filter(s => s.isStarred)
}));

// ───── Session management ───────────────────────────────────────────────
async function reloadSessions() {
  // 工作流画板嵌入模式：只拉本工作流的会话（下拉列表与默认加载同口径）
  const {data, error} = await fetchAgentSessions(200, props.workflowKey ? {workflowKey: props.workflowKey} : undefined);
  if (!error && data) sessions.value = data;
}

// ───── 搜索历史对话 ─────────────────────────────────────────────────────
const searchModalShow = ref(false);

function handleSearchSelect(session: ApiSession) {
  // 将该会话顶到列表最前面
  const idx = sessions.value.findIndex(s => s.sessionKey === session.sessionKey);
  if (idx >= 0) {
    sessions.value[idx].updatedAt = Date.now();
  } else {
    // 超出 200 条限制的旧会话，插入列表
    sessions.value.unshift({...session, updatedAt: Date.now()});
  }
  // 持久化"顶到最近"：后端 update_time 随之刷新，刷新页面后仍排在前面
  fetchUpdateAgentSession(session.sessionKey, {touch: true}).catch(() => {});
  loadSession(session.sessionKey);
}

async function startNewSession() {
  // 仅清掉草稿态自身的流；其他会话的流保持后台运行
  activeChatAborts['']?.abort();
  delete activeChatAborts[''];
  setRunning('', false);
  draftMessages.value = [];
  draftMsgIdCounter = 0;
  switchInputDraft('');
  currentSessionKey.value = '';
  syncSessionToUrl();
  closeSidebarIfMobile();
}

/** 侧栏「新建对话」：开新会话并把伴侣面板一并关掉。关板只放在这个入口，不进 startNewSession——
 *  sendSingle 首条消息也走 startNewSession，在那里关板会把首屏刚挂上/自动唤起的板抹掉 */
function handleSidebarNewSession() {
  detachBoard();
  void startNewSession();
  triggerDailyBriefIfNeeded(false);
}

/** 草稿态待生效的驻留专家：无会话时召唤先挂这里，随用户首条消息建会话时落库（用户发起对话才开会话） */
const pendingExpertKey = ref<string | null>(null);

/** 持续精造模式开关（仅管理员可见入口）：开关式常亮，点亮期间每条消息都带 sustained_work 标志 */
const sustainedWorkOn = ref(false);

/** 输入框专家位展示：会话驻留专家 > 草稿态待生效专家 */
const currentExpertInfo = computed(() => {
  if (sessionExpert.value) return sessionExpert.value;
  if (!pendingExpertKey.value) return null;
  const row = expertsList.value.find(e => e.expertKey === pendingExpertKey.value);
  if (!row) return null;
  return { key: row.expertKey, name: row.name, icon: row.icon ?? null, welcome: null };
});

/** 召唤专家（面板「召唤」/ 输入框「专家」按钮统一入口）：
 *  不新建会话——已有会话直接改绑；草稿态挂 pending，随用户首条消息落库 */
async function handleSummonExpert(expertKey: string) {
  if (currentSessionKey.value) {
    const { data, error } = await fetchUpdateAgentSession(currentSessionKey.value, { expertKey });
    if (error) {
      window.$message?.error?.('召唤失败');
      return;
    }
    const sRow = sessions.value.find(x => x.sessionKey === currentSessionKey.value);
    if (sRow && data) {
      sRow.expertKey = data.expertKey;
      sRow.expertName = data.expertName;
      sRow.expertIcon = data.expertIcon;
    }
  } else {
    pendingExpertKey.value = expertKey;
  }
  const ex = expertsList.value.find(e => e.expertKey === expertKey);
  window.$message?.success?.(ex ? `已召唤${expertLabel}「${ex.name}」` : `已召唤${expertLabel}`);
}

/** 输入框「专家」按钮候选：已添加且启用的专家 */
const expertCandidates = computed(() =>
  expertsList.value.filter(e => e.isAdded && e.userEnabled).map(e => ({ key: e.expertKey, name: e.name, icon: e.icon ?? null }))
);

/** 移除驻留专家：草稿态清 pending；已有会话解绑（下一条消息回通用形态） */
async function handleRemoveSessionExpert() {
  if (!currentSessionKey.value) {
    pendingExpertKey.value = null;
    return;
  }
  const key = currentSessionKey.value;
  const { data, error } = await fetchUpdateAgentSession(key, { expertKey: null });
  if (error) {
    window.$message?.error?.('移除失败');
    return;
  }
  const sRow = sessions.value.find(x => x.sessionKey === key);
  if (sRow) {
    sRow.expertKey = data?.expertKey ?? null;
    sRow.expertName = data?.expertName ?? undefined;
    sRow.expertIcon = data?.expertIcon ?? undefined;
  }
  window.$message?.success?.(`已移除${expertLabel}，回到通用助手`);
}

/**
 * 旧消息（deepagents 时代）回放兼容：thinking + toolSteps → 过程时间线条目。
 * 新消息走 processSteps 直通，不经过这里。
 */
function legacyStepsToProcess(thinking: string | null | undefined, toolSteps: AgentToolStep[] | null | undefined): ProcessStep[] {
  const out: ProcessStep[] = [];
  const t = (thinking || '').trim();
  if (t) out.push({id: 'legacy-thinking', kind: 'reasoning', content: t});
  for (const s of toolSteps || []) {
    if (s.type === 'tool_call') {
      out.push({id: `legacy-c${s.id}`, kind: 'tool_call', tool: s.tool, tool_display: s.tool_display || s.tool, args: s.args || {}});
    } else {
      out.push({id: `legacy-e${s.id}`, kind: 'tool_result', tool: s.tool, tool_display: s.tool_display || s.tool, content: s.content ?? ''});
    }
  }
  return out;
}

/**
 * 恢复路径重建：从条目尾部倒扫，定位「打开中」的尾部 text 条目。
 * 条目按时间顺序落库，而 text 只会被其后的 tool_call（定性叙述）或回合结束
 * （提升为答案并从 process_json 剔除）关闭，因此倒扫时：
 * 跳过 reasoning/todo/compaction（不影响 text 开闭），
 * 遇到 text → 它必然打开中（流式中途 / error 快照）；遇到 tool_call/tool_result → 尾部文本已闭合。
 */
function tailOpenTextId(items: ProcessStep[]): string | null {
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i];
    if (it.kind === 'reasoning' || it.kind === 'todo' || it.kind === 'compaction' || it.kind === 'questionnaire') continue;
    return it.kind === 'text' ? it.id : null;
  }
  return null;
}

function apiMsgToUi(m: ApiMessage): Message {
  // 过程时间线双路径：新消息 processSteps 直通；旧消息 thinking/toolSteps 兼容转换。
  const process = m.processSteps?.length
    ? m.processSteps
    : legacyStepsToProcess(m.thinking, m.toolSteps);

  let content = m.content || '';
  let openTextId: string | null = null;
  if (m.processSteps?.length && (m.status === 'streaming' || m.status === 'error')) {
    // 恢复路径（刷新 / 轮询）重建 ——
    // DB 流式中途 / error 快照里 content = 全部累积增量（含已定性进时间线的叙述），
    // process_json 又含未闭合的尾部 text 条目，照单渲染会在结果区与时间线双双重复。
    // 这里还原 live 语义：尾部倒扫找打开中的 text 条目——
    // 找到 → 它正由结果区流式渲染：content 取其全量，时间线按 openTextId 隐藏；
    // 没找到 → 处于工具执行阶段，结果区应为空（live 行为在 tool_call 时清空）。
    // 注意：done/aborted 不重建——后端已把尾部文本提升为答案（content = 答案、
    // process 剔除它），若强行置空会抹掉 aborted 保留的部分答案。
    openTextId = tailOpenTextId(m.processSteps);
    content = openTextId ? (m.processSteps.find(i => i.id === openTextId)?.content || '') : '';
  }
  let inlineCharts: AgentArtifact[] = [];
  let inlineHtmls: AgentArtifact[] = [];
  if (content) {
    const r1 = extractChartBlocks(content);
    content = r1.stripped;
    inlineCharts = r1.charts;
    const r2 = extractHtmlBlocks(content);
    content = r2.stripped;
    inlineHtmls = r2.htmlArtifacts;
  }
  const artifacts = mergeArtifacts(mergeArtifacts(m.artifacts, inlineCharts), inlineHtmls);
  // 用户主动停止不算异常：aborted 状态（含历史脏数据 error='用户主动停止'）统一转成中性的 stopped 提示
  const stopped = m.status === 'aborted' || m.error === '用户主动停止';
  // 历史回放一律默认折叠（点击摘要行展开回看）；loading 中的消息保持展开。
  const msg: Message = {
    id: m.id,
    serverId: m.id,
    role: m.role,
    content,
    process: process.length ? process : undefined,
    processCollapsed: process.length && m.status !== 'streaming' ? true : undefined,
    openTextId: openTextId || undefined,
    loading: m.status === 'streaming',
    error: stopped ? null : m.error,
    stopped: stopped || undefined,
    artifacts,
    attachments: m.attachments || undefined
  };
  if (msg.content) {
    msg.contentHtml = marked.parse(stripArtifactMarkers(msg.content)) as string;
    linkifyStandardNos(msg.contentHtml).then(h => { msg.contentHtml = h; });
  }
  msg.contentSegments = buildContentSegments(msg.content, msg.artifacts);
  // 异步 linkify segments 中的 html 段
  if (msg.contentSegments?.length) {
    Promise.all(msg.contentSegments.map(async (seg, i) => {
      if (seg.type === 'html' && seg.html) {
        const linked = await linkifyStandardNos(seg.html);
        if (msg.contentSegments && msg.contentSegments[i]) {
          msg.contentSegments[i] = {...seg, html: linked};
        }
      }
    }));
  }
  return msg;
}

async function loadSession(key: string) {
  if (key === currentSessionKey.value) {
    closeSidebarIfMobile();
    return;
  }

  // 切换会话时隐藏日报
  briefState.visible = false;

  switchInputDraft(key);
  currentSessionKey.value = key;
  restoreBoardPanelForSession(key);
  syncSessionToUrl();
  closeSidebarIfMobile();

  // 本地已有缓存（含正在跑流的会话），直接复用，不再拉 DB 覆盖
  if (sessionMessages[key] && sessionMessages[key].length > 0) {
    // 切走时轮询会停；切回若仍有生成中的消息，续上轮询（pollUntilDone 内部防重入）
    if (sessionMessages[key].some(m => m.role === 'assistant' && m.loading)) {
      setRunning(key, true);
      pollUntilDone(key);
    }
    scrollFeedToBottom(false);
    return;
  }

  loadingKeys.add(key);
  const {data, error} = await fetchAgentMessages(key).finally(() => loadingKeys.delete(key));
  if (error || !data) return;
  const ui: Message[] = [];
  let hasStreamingMsg = false;
  for (const m of data) {
    const u = apiMsgToUi(m);
    // 历史消息中 streaming 状态：后台任务可能仍在执行，保持 loading 显示
    if (u.loading && u.role === 'assistant') {
      hasStreamingMsg = true;
    }
    ui.push(u);
  }
  sessionMessages[key] = ui;
  sessionMsgIdCounter[key] = ui.reduce((mx, x) => Math.max(mx, x.id), 0);
  scrollFeedToBottom(false);

  // 若存在后台仍在执行的消息，启动轮询直到完成
  if (hasStreamingMsg) {
    setRunning(key, true);
    pollUntilDone(key);
  }
}

// 轮询：历史消息中存在 streaming 状态时，每隔 3s 重新拉取一次，直到所有消息都完成
const pollingTimers: Record<string, ReturnType<typeof setTimeout>> = {};

async function pollUntilDone(key: string) {
  if (pollingTimers[key]) return; // 已在轮询中，不重复启动
  const tick = async () => {
    // 会话已切走，停止轮询
    if (currentSessionKey.value !== key) {
      delete pollingTimers[key];
      setRunning(key, false);
      return;
    }
    const { data, error } = await fetchAgentMessages(key);
    if (error || !data) {
      delete pollingTimers[key];
      return;
    }
    const stillStreaming = data.some(m => m.status === 'streaming');
    // 仅更新 streaming 消息的内容（避免干扰 batch 等复杂状态）
    const list = getMessageList(key);
    for (const m of data) {
      const existing = list.find(x => x.serverId === m.id);
      if (!existing) continue;
      if (m.status !== 'streaming') {
        // 已完成，更新内容并关闭 loading
        const u = apiMsgToUi(m);
        Object.assign(existing, u);
        existing.loading = false;
      } else {
        // 仍在 streaming：增量合并后端节流落库的中间进度（正文/过程时间线），
        // 刷新页面后不再只是「思考中...」占位
        const u = apiMsgToUi(m);
        Object.assign(existing, u); // u.loading 已按 status 置 true
        // 从时间线末尾找最后一条工具事件：末尾是未配对的 tool_call → 显示「执行 · xxx」
        const items = u.process || [];
        const lastTool = [...items].reverse().find(i => i.kind === 'tool_call' || i.kind === 'tool_result');
        existing.currentTool = lastTool && lastTool.kind === 'tool_call' ? (lastTool.tool_display || lastTool.tool || '') : '';
        // 轮询恢复的流式消息时间线保持展开
        existing.processCollapsed = false;
      }
    }
    if (stillStreaming) {
      pollingTimers[key] = setTimeout(tick, 3000);
    } else {
      delete pollingTimers[key];
      setRunning(key, false);
      scrollFeedToBottom(false);
    }
  };
  pollingTimers[key] = setTimeout(tick, 3000);
}

async function deleteSession(key: string, e?: Event) {
  e?.stopPropagation();
  // 删除前先停掉这个会话所有跑着的流（不影响其他会话）
  activeChatAborts[key]?.abort();
  delete activeChatAborts[key];
  setRunning(key, false);
  delete sessionMessages[key];
  delete sessionMsgIdCounter[key];
  delete inputDrafts[key];
  // 删除前先按展示顺序（置顶优先、更新时间倒序，与侧栏一致）算好相邻会话，
  // 删掉当前会话后落到它的下一条，而不是永远跳到列表第一条（有置顶会话时总是跳到那条置顶的）
  const ordered = [...sessions.value].sort((a, b) => b.isStarred - a.isStarred || b.updatedAt - a.updatedAt);
  const idx = ordered.findIndex(s => s.sessionKey === key);
  const neighbor = idx >= 0 ? ordered[idx + 1] ?? ordered[idx - 1] : undefined;
  await fetchDeleteAgentSession(key).catch(() => {
  });
  sessions.value = sessions.value.filter(s => s.sessionKey !== key);
  if (currentSessionKey.value === key) {
    // 兜底切换到被删会话的相邻会话（画板会话已融合进主页，不再跳过）
    if (neighbor && sessions.value.some(s => s.sessionKey === neighbor.sessionKey)) {
      await loadSession(neighbor.sessionKey);
    } else {
      // 最后一个删完也不自动建，保持草稿态
      currentSessionKey.value = '';
    }
  }
}

// ───── 伴侣面板：流程编排 / 应用制作挂进对话页 ─────────────────────
// 上下文随面板：面板打开时消息自动带 workflow_key（sendSingle 取 attachedWorkflowKey）；
// 惰性绑属：首次带板发送后后端把会话归属同步到该板，下次打开该会话自动恢复面板（watch currentSessionKey）
const attachedWorkflowKey = ref<string | null>(null);
const boardPanelOpen = ref(false);
const boardPanelRef = ref<InstanceType<typeof BoardPanel> | null>(null);
const boardPanelVisible = computed(() => !props.embedded && !isMobile.value && boardPanelOpen.value && !!attachedWorkflowKey.value);

// 面板宽度全程按比例（跨屏幕一致）：0 = 默认 2/3（板是主角，grid 轨道 (1-r)fr : r fr）；
// 拖动分隔条改比例并记忆（localStorage 存比例值，不存 px）；双击分隔条回默认
const PANEL_RATIO_KEY = 'SOY_qa_board_panel_ratio';
const DEFAULT_PANEL_RATIO = 2 / 3;
// 画板最低占内容区一半（用户规格）；对话列至多让到 400px（运行期边界兜底，不影响存比例）
const MIN_PANEL_RATIO = 0.5;
const boardPanelRatio = ref(Number(localStorage.getItem(PANEL_RATIO_KEY)) || 0);
const currentPanelRatio = computed(() => (boardPanelRatio.value >= MIN_PANEL_RATIO && boardPanelRatio.value < 1 ? boardPanelRatio.value : DEFAULT_PANEL_RATIO));
function resetPanelRatio() {
  boardPanelRatio.value = 0;
  localStorage.removeItem(PANEL_RATIO_KEY);
}
let panelResizeAnchor: {right: number; sidebarW: number} | null = null;
const boardPanelResizing = ref(false);
function onPanelResizeStart(e: PointerEvent) {
  if (e.button !== 0) return;
  const shellEl = document.querySelector('.qa-shell') as HTMLElement | null;
  if (!shellEl) return;
  const rect = shellEl.getBoundingClientRect();
  panelResizeAnchor = {right: rect.right, sidebarW: sidebarOpen.value ? 272 : 0};
  boardPanelResizing.value = true;
  (e.target as HTMLElement).setPointerCapture(e.pointerId);
  window.addEventListener('pointermove', onPanelResizeMove);
  window.addEventListener('pointerup', onPanelResizeUp);
}
function onPanelResizeMove(e: PointerEvent) {
  if (!panelResizeAnchor) return;
  const shellW = panelResizeAnchor.right - (document.querySelector('.qa-shell') as HTMLElement).getBoundingClientRect().left;
  const contentW = shellW - panelResizeAnchor.sidebarW;
  if (contentW <= 0) return;
  // 比例 = 指针右侧剩余宽 / 内容区宽；钳在 [一半, 1-400px 兜底]
  const maxR = Math.max(1 - 400 / contentW, MIN_PANEL_RATIO);
  const r = (panelResizeAnchor.right - e.clientX) / contentW;
  boardPanelRatio.value = Math.round(Math.min(Math.max(r, MIN_PANEL_RATIO), maxR) * 1000) / 1000;
}
function onPanelResizeUp() {
  panelResizeAnchor = null;
  boardPanelResizing.value = false;
  window.removeEventListener('pointermove', onPanelResizeMove);
  window.removeEventListener('pointerup', onPanelResizeUp);
  if (boardPanelRatio.value) localStorage.setItem(PANEL_RATIO_KEY, String(boardPanelRatio.value));
}
// 面板打开时以行内样式接管第三轨道（比例 → fr，任何屏幕同比例），同时兼容侧栏收展
const boardPanelGridStyle = computed(() => {
  if (!boardPanelVisible.value) return {};
  const side = sidebarOpen.value ? '272px' : '0px';
  const r = currentPanelRatio.value;
  return {gridTemplateColumns: `${side} ${Number((1 - r).toFixed(4))}fr ${Number(r.toFixed(4))}fr`};
});
// 对话列被压到内容区一半以下（=板超过一半）：隐藏侧栏开关，顶栏只留内容钮
const chatColumnCramped = computed(() => boardPanelVisible.value && currentPanelRatio.value > MIN_PANEL_RATIO + 0.001);

// 选板弹层数据（composer 内弹层；fetchListWorkflows 无板型参数，前端按 boardType 过滤）
const boardPickerMode = ref<'board' | 'html'>('board');
const boardPickerOpen = ref(false);
const boardPickerList = ref<WorkflowListItem[]>([]);
const boardPickerLoading = ref(false);
// 板型本地备忘（wk → boardType）：首屏建板/挂板时会话还没落库、选板列表也没拉过，
// attachedBoardInfo 查不到板型会误回落 'board'（勾的应用制作却显示成流程编排）——凡得知板型的入口都记一笔
const knownBoardTypes = reactive<Record<string, 'board' | 'html'>>({});
async function loadBoardPickerList() {
  boardPickerLoading.value = true;
  const {data} = await fetchListWorkflows();
  boardPickerList.value = data || [];
  for (const b of boardPickerList.value) {
    knownBoardTypes[b.workflowKey] = (b.boardType || 'board') as 'board' | 'html';
  }
  boardPickerLoading.value = false;
}
function openBoardPicker(mode: 'board' | 'html') {
  // 首屏空态：还没有任何对话内容，选板没有意义——直接建一块空板开聊
  if (showNewWelcome.value) {
    void createBoardInPanel(mode);
    return;
  }
  boardPickerMode.value = mode;
  boardPickerOpen.value = true;
  void loadBoardPickerList();
}
function closeBoardPicker() {
  boardPickerOpen.value = false;
}
function attachBoard(wk: string) {
  boardPickerOpen.value = false;
  if (attachedWorkflowKey.value !== wk) {
    // 换板前先冲刷现挂板的未保存编辑（board-shell 延迟保存 900ms，直接换会丢）
    void boardPanelRef.value?.flushSave();
    attachedWorkflowKey.value = wk;
  }
  boardPanelOpen.value = true;
  syncSessionToUrl();
}
async function createBoardInPanel(type: 'board' | 'html') {
  const {data, error} = await fetchCreateWorkflow({
    title: type === 'html' ? '新应用制作' : '新流程编排',
    nodes: [],
    edges: [],
    boardType: type
  });
  if (error || !data) {
    window.$message?.error('创建失败');
    return;
  }
  knownBoardTypes[data.workflowKey] = type;
  attachBoard(data.workflowKey);
}
function detachBoard() {
  boardPickerOpen.value = false;
  attachedWorkflowKey.value = null;
  boardPanelOpen.value = false;
  syncSessionToUrl();
}
/** 到流程编排专页打开当前挂板：深链 wk+sid，专页悬浮窗续上同一会话 */
function expandBoardToPage() {
  boardPickerOpen.value = false;
  if (!attachedWorkflowKey.value) return;
  router.push({
    name: 'ai_workflow',
    query: {wk: attachedWorkflowKey.value, ...(currentSessionKey.value ? {sid: currentSessionKey.value} : {})}
  });
}
function closeBoardPanel() {
  // 收起仅清页面态；DB 归属保留，重开该会话仍会恢复面板
  attachedWorkflowKey.value = null;
  boardPanelOpen.value = false;
  syncSessionToUrl();
}
function onPanelNotFound() {
  attachedWorkflowKey.value = null;
  boardPanelOpen.value = false;
  syncSessionToUrl();
  window.$message?.error('该板已不存在（空板会被自动清理）');
}
/** 当前挂板信息（composer chip 态）：标题优先查选板列表，板型按「列表 → 会话归属 → 本地备忘 → 兜底 board」解析 */
const attachedBoardInfo = computed(() => {
  const wk = attachedWorkflowKey.value;
  if (!wk) return null;
  const hit = boardPickerList.value.find(b => b.workflowKey === wk);
  const sess = sessions.value.find(s => s.workflowKey === wk);
  return {
    workflowKey: wk,
    title: hit?.title || '',
    boardType: ((hit?.boardType || sess?.boardType || knownBoardTypes[wk] || 'board') as 'board' | 'html')
  };
});
// 挂板但三处都查不到板型（如 URL 恢复 / 跨端建板）：补拉一次板列表落进 knownBoardTypes，chip 自愈
watch(attachedWorkflowKey, wk => {
  if (!wk || knownBoardTypes[wk]) return;
  if (boardPickerList.value.some(b => b.workflowKey === wk)) return;
  if (sessions.value.some(s => s.workflowKey === wk && s.boardType)) return;
  void loadBoardPickerList();
});
/** 板子唤起由 agent 自主判断（后端建板工具 create_workflow_board + 提示词引导，无前端硬逻辑）：
 *  流中 agent 调板工具即它正操作/新建那块板——自动挂上并伸出面板。
 *  - 建板工具的结果里带新板 workflow_key（tool_result content），但其后紧跟的 read/edit 调用参数里也带，
 *    统一在 tool_call 时点按参数挂板即可；建板调用顺手记下板型，免等列表回拉。
 *  - 复用 attachBoard：先冲刷现挂板未保存编辑再切换 */
const WORKFLOW_TOOL_NAMES = new Set(['create_workflow_board', 'read_workflow', 'edit_workflow_board', 'publish_html_board', 'rollback_workflow_version']);
/** dsh 桥工具带传输前缀（mcp__stdtools__X / mcp__conn_<key>__X），剥掉再匹配——与后端 tool_display_names 同规则 */
function bareToolName(name: string): string {
  if (name.startsWith('mcp__')) {
    const parts = name.split('__');
    if (parts.length >= 3 && parts[2]) return parts[2];
  }
  return name;
}
function autoAttachBoardFromTool(toolName: string, args: Record<string, unknown>) {
  if (props.embedded || !WORKFLOW_TOOL_NAMES.has(bareToolName(toolName))) return;
  const wk = typeof args?.workflow_key === 'string' ? args.workflow_key : '';
  if (!wk || wk === attachedWorkflowKey.value) return;
  attachBoard(wk);
}
/** create_workflow_board 的 tool_result 里带新板 workflow_key 与板型：记下板型并挂板
 *  （建板调用参数里还没有 key，挂板发生在结果回来这一刻） */
function autoAttachBoardFromCreateResult(toolName: string, content: string) {
  if (props.embedded || bareToolName(toolName) !== 'create_workflow_board' || !content) return;
  try {
    const r = JSON.parse(content) as {ok?: boolean; workflow_key?: string; board_type?: string};
    if (!r?.ok || !r.workflow_key) return;
    if (r.board_type === 'html' || r.board_type === 'board') knownBoardTypes[r.workflow_key] = r.board_type;
    if (r.workflow_key !== attachedWorkflowKey.value) attachBoard(r.workflow_key);
  } catch {
    /* 非 JSON 结果忽略 */
  }
}
/** 面板内选中节点：随消息发给 Agent（接通画板「选中节点」上下文，scope 软强制仍不启用） */
const panelSelectedNodeIds = computed<string[]>(() => boardPanelRef.value?.selectedNodeIds || []);
// 对话桥：画板成品组件所有「找对话」的动作经此桥（editLocked 数据源 / 程序化发消息 / 上传取会话 key）
const boardBridge: QaBridge = {
  running,
  sendMessage: async (t: string) => {
    if (running.value) return false;
    await sendSingle(t);
    return true;
  },
  currentSessionKey: () => currentSessionKey.value || ''
};
/** 面板跟随会话归属：带板会话自动恢复/伸出，无板会话收起（列表里没有该会话时不动，防误清）。
 *  必须在 loadSession 的 syncSessionToUrl 之前同步执行，否则 URL 同步拿到的还是旧挂板态 */
function restoreBoardPanelForSession(key: string) {
  if (props.embedded || !key) return;
  const s = sessions.value.find(x => x.sessionKey === key);
  if (!s) return;
  if (s.workflowKey) {
    attachedWorkflowKey.value = s.workflowKey;
    boardPanelOpen.value = true;
  } else {
    attachedWorkflowKey.value = null;
    boardPanelOpen.value = false;
  }
}

// ───── 收藏/改名/分叉 ────────────────────────────────────────────────────

async function toggleStar(key: string, e: Event) {
  e.stopPropagation();
  const s = sessions.value.find(x => x.sessionKey === key);
  if (!s) return;
  const next = s.isStarred ? 0 : 1;
  s.isStarred = next;
  const {data, error} = await fetchUpdateAgentSession(key, {is_starred: next});
  if (error || !data) s.isStarred = next ? 0 : 1; // 失败回滚
}

const renamingKey = ref<string>('');
const renamingTitle = ref<string>('');

function startRename(key: string, currentTitle: string, e: Event) {
  e.stopPropagation();
  renamingKey.value = key;
  renamingTitle.value = currentTitle;
  nextTick(() => {
    const input = document.querySelector<HTMLInputElement>(`.session-rename-input[data-key="${key}"]`);
    input?.focus();
    input?.select();
  });
}

async function commitRename() {
  const key = renamingKey.value;
  const title = renamingTitle.value.trim();
  renamingKey.value = '';
  if (!key || !title) return;
  const s = sessions.value.find(x => x.sessionKey === key);
  if (!s) return;
  const prev = s.title;
  s.title = title;
  const {data, error} = await fetchUpdateAgentSession(key, {title});
  if (error || !data) s.title = prev;
}

function cancelRename() {
  renamingKey.value = '';
}

const truncatingMsgId = ref<number | null>(null);
const truncateConfirmMsgId = ref<number | null>(null);

// 检测 q-text 是否被 line-clamp 截断，只有截断时才显示 tooltip
const clampedMsgIds = reactive(new Set<number>());
const qTextRoMap = new Map<number, ResizeObserver>();

function registerQTextRef(el: HTMLElement | null, msgId: number) {
  if (!el) {
    qTextRoMap.get(msgId)?.disconnect();
    qTextRoMap.delete(msgId);
    return;
  }
  if (qTextRoMap.has(msgId)) return;
  const check = () => {
    if (el.scrollHeight > el.clientHeight + 1) {
      clampedMsgIds.add(msgId);
    } else {
      clampedMsgIds.delete(msgId);
    }
  };
  const ro = new ResizeObserver(check);
  ro.observe(el);
  qTextRoMap.set(msgId, ro);
  check();
}

function handleTruncateOutsideClick(e: MouseEvent) {
  if (!(e.target as HTMLElement).closest('.answer-truncate--confirm')) {
    truncateConfirmMsgId.value = null;
  }
}

async function truncateFromMessage(serverId: number) {
  if (!currentSessionKey.value) return;
  truncatingMsgId.value = serverId;
  truncateConfirmMsgId.value = null;
  try {
    const {data, error} = await fetchTruncateAgentSession(currentSessionKey.value, serverId);
    if (error || !data) {
      window.$message?.error?.('截断失败');
      return;
    }
    // 从当前消息列表里删掉此消息（含）及之后的内容
    const idx = messages.value.findIndex(m => m.serverId === serverId);
    if (idx >= 0) {
      messages.value.splice(idx);
    }
    // 更新侧边栏会话信息
    const si = sessions.value.findIndex(s => s.sessionKey === currentSessionKey.value);
    if (si >= 0 && data) {
      sessions.value[si] = data;
    }
  } finally {
    truncatingMsgId.value = null;
  }
}

// ───── Send / Stream ─────────────────────────────────────────────────────
function scrollToBottom() {
  followBottom.value = true;
  turnSkipped.value = false;
  anchorCheckExhausted.value = false; // 新一轮：anchor 检查重新可用
  programmaticScrollUntil = Date.now() + 700;
  nextTick(() => {
    if (scrollEl.value) scrollEl.value.scrollTo({top: scrollEl.value.scrollHeight, behavior: 'smooth'});
  });
}

function truncate(s: string, n = 100): string {
  return s.length > n ? s.slice(0, n) + '…' : s;
}

// 从 markdown 内容中去除 [artifact:N] 占位行，用于生成 contentHtml 时不暴露明文标记。
// msg.content 本身保留原始标记，供 buildContentSegments 使用。
// loading=true 时替换为友好占位符（打字机过程），false 时直接去除（done 后 contentSegments 接管渲染）。
function stripArtifactMarkers(content: string, loading = false): string {
  if (loading) {
    const CHART_PH = '\n<div class="als-chart"><div class="als-bars"><div class="als-bone als-bar b1"></div><div class="als-bone als-bar b2"></div><div class="als-bone als-bar b3"></div><div class="als-bone als-bar b4"></div><div class="als-bone als-bar b5"></div><div class="als-bone als-bar b6"></div></div><div class="als-axis"></div></div>\n';
    const HTML_PH  = '\n<div class="als-html"><div class="als-page"><div class="als-bone als-nav"></div><div class="als-grid"><div class="als-col"><div class="als-bone als-line lf"></div><div class="als-bone als-line l8"></div><div class="als-bone als-line lf"></div><div class="als-bone als-line l6"></div></div><div class="als-bone als-side"></div></div><div class="als-bone als-foot"></div></div></div>\n';
    const FILE_PH  = '\n<div class="als-file"><div class="als-bone als-f-ext"></div><div class="als-bone als-f-name"></div><div class="als-bone als-f-act"></div></div>\n';
    return content
      .replace(/```chart[\s\S]*$/g, CHART_PH)
      .replace(/```html[\s\S]*$/g, HTML_PH)
      .replace(/^\[artifact:(-?\d+)\]\s*$/gm, FILE_PH)
      .replace(/\n{3,}/g, '\n\n')
      .trim();
  }
  return content
    .replace(/^\[artifact:-?\d+\]\s*$/gm, '')
    .replace(/^\[questionnaire:qn\d+\]\s*$/gm, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

// 把 markdown 里的 ```chart {...}``` fenced block 抽出来作为 artifact，
// 同时从正文里删掉，避免把 raw JSON 展示给用户。
function extractChartBlocks(content: string): { stripped: string; charts: AgentArtifact[] } {
  const charts: AgentArtifact[] = [];
  const pattern = /```chart[^\n]*\n([\s\S]*?)```/g;
  let idx = 0;
  const stripped = content.replace(pattern, (_m, body: string) => {
    try {
      const spec = JSON.parse(body.trim());
      idx += 1;
      const id = -Date.now() - idx; // 负值表示内联 chart，不来自 DB
      charts.push({
        id,
        artifactType: 'chart',
        name: (typeof spec.title === 'string' ? spec.title : spec.title?.text) || `图表 ${idx}`,
        description: null,
        path: null,
        size: null,
        chartSpec: spec,
        messageId: null,
        batchItemId: null,
        downloadUrl: null,
        createdAt: Date.now()
      });
    } catch {
      // 解析失败就保留原 block
      return `\`\`\`chart\n${body}\n\`\`\``;
    }
    return `\n[artifact:${charts[charts.length - 1].id}]\n`;
  });
  return {stripped, charts};
}

// 把 markdown 里的 ```html ... ``` fenced block 抽出来作为内联 HTML artifact
function extractHtmlBlocks(content: string): { stripped: string; htmlArtifacts: AgentArtifact[] } {
  const htmlArtifacts: AgentArtifact[] = [];
  const pattern = /```html[^\n]*\n([\s\S]*?)```/g;
  let idx = 0;
  const stripped = content.replace(pattern, (_m, body: string) => {
    idx += 1;
    const id = -Date.now() - 10000 - idx; // 负值，与 chart 区段错开
    htmlArtifacts.push({
      id,
      artifactType: 'html',
      name: `页面 ${idx}`,
      description: null,
      path: null,
      size: null,
      chartSpec: null,
      htmlContent: body,
      messageId: null,
      batchItemId: null,
      downloadUrl: null,
      createdAt: Date.now()
    } as any);
    return `\n[artifact:${id}]\n`;
  });
  return {stripped, htmlArtifacts};
}

function mergeArtifacts(
  base: AgentArtifact[] | undefined,
  inline: AgentArtifact[]
): AgentArtifact[] {
  const out = base ? [...base] : [];
  out.push(...inline);
  return out;
}

// 流式中提前提取已完整的 chart/html 块，立即渲染
function extractAndRefreshInline(msg: Message): void {
  if (!msg.content) return;
  const {stripped: s1, charts} = extractChartBlocks(msg.content);
  const {stripped, htmlArtifacts} = extractHtmlBlocks(s1);

  // 如果提取到新的 artifacts，更新 content 和 artifacts
  if (charts.length || htmlArtifacts.length) {
    msg.content = stripped;
    if (charts.length) msg.artifacts = mergeArtifacts(msg.artifacts, charts);
    if (htmlArtifacts.length) msg.artifacts = mergeArtifacts(msg.artifacts, htmlArtifacts);
  }

  // 如果存在 artifacts（包括之前提取的），需要刷新 contentSegments
  // 这样流式追加的文本才能正确显示在最后一个 segment 中
  if (msg.artifacts?.length) {
    refreshContentSegments(msg);
  }
}

// ───── Content Segments (inline artifacts) ──────────────────────────────
type ContentSegment = { type: 'html'; html: string; markdown?: string } | { type: 'artifact'; id: number } | { type: 'questionnaire'; id: string };

/**
 * 解析 content 中的 [artifact:ID] / [questionnaire:qid] 占位符，切分成文本段 + 卡片段
 * 如果没有任何标记，返回 null（fallback 到原 v-html）
 *
 * 容错策略：当 ID 不匹配时，按出现顺序映射到实际 artifacts（AI 有时会写 [artifact:1] 但实际 ID 是数据库自增值）
 */
function buildContentSegments(
  content: string,
  artifacts: AgentArtifact[] | undefined,
  loading = false
): ContentSegment[] | null {
  if (!content) return null;

  const hasMarkers = SEG_MARKER_RE.test(content);
  SEG_MARKER_RE.lastIndex = 0;
  if (!hasMarkers) return null;

  const artifactIds = new Set((artifacts || []).map(a => a.id));

  // artifact 标记容错（既有语义）：ID 全不匹配且数量相等时按出现顺序映射
  const ART_MARKER_RE = /^\[artifact:(-?\d+)\]$/gm;
  const markerIds: number[] = [];
  let artMatch: RegExpExecArray | null;
  while ((artMatch = ART_MARKER_RE.exec(content)) !== null) {
    markerIds.push(Number(artMatch[1]));
  }
  const allMatched = markerIds.every(id => artifactIds.has(id));
  const needMapping = markerIds.length > 0 && !allMatched && markerIds.length === (artifacts?.length ?? 0);
  const idMap = new Map<number, number>();
  if (needMapping && artifacts) {
    markerIds.forEach((markerId, idx) => {
      idMap.set(markerId, artifacts[idx].id);
    });
  }

  const segments: ContentSegment[] = [];
  const pushTextSegment = (raw: string) => {
    const text = raw.trim();
    if (!text) return;
    const t = loading ? stripArtifactMarkers(text, true) : text;
    segments.push({ type: 'html', html: marked.parse(t) as string, markdown: loading ? t : undefined });
  };
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = SEG_MARKER_RE.exec(content)) !== null) {
    if (match[1] !== undefined) {
      // artifact 标记：只处理真实存在的 ID（不存在则占位符留在文本段里）
      const rawId = Number(match[1]);
      const actualId = needMapping ? (idMap.get(rawId) ?? rawId) : rawId;
      if (!artifactIds.has(actualId)) continue;
      pushTextSegment(content.slice(lastIndex, match.index));
      segments.push({ type: 'artifact', id: actualId });
    } else {
      // 问卷标记：载荷在 process 条目里（父组件按 id 查）；占位符一律消费，缺失时卡片内兜底
      pushTextSegment(content.slice(lastIndex, match.index));
      segments.push({ type: 'questionnaire', id: match[2] });
    }
    lastIndex = SEG_MARKER_RE.lastIndex;
  }

  pushTextSegment(content.slice(lastIndex));

  return segments.length > 0 ? segments : null;
}

/**
 * 获取消息中未被 content 引用的 artifacts（兜底追加在末尾）
 * 容错策略：支持 ID 按顺序映射（与 buildContentSegments 保持一致）
 */
function getUnreferencedArtifacts(msg: Message): AgentArtifact[] {
  if (!msg.artifacts?.length) return [];
  if (!msg.content) return msg.artifacts;

  const MARKER_RE = /^\[artifact:(-?\d+)\]$/gm;
  const artifactIds = new Set(msg.artifacts.map(a => a.id));

  // 收集所有标记的 ID
  const markerIds: number[] = [];
  let match: RegExpExecArray | null;
  while ((match = MARKER_RE.exec(msg.content)) !== null) {
    markerIds.push(Number(match[1]));
  }

  if (markerIds.length === 0) return msg.artifacts;

  // 检查是否需要按顺序映射
  const allMatched = markerIds.every(id => artifactIds.has(id));
  const needMapping = !allMatched && markerIds.length === msg.artifacts.length;

  const referencedIds = new Set<number>();
  if (needMapping) {
    // 按顺序映射：markerIds[i] 对应 artifacts[i].id
    markerIds.forEach((_, idx) => {
      if (idx < msg.artifacts!.length) {
        referencedIds.add(msg.artifacts![idx].id);
      }
    });
  } else {
    // 直接使用标记中的 ID
    markerIds.forEach(id => referencedIds.add(id));
  }

  return msg.artifacts.filter(a => !referencedIds.has(a.id));
}

/**
 * artifact 赋值后同步更新 contentSegments
 * 必须在每次 msg.artifacts 被赋新值后调用
 */
function refreshContentSegments(msg: Message): void {
  msg.contentSegments = buildContentSegments(msg.content, msg.artifacts, msg.loading);
}

/**
 * 从消息的 artifacts 中找指定 ID 的单个 artifact（渲染单个 inline artifact）
 */
function getArtifactById(msg: Message, id: number): AgentArtifact[] {
  if (!msg.artifacts) return [];
  const found = msg.artifacts.find(a => a.id === id);
  return found ? [found] : [];
}

/** 从消息 process 里找指定 id 的问卷条目（正文占位符渲染卡片用） */
function getQuestionnaireById(msg: Message, id: string): ProcessStep | undefined {
  return (msg.process || []).find(p => p.kind === 'questionnaire' && p.id === id);
}

/** 问卷作答配对：第 k 条「问卷回答：」回流消息回答第 k 个未配对问卷。
 * （qid 每回合从 qn1 重新计数，跨回合会重名，必须按顺序配对而非按 id）
 * 同时解析作答行（`- q1 (标题): 值`）为 问题 id → 值，供卡片只读回显实际答案。 */
const ANSWER_MARK = '问卷回答：';
const ANSWER_LINE_RE = /^-\s*(q\d+)\s*[（(][^()（）]*[)）]\s*[:：]\s*(.*)$/;
const qnAnswers = computed(() => {
  const map = new Map<number, Map<string, Record<string, string>>>();
  const pending: Array<{msgId: number; qid: string}> = [];
  for (const m of messages.value) {
    if (m.role === 'user') {
      if ((m.content || '').trimStart().startsWith(ANSWER_MARK) && pending.length) {
        const p = pending.shift()!;
        const per: Record<string, string> = {};
        for (const line of (m.content || '').split('\n')) {
          const mm = ANSWER_LINE_RE.exec(line.trim());
          if (mm) per[mm[1]] = mm[2].trim();
        }
        let inner = map.get(p.msgId);
        if (!inner) {
          inner = new Map();
          map.set(p.msgId, inner);
        }
        inner.set(p.qid, per);
      }
      continue;
    }
    for (const it of m.process || []) {
      if (it.kind === 'questionnaire') pending.push({msgId: m.id, qid: it.id});
    }
  }
  return map;
});

/** 功能性用户消息前缀：消息原样发后端（模型要看），但不在聊天里展示——
 * 问卷作答后用户看到的直接就是 agent 的回复。可复用约定：
 * 以后任何「只需送达模型、无需展示」的功能性消息都按前缀登记到这里。 */
const FUNCTIONAL_USER_PREFIXES = [ANSWER_MARK];

function isFunctionalUserMsg(m: Message): boolean {
  if (m.role !== 'user') return false;
  const t = (m.content || '').trimStart();
  return FUNCTIONAL_USER_PREFIXES.some(p => t.startsWith(p));
}

/** 渲染用消息列表：隐藏问卷回答回流等功能性消息 */
const visibleMessages = computed(() => messages.value.filter(m => !isFunctionalUserMsg(m)));

/** 续接段：紧随功能性消息（问卷作答）之后的助手回复。
 * 问卷只是中间状态——问卷前的内容与作答后的回复本质是同一次回答：
 * 续接段不再重复 answer-mark，间距紧贴上方问卷卡片。 */
const continuationIds = computed(() => {
  const ids = new Set<number>();
  const list = messages.value;
  for (let i = 0; i < list.length - 1; i += 1) {
    if (isFunctionalUserMsg(list[i]) && list[i + 1].role === 'assistant') ids.add(list[i + 1].id);
  }
  return ids;
});

/** 续接段前一条可见消息（即问卷宿主回复）：收紧它与续接段之间的间距 */
const preContinuationIds = computed(() => {
  const ids = new Set<number>();
  const list = messages.value;
  for (let i = 1; i < list.length; i += 1) {
    if (isFunctionalUserMsg(list[i])) ids.add(list[i - 1].id);
  }
  return ids;
});

/** 问卷提交 = 按约定格式的回流消息走普通发送链路（无新增接口，见 qa_agent 提示词契约） */
function onSurveySubmit(text: string) {
  void sendSingle(text);
}

/** 导出 md 时把问卷占位符展开成可读问题行 */
function contentWithQuestionnairesForExport(msg: Message): string {
  if (!msg.content || !/\[questionnaire:qn\d+\]/.test(msg.content)) return msg.content;
  return msg.content.replace(/^\[questionnaire:(qn\d+)\]$/gm, (_m, qid: string) => {
    const it = getQuestionnaireById(msg, qid);
    if (!it?.questions?.length) return '';
    const lines = it.questions.map(q => `**${q.question}**（${q.options.map(o => o.label).join(' / ')}）`);
    return `〔问卷〕\n${lines.join('\n')}`;
  });
}

/**
 * 接受原始 content 字符串和 artifacts 数组，返回未被 content 引用的 artifacts
 * 用于 batch item（没有 Message 对象）
 */
// ───── Export as Markdown ───────────────────────────────────────────────
function buildMarkdown(title: string, content: string, artifacts?: AgentArtifact[]): string {
  const parts: string[] = [];
  parts.push(`# ${title}`);
  parts.push(`\n> 导出于 ${new Date().toLocaleString()}\n`);
  if (content?.trim()) {
    parts.push(content.trim());
  }
  // Inline charts（我们之前从正文剥离过，这里重新拼回去）
  if (artifacts) {
    for (const a of artifacts) {
      if (a.artifactType === 'chart' && a.chartSpec) {
        parts.push('');
        parts.push('```chart');
        parts.push(JSON.stringify(a.chartSpec, null, 2));
        parts.push('```');
      }
    }
    const files = artifacts.filter(a => a.artifactType !== 'chart');
    if (files.length) {
      parts.push('\n## 附件\n');
      for (const f of files) {
        const sizeStr = f.size ? ` (${(f.size / 1024).toFixed(1)} KB)` : '';
        parts.push(`- **${f.name}** · ${f.artifactType.toUpperCase()}${sizeStr}${f.description ? ` — ${f.description}` : ''}`);
      }
    }
  }
  return parts.join('\n');
}

function downloadMd(filename: string, md: string) {
  downloadText(filename.endsWith('.md') ? filename : `${filename}.md`, md, 'text/markdown;charset=utf-8');
}

function exportMessageAsMd(msg: Message) {
  // 找紧挨着它之前的那条用户消息做标题（跳过问卷回答等功能性消息）
  const idx = messages.value.findIndex(m => m.id === msg.id);
  let titleHint = '';
  for (let i = idx - 1; i >= 0; i--) {
    const um = messages.value[i];
    if (um.role === 'user' && !isFunctionalUserMsg(um)) {
      titleHint = um.content.slice(0, 40);
      break;
    }
  }
  const title = titleHint || '回复导出';
  const md = buildMarkdown(title, contentWithQuestionnairesForExport(msg), msg.artifacts);
  downloadMd(sanitizeFilename(title) || 'answer', md);
}

async function sendSingle(text: string) {
  if (!text || running.value) return;
  if (!currentSessionKey.value) await startNewSession();

  // 锁定本次消息归属的 sessionKey（草稿用 ''；session 事件来了再迁移）
  let targetKey = currentSessionKey.value || '';
  const targetList = () => getMessageList(targetKey);

  const sentAttachments: MessageAttachment[] = attachedFiles.value
    .filter(f => f.path && !f.error)
    .map(f => ({name: f.name, path: f.path, size: f.size, isImage: isImageFile(f.name)}));

  const userMsgId = nextMsgId(targetKey);
  targetList().push({id: userMsgId, role: 'user', content: text, attachments: sentAttachments.length > 0 ? sentAttachments : undefined});
  // 用户在老会话里发消息：立刻把这个会话顶到列表首位（groupedSessions 是按 updatedAt 排序的）
  if (targetKey) {
    const s = sessions.value.find(x => x.sessionKey === targetKey);
    if (s) s.updatedAt = Date.now();
  }
  const assistantId = nextMsgId(targetKey);
  const assistantMsg: Message = {
    id: assistantId,
    role: 'assistant',
    content: '',
    process: [],
    processCollapsed: false,
    openTextId: null,
    loading: true,
    currentTool: ''
  };
  targetList().push(assistantMsg);
  scrollToBottom();

  const abortController = new AbortController();
  activeChatAborts[targetKey] = abortController;
  setRunning(targetKey, true);

  // 通过 targetKey + assistantId 取 message —— 切会话后 messages.value 不再指向原数组，必须按 key 查
  const findAssistantMsg = (): Message | undefined =>
    getMessageList(targetKey).find(m => m.id === assistantId);

  try {
    const filePaths = attachedFiles.value.filter(f => f.path && !f.error).map(f => f.path);
    attachedFiles.value.forEach(f => f.previewUrl && URL.revokeObjectURL(f.previewUrl));
    attachedFiles.value = [];

    // 待生效的驻留专家：仅草稿态（本次将新建会话）随请求下发；已有会话的绑定走会话更新接口
    const sendExpertKey = !targetKey ? pendingExpertKey.value ?? undefined : undefined;

    await fetchQAChatStream(
      text,
      targetKey || null,
      (event: QAEvent) => {
        const msg = findAssistantMsg();
        if (!msg) return;

        if (event.type === 'session') {
          // 草稿态拿到真实 sessionKey 后，把消息从 '' 迁移到真实 key，并把 abort/running 也迁过去
          if (event.sessionKey && !targetKey) {
            const realKey = event.sessionKey;
            const draftList = draftMessages.value;
            sessionMessages[realKey] = draftList;
            sessionMsgIdCounter[realKey] = draftMsgIdCounter;
            draftMessages.value = [];
            draftMsgIdCounter = 0;
            if (activeChatAborts['']) {
              activeChatAborts[realKey] = activeChatAborts[''];
              delete activeChatAborts[''];
            }
            if (runningSessions['']) {
              runningSessions[realKey] = true;
              delete runningSessions[''];
            }
            targetKey = realKey;
            // 输入草稿随消息一起迁到真实 key（''=新会话草稿态）
            if ('' in inputDrafts) {
              inputDrafts[realKey] = inputDrafts[''];
              delete inputDrafts[''];
            }
            // 仅当用户没切走时同步 currentSessionKey
            if (!currentSessionKey.value) {
              currentSessionKey.value = realKey;
            }
            reloadSessions();
          }
          if (event.assistantMessageId) {
            msg.serverId = event.assistantMessageId;
          }
          // 会话驻留专家回显（@召唤改绑 / 「和 TA 对话」建会话都经此同步）：
          // 更新本地会话行的专家信息 → 顶栏徽标 / 首屏标题 / 侧栏徽标随 currentSession 派生刷新
          if (event.sessionKey) {
            const sRow = sessions.value.find(x => x.sessionKey === event.sessionKey);
            if (sRow) {
              sRow.expertKey = event.expertKey ?? null;
              sRow.expertName = event.expertName ?? undefined;
              sRow.expertIcon = event.expertIcon ?? undefined;
            }
            if (event.expertSummoned && event.expertName) {
              window.$message?.success?.(`已召唤${expertLabel}「${event.expertName}」，本任务将由 TA 服务`);
            }
          }
        } else if (event.type === 'process') {
          handleProcessEvent(msg, event);
        } else if (event.type === 'moderated') {
          // 审核拦截（输入侧或输出侧）：正文以拦截文案为准（与落库一致），过程清空
          msg.content = '[内容审核未通过，已拦截]';
          msg.contentHtml = '[内容审核未通过，已拦截]';
          msg.contentSegments = null;
          msg.process = [];
          msg.openTextId = null;
        } else if (event.type === 'done') {
          msg.loading = false;
          msg.currentTool = '';
          // 被提升为答案的尾部 text：从时间线剔除（它已在结果区），随后折叠为摘要行
          if (event.promoted?.length && msg.process?.length) {
            msg.process = msg.process.filter(i => !event.promoted.includes(i.id));
          }
          msg.processDurationMs = msg.processStartedAt ? Date.now() - msg.processStartedAt : null;
          msg.processCollapsed = true;
          msg.openTextId = null;
          if (msg.content) {
            const {stripped: s1, charts} = extractChartBlocks(msg.content);
            const {stripped, htmlArtifacts} = extractHtmlBlocks(s1);
            msg.content = stripped;
            msg.contentHtml = marked.parse(stripArtifactMarkers(stripped)) as string;
            if (charts.length) msg.artifacts = mergeArtifacts(msg.artifacts, charts);
            if (htmlArtifacts.length) msg.artifacts = mergeArtifacts(msg.artifacts, htmlArtifacts);
            refreshContentSegments(msg);
            // 流式完成后 linkify 标准编号
            linkifyStandardNos(msg.contentHtml).then(h => { msg.contentHtml = h; });
            if (msg.contentSegments?.length) {
              Promise.all(msg.contentSegments.map(async (seg, i) => {
                if (seg.type === 'html' && seg.html) {
                  const linked = await linkifyStandardNos(seg.html);
                  if (msg.contentSegments && msg.contentSegments[i]) {
                    msg.contentSegments[i] = {...seg, html: linked};
                  }
                }
              }));
            }
          }
          // 用本次任务自己的 sessionKey，不用 currentSessionKey（用户可能已切走）
          const s = sessions.value.find(x => x.sessionKey === targetKey);
          if (s) s.updatedAt = Date.now();
          if (targetKey && msg.serverId) {
            const sid = msg.serverId;
            fetchAgentMessages(targetKey).then(res => {
              if (!res.error && res.data) {
                const fresh = res.data.find(x => x.id === sid);
                if (fresh?.artifacts?.length) {
                  const msgRef = findAssistantMsg();
                  if (msgRef) {
                    msgRef.artifacts = mergeArtifacts(msgRef.artifacts, fresh.artifacts);
                    refreshContentSegments(msgRef);
                  }
                }
              }
            });
          }
        } else if (event.type === 'aborted') {
          // 用户主动停止（非异常）：正常路径下前端已 abort 收不到此事件，这里兜底停止信号来自他处的情况
          msg.stopped = true;
          msg.error = null;
          msg.loading = false;
          msg.currentTool = '';
          msg.processDurationMs = msg.processStartedAt ? Date.now() - msg.processStartedAt : null;
          msg.processCollapsed = false; // 异常终止回合时间线保持展开
        } else if (event.type === 'error') {
          msg.error = event.message;
          msg.loading = false;
          msg.currentTool = '';
          msg.processDurationMs = msg.processStartedAt ? Date.now() - msg.processStartedAt : null;
          msg.processCollapsed = false;
        } else if (event.type === 'quota_exceeded') {
          msg.error = event.message || '积分余额不足，请联系管理员';
          msg.loading = false;
          msg.currentTool = '';
        }
        followScrollIfNeeded();
      },
      abortController.signal,
      filePaths.length > 0 ? filePaths : undefined,
      // 伴侣面板挂板时消息带板上下文（后端按消息级注入板规则）；嵌入模式仍走宿主画板的 workflowKey
      attachedWorkflowKey.value ?? props.workflowKey,
      undefined,
      panelSelectedNodeIds.value.length ? panelSelectedNodeIds.value : props.selectedNodeIds,
      // 草稿态（即将新建会话）携带待生效的驻留专家：会话随用户首条消息创建并绑定
      sendExpertKey,
      // 持续精造模式（仅管理员；开关式常亮，发送后不清空）
      sustainedWorkOn.value
    );
    pendingExpertKey.value = null;
  } catch (err: any) {
    const msg = findAssistantMsg();
    if (msg) {
      if (err?.name !== 'AbortError') msg.error = err?.message || '请求失败';
      msg.loading = false;
      msg.currentTool = '';
    }
  } finally {
    if (activeChatAborts[targetKey] === abortController) {
      delete activeChatAborts[targetKey];
    }
    setRunning(targetKey, false);
    reloadSessions();
  }
}

function handleStop() {
  const key = currentSessionKey.value || '';
  console.log('[handleStop] key=', key, 'running=', runningSessions);
  // 通知后端停止任务
  if (key) {
    console.log('[handleStop] 发送停止请求');
    fetchQAStop(key).catch(err => {
      console.error('停止请求失败:', err);
    });
  } else {
    console.warn('[handleStop] key 为空，跳过停止请求');
  }
  // 单条流：只 abort 当前会话的
  activeChatAborts[key]?.abort();
  delete activeChatAborts[key];
  setRunning(key, false);
  // 从后往前找正在生成的助手消息，立刻标记"已停止"（中性提示，不等刷新拉库）
  const list = getMessageList(key);
  for (let i = list.length - 1; i >= 0; i--) {
    const m = list[i];
    if (m.role === 'assistant' && m.loading) {
      m.loading = false;
      m.currentTool = '';
      m.stopped = true;
      m.error = null;
      break;
    }
  }
}

// ───── Skill popup ──────────────────────────────────────────────────────
function closeSkillPopup() {
  skillPopupOpen.value = false;
  skillQuery.value = '';
  skillActiveIndex.value = 0;
  skillTriggerPos = -1;
}

function handleInput(_e?: Event) {
  const caret = composerRef.value?.selectionStart ?? inputText.value.length;
  const before = inputText.value.slice(0, caret);
  const atIdx = before.lastIndexOf('@');
  if (atIdx < 0) {
    if (skillPopupOpen.value) closeSkillPopup();
    return;
  }
  const prevChar = atIdx === 0 ? ' ' : before[atIdx - 1];
  const isBoundary = /\s/.test(prevChar);
  if (!isBoundary) {
    if (skillPopupOpen.value) closeSkillPopup();
    return;
  }
  const query = before.slice(atIdx + 1);
  if (/\s/.test(query)) {
    if (skillPopupOpen.value) closeSkillPopup();
    return;
  }
  skillTriggerPos = atIdx;
  skillQuery.value = query;
  skillActiveIndex.value = 0;
  const wasOpen = skillPopupOpen.value;
  skillPopupOpen.value = true;
  // 弹窗打开即后台拉最新清单（不阻塞输入，先用现有列表渲染）：
  // 覆盖「会话中创建技能/专家」「其他端变更」等面板事件冒泡覆盖不到的路径
  if (!wasOpen) {
    void reloadSkills();
    void reloadExperts();
  }
}

function insertSkill(skill: AgentSkill) {
  if (skillTriggerPos < 0) {
    closeSkillPopup();
    return;
  }
  const caret = composerRef.value?.selectionStart ?? inputText.value.length;
  const before = inputText.value.slice(0, skillTriggerPos);
  const after = inputText.value.slice(caret);
  const inserted = `@${skill.skillKey} `;
  inputText.value = before + inserted + after;
  closeSkillPopup();
  nextTick(() => {
    const pos = before.length + inserted.length;
    composerRef.value?.setCaretPos(pos);
  });
}

function handleKeydown(e: KeyboardEvent) {
  if (skillPopupOpen.value) {
    const total = filteredSkills.value.length;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (total) skillActiveIndex.value = (skillActiveIndex.value + 1) % total;
      return;
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (total) skillActiveIndex.value = (skillActiveIndex.value - 1 + total) % total;
      return;
    }
    if (e.key === 'Enter' || e.key === 'Tab') {
      if (total) {
        e.preventDefault();
        insertSkill(filteredSkills.value[skillActiveIndex.value]);
        return;
      }
    }
    if (e.key === 'Escape') {
      e.preventDefault();
      closeSkillPopup();
      return;
    }
  }
  // 手机端：回车一律换行，只走可视的发送按钮——软键盘没有 Shift，
  // 否则用户每按一次回车就直接把消息发出去了。
  if (isMobile.value) return;
  // 桌面：IME 输入法选词时回车上屏候选词，不能当成发送。
  // isComposing 标准化字段；keyCode === 229 是部分浏览器 fallback。
  if (e.isComposing || (e as any).keyCode === 229) return;
  if (e.key === 'Enter' && !e.shiftKey) {
    if (running.value) return;
    e.preventDefault();
    handleSend();
  }
}

async function handleSend() {
  if (running.value) return;
  briefState.visible = false;
  const text = inputText.value.trim();
  const hasFiles = attachedFiles.value.some(f => f.path && !f.error);
  if (!text && !hasFiles) return;
  inputText.value = '';
  await sendSingle(text || '[附件]');
}

// ───── 旧案例体系入口开关：暂时隐藏（留给后续「探索」栏目），翻回 true 即恢复 ─────
const SHOW_CASES = false;

// ───── 加载快捷功能案例（fork 为真实会话） ──────────────────────────────────────
// 正在加载的案例 id：fork 有时要花数秒，用它驱动卡片进度条 / 按钮转圈，并防重复触发
const exampleLoadingId = ref<number | null>(null);

async function handleLoadExample(example: QuickActionExample) {
  if (exampleLoadingId.value !== null) return;
  exampleLoadingId.value = example.id;
  const msg = window.$message?.loading?.(`正在加载案例：${example.title}…`, { duration: 0 });
  try {
    const {data, error} = await fetchForkQuickActionExample(example.id);
    if (error || !data) {
      window.$message?.error?.('加载案例失败');
      return;
    }

    // 将新会话添加到侧边栏顶部
    sessions.value.unshift(data);

    // 切换到新会话（loadSession 处理 set key + syncUrl + fetch messages + scroll）
    await loadSession(data.sessionKey);

    // 关闭案例弹窗
    closeSkillPopup();

    window.$message?.success?.(`已加载案例：${example.title}`);
  } catch {
    window.$message?.error?.('加载案例失败');
  } finally {
    exampleLoadingId.value = null;
    msg?.destroy?.();
  }
}

// ───── 功能案例橱窗（会话为空时的首屏） ─────────────────────────────────
// 原始全量数据（橱窗与对话框的数据源）
const allShowcaseActions = ref<QuickAction[]>([]);
const allShowcaseGroupDefs = ref<QuickActionGroup[]>([]);
const showcaseLoading = ref(false);
let showcaseLastLoadAt = 0;

// ── 新手引导 / 个人订阅状态 ──
/** 「专家」体系称谓随品牌变体：standard=助理 / generic=专家 */
const expertLabel = brand.expertLabel;
const isOnboarded = ref(false);
const needOnboarding = ref(false);
const myActionIds = ref<number[]>([]); // 订阅功能（按订阅顺序）
const showAllActions = ref(false); // 橱窗「查看全部」开关
const onboardingExperts = ref<AgentExpert[]>([]);
const onboardingGroups = ref<QuickActionGroup[]>([]);
const showOnboarding = ref(false); // 首次强制引导（仅此一次，无二次设置入口）

/** 专家清单（@召唤候选与面板共用） */
const expertsList = ref<AgentExpert[]>([]);
async function reloadExperts() {
  const {data, error} = await fetchAgentExperts(false);
  if (!error && data) expertsList.value = data;
}

/** 当前会话驻留专家（@召唤绑定；会话行自带展示名，专家被删时后端置空降级） */
const sessionExpert = computed(() => {
  const s = currentSession.value;
  if (!s?.expertKey) return null;
  const row = expertsList.value.find(e => e.expertKey === s.expertKey);
  return {
    key: s.expertKey,
    name: s.expertName || row?.name || '专家',
    icon: s.expertIcon ?? row?.icon ?? null,
    welcome: row?.welcomeMessage ?? null
  };
});

/** 书桌模式：已引导、正看「我的功能」——卷首语 / 印章 / 水印整体换声，从「宣传册」变「你的书桌」 */
const deskMode = computed(() => isOnboarded.value && !showAllActions.value);

/** 订阅 ID 集合：「全部功能」视图里给已订阅的卡盖「已订阅」小章 */
const mySubscribed = computed(() => new Set(myActionIds.value));

/** 订阅功能（按订阅顺序），橱窗「我的」视图用 */
const mySubscribedActions = computed(() => {
  const byId = new Map(allShowcaseActions.value.map(a => [a.id, a]));
  return myActionIds.value.map(id => byId.get(id)).filter((a): a is QuickAction => Boolean(a));
});

/** 橱窗实际渲染：已引导且未「查看全部」→ 订阅子集；否则全量 */
const showcaseActions = computed<QuickAction[]>(() => {
  if (!isOnboarded.value || showAllActions.value || !myActionIds.value.length) return allShowcaseActions.value;
  return mySubscribedActions.value;
});
const showcaseGroupDefs = computed<QuickActionGroup[]>(() => allShowcaseGroupDefs.value);

async function loadShowcaseActions(force = false) {
  // 30 秒内不重复拉取（简报开合频繁时避免刷请求）
  const now = Date.now();
  if (!force && now - showcaseLastLoadAt < 30_000) return;
  showcaseLastLoadAt = now;
  showcaseLoading.value = true;
  try {
    const { data, error } = await fetchQuickActions();
    if (!error && data) {
      allShowcaseActions.value = data.actions;
      allShowcaseGroupDefs.value = data.groups;
      // 默认选中首个类型分组（按全量章节选，避免书桌模式下订阅子集章节不全）
      if (!activeShowcaseCat.value) {
        const firstCat = exploreGroups.value[0]?.cat ?? showcaseGroups.value[0]?.cat;
        if (firstCat) activeShowcaseCat.value = firstCat;
      }
    }
  } finally {
    showcaseLoading.value = false;
  }
}

/** 拉取新手引导数据：是否需要引导 + 专家清单 + 当前订阅回显 */
async function loadOnboarding() {
  const { data, error } = await fetchExpertOnboarding();
  if (error || !data) return;
  onboardingExperts.value = data.experts;
  onboardingGroups.value = data.groups;
  needOnboarding.value = data.needOnboarding;
  isOnboarded.value = !data.needOnboarding;
  myActionIds.value = data.current?.actionIds ?? [];
  // 无专家或无功能可勾选时，强制引导会是选无可选、关不掉的死胡同 → 不弹，改由橱窗「空白章节」承接
  if (data.needOnboarding && data.experts.length > 0 && data.actions.length > 0) showOnboarding.value = true;
}

/** 引导 / 设置确认后刷新订阅状态与橱窗（复用 onboarding 数据，无需重拉全量） */
async function refreshSubscription() {
  const { data, error } = await fetchExpertOnboarding();
  if (error || !data) return;
  onboardingExperts.value = data.experts;
  isOnboarded.value = !data.needOnboarding;
  needOnboarding.value = false;
  myActionIds.value = data.current?.actionIds ?? [];
}

async function handleOnboardingConfirm(payload: {expertKeys: string[]; actionIds: number[]}) {
  const { error } = await fetchCompleteExpertOnboarding({expertKeys: payload.expertKeys, actionIds: payload.actionIds});
  if (error) {
    window.$message?.error?.('保存失败，请重试');
    return;
  }
  showOnboarding.value = false;
  await refreshSubscription();
  await reloadExperts();
  window.$message?.success?.(`欢迎！已把 ${payload.expertKeys.length} 位${expertLabel}加入你的${expertLabel}库`);
}

function toggleShowAllActions() {
  showAllActions.value = !showAllActions.value;
  // 切回「我的」时若当前章节已不在订阅里，回落到首个可见章节
  if (!showAllActions.value && activeShowcaseGroup.value && !showcaseGroups.value.some(g => g.cat === activeShowcaseCat.value)) {
    activeShowcaseCat.value = showcaseGroups.value[0]?.cat ?? '';
  }
}


/** 章节分组：按类型定义顺序收成员，未挂类型的功能按全局序收进「更多能力」章。
 *  icon 透传分类表（agent_skill_category.icon）里的 svg；「更多能力」为合成章无图标，前端不渲染图标位 */
function buildShowcaseGroups(actions: QuickAction[], defs: QuickActionGroup[]): Array<{cat: string; icon?: string; actions: QuickAction[]}> {
  const actionById = new Map(actions.map(a => [a.id, a]));
  const groups: Array<{cat: string; icon?: string; actions: QuickAction[]}> = [];
  const grouped = new Set<number>();
  for (const g of defs) {
    const members = g.actionIds.map(id => actionById.get(id)).filter((a): a is QuickAction => Boolean(a));
    if (!members.length) continue;
    groups.push({cat: g.name, icon: g.icon, actions: members});
    members.forEach(a => grouped.add(a.id));
  }
  const rest = actions.filter(a => !grouped.has(a.id));
  if (rest.length) groups.push({cat: '更多能力', actions: rest});
  return groups;
}

/**
 * 章节来自后端 groups（类型顺序 + 类型内排序均已在后端算好）。
 * 未挂任何类型的功能按全局序收进「更多能力」章。
 */
const showcaseGroups = computed(() => buildShowcaseGroups(showcaseActions.value, showcaseGroupDefs.value));

/** 全量章节：不受个人订阅裁剪（旧 codex 图鉴回滚链路用） */
const exploreGroups = computed(() => buildShowcaseGroups(allShowcaseActions.value, allShowcaseGroupDefs.value));

/** 类型切换：当前选中的分组（章节形态，一屏看全）
 *  书桌模式没有章节——我的全部常用功能铺成一个虚拟章节，codex-body / 条目页 / 色相表整条链路原样复用 */
const activeShowcaseCat = ref('');
const activeShowcaseGroup = computed(() => {
  if (deskMode.value) return {cat: '我的功能', actions: showcaseActions.value};
  return showcaseGroups.value.find(g => g.cat === activeShowcaseCat.value) || showcaseGroups.value[0] || null;
});


/** 橱窗空态类型：loading=数据加载中；mine=「我的功能」订阅为空（订阅的功能已下架）；global=全站未配置任何功能。
 *  null=正常渲染目录。避免空白首屏看起来像 bug */
const showcaseBlankKind = computed<'loading' | 'mine' | 'global' | null>(() => {
  if (showcaseActions.value.length) return null;
  if (showcaseLoading.value) return 'loading';
  return allShowcaseActions.value.length ? 'mine' : 'global';
});

// ── Codex 图鉴细节 ──
function pad2(n: number): string {
  return String(n).padStart(2, '0');
}

/** 卷首统计的收录日期：YYYY.MM.DD */
const codexDateStr = computed(() => {
  const d = new Date();
  return `${d.getFullYear()}.${String(d.getMonth() + 1).padStart(2, '0')}.${String(d.getDate()).padStart(2, '0')}`;
});

/** 卷首统计的案例总数：当前视图全部功能的案例之和 */
const codexPlateTotal = computed(() => showcaseActions.value.reduce((sum, a) => sum + a.examples.length, 0));

// ── 目录 ⇄ 条目：点开一个功能翻到它的条目页，看它的全部案例 ──
const activeCodexFeatureId = ref<number | null>(null);

/** 当前条目（功能）；数据刷新后若该功能已不在本章，自动回落 null（显示目录） */
const activeCodexFeature = computed(() => {
  if (activeCodexFeatureId.value == null) return null;
  return activeShowcaseGroup.value?.actions.find(a => a.id === activeCodexFeatureId.value) ?? null;
});

const activeCodexFeatureNo = computed(() => {
  const idx = (activeShowcaseGroup.value?.actions || []).findIndex(a => a.id === activeCodexFeatureId.value);
  return pad2(idx < 0 ? 0 : idx + 1);
});

function openCodexFeature(action: QuickAction) {
  activeCodexFeatureId.value = action.id;
}

// ── Codex 色谱：名称哈希定基础色相，同章相邻撞色时让一位 ──
// 与 nian feed-card 同款思路：卡片级 --ca/--ca2 变量驱动全部点缀色
const CODEX_HUES: Array<[string, string]> = [
  ['#1e40af', '#2563eb'], // 藏蓝
  ['#047857', '#10b981'], // 松绿
  ['#b45309', '#f59e0b'], // 琥珀
  ['#7c3aed', '#a855f7'], // 紫罗兰
  ['#be185d', '#ec4899'], // 品红
  ['#0e7490', '#06b6d4'] // 青碧
];

function codexHashHue(action: QuickAction): number {
  let h = action.id;
  for (const ch of action.name) {
    h = (h * 31 + ch.codePointAt(0)!) >>> 0;
  }
  return h % CODEX_HUES.length;
}

/** 当前章节色相表：哈希撞色时顺移一个色位，保证相邻卡片不重色（数据不变则结果稳定） */
const codexChapterHues = computed(() => {
  const map = new Map<number, number>();
  let prev = -1;
  for (const action of activeShowcaseGroup.value?.actions ?? []) {
    let h = codexHashHue(action);
    if (h === prev) h = (h + 1) % CODEX_HUES.length;
    map.set(action.id, h);
    prev = h;
  }
  return map;
});

function codexCardStyle(action: QuickAction, index: number): string {
  const hi = codexChapterHues.value.get(action.id) ?? codexHashHue(action);
  const [ca, ca2] = CODEX_HUES[hi] ?? CODEX_HUES[0];
  // --holo：镭射丝带动效的相位错开量，整排卡不同步闪烁
  return `--delay:${Math.min(index * 60, 300)}ms;--holo:${-(index * 0.55)}s;--ca:${ca};--ca2:${ca2}`;
}

function codexEntryStyle(action: QuickAction): string {
  const hi = codexChapterHues.value.get(action.id) ?? codexHashHue(action);
  const [ca, ca2] = CODEX_HUES[hi] ?? CODEX_HUES[0];
  return `--ca:${ca};--ca2:${ca2}`;
}

// ── 卡片鼠标响应：灯光（spotlight 跟随光标）+ 纸片微倾（tilt） ──
function onCodexCardMove(e: MouseEvent) {
  const el = e.currentTarget as HTMLElement;
  const rect = el.getBoundingClientRect();
  const px = (e.clientX - rect.left) / rect.width;
  const py = (e.clientY - rect.top) / rect.height;
  el.style.setProperty('--mx', `${(px * 100).toFixed(2)}%`);
  el.style.setProperty('--my', `${(py * 100).toFixed(2)}%`);
  el.style.setProperty('--tilt-y', `${((px - 0.5) * 7).toFixed(2)}deg`);
  el.style.setProperty('--tilt-x', `${((0.5 - py) * 7).toFixed(2)}deg`);
}

function onCodexCardLeave(e: MouseEvent) {
  const el = e.currentTarget as HTMLElement;
  el.style.removeProperty('--mx');
  el.style.removeProperty('--my');
  el.style.removeProperty('--tilt-x');
  el.style.removeProperty('--tilt-y');
}

// 舞台视差：环境层（巨型章节水印 + 点阵）随鼠标反向慢漂，做出景深
function onCodexStageMove(e: MouseEvent) {
  const el = e.currentTarget as HTMLElement;
  const rect = el.getBoundingClientRect();
  const px = (e.clientX - rect.left) / rect.width - 0.5;
  const py = (e.clientY - rect.top) / rect.height - 0.5;
  el.style.setProperty('--parx', `${(px * -16).toFixed(1)}px`);
  el.style.setProperty('--pary', `${(py * -12).toFixed(1)}px`);
}

function onCodexStageLeave(e: MouseEvent) {
  const el = e.currentTarget as HTMLElement;
  el.style.removeProperty('--parx');
  el.style.removeProperty('--pary');
}

/** 章节号水印：条目页时显示条目编号，翻的是哪一页一目了然 */
const activeChapterNo = computed(() => {
  if (activeCodexFeature.value) return activeCodexFeatureNo.value;
  const idx = showcaseGroups.value.findIndex(g => g.cat === (activeShowcaseGroup.value?.cat ?? ''));
  return pad2(idx < 0 ? 0 : idx + 1);
});

// ── 章节游标：极光指示条，随选中章节滑动 ──
// 游标按「活动 tab 的几何」定位（含垂直位置）：窄窗里章节会换行成两排，
// 若只给 left/width 并钉死 bottom，选中条会永远贴容器底、视觉上飘到第二行身上
const chapterTabEls = ref<(HTMLElement | null)[]>([]);
const chapterCursor = reactive({ left: 0, top: 0, width: 0 });

function setChapterTabRef(el: Element | null, i: number) {
  chapterTabEls.value[i] = el as HTMLElement | null;
}

function measureChapterCursor() {
  const idx = showcaseGroups.value.findIndex(g => g.cat === (activeShowcaseGroup.value?.cat ?? ''));
  const el = idx >= 0 ? chapterTabEls.value[idx] : null;
  if (el) {
    chapterCursor.left = el.offsetLeft;
    chapterCursor.width = el.offsetWidth;
    chapterCursor.top = el.offsetTop + el.offsetHeight + 1;
  }
}

watch([activeShowcaseCat, showcaseGroups], () => {
  nextTick(measureChapterCursor);
});

// 切回浏览模式（卷首重新可见）后重测章节游标——书桌模式下卷首隐藏，游标测量无效
watch(deskMode, v => {
  if (!v) nextTick(measureChapterCursor);
});

// 容器宽度变化（浮窗拖动/换行与否切换）→ 各 tab 位置全部改变，游标必须重测
const chapterNavRef = ref<HTMLElement | null>(null);
let chapterNavRO: ResizeObserver | null = null;
watch(chapterNavRef, el => {
  chapterNavRO?.disconnect();
  chapterNavRO = null;
  if (!el) return;
  chapterNavRO = new ResizeObserver(() => measureChapterCursor());
  chapterNavRO.observe(el); // observe 首次即回调，兼顾 nav 条件渲染重新挂载的场景
});
onBeforeUnmount(() => chapterNavRO?.disconnect());

// 切类型回目录（条目只属于当前章节视图）
watch(activeShowcaseCat, () => {
  activeCodexFeatureId.value = null;
});

/** 定时任务抽屉「到会话中创建」：把起手文案塞进输入框并聚焦（与 insertActionSkill 同款拼接逻辑） */
function onTaskDrawerFill(text: string) {
  const currentValue = inputText.value;
  const needSpace = currentValue.length > 0 && !/\s$/.test(currentValue);
  inputText.value = currentValue + (needSpace ? ' ' : '') + text;
  nextTick(() => composerRef.value?.focus());
}

/** 点功能卡：把 @技能 塞进输入框并聚焦（与 QAComposer 的 insertQuickAction 一致）；
 *  未关联技能的功能用功能名做起手文案，保证点了总有响应 */
function insertActionSkill(action: QuickAction) {
  if (running.value) return;
  const seed = action.skillKey ? `@${action.skillKey} ` : `${action.name}：`;
  const currentValue = inputText.value;
  const needSpace = currentValue.length > 0 && !/\s$/.test(currentValue);
  inputText.value = currentValue + (needSpace ? ' ' : '') + seed;
  nextTick(() => composerRef.value?.focus());
}

// 案例缩略图：悬停横向扫动切换预览帧（与 QAComposer 同款交互）
const scrubExampleId = ref<number | null>(null);
const scrubIndex = ref(0);

function exampleImages(ex: QuickActionExample): string[] {
  if (ex.previewImages?.length) return ex.previewImages;
  if (ex.previewImage) return [ex.previewImage];
  return [];
}

function showcaseImgUrl(path?: string): string {
  if (!path) return '';
  const isHttpProxy = import.meta.env.DEV && import.meta.env.VITE_HTTP_PROXY === 'Y';
  const { baseURL } = getServiceBaseURL(import.meta.env, isHttpProxy);
  const origin = /^https?:\/\/[^/]+/.exec(baseURL)?.[0] ?? '';
  return origin + path;
}

function onShowcaseThumbMove(e: MouseEvent, ex: QuickActionExample) {
  scrubExampleId.value = ex.id;
  const images = exampleImages(ex);
  if (images.length <= 1) return;
  const el = e.currentTarget as HTMLElement;
  const rect = el.getBoundingClientRect();
  const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
  scrubIndex.value = Math.min(images.length - 1, Math.floor(ratio * images.length));
}

function onShowcaseThumbLeave() {
  scrubExampleId.value = null;
  scrubIndex.value = 0;
}

const capabilities = brand.qaCapabilities;
const isGeneric = getBrandVariant() === 'generic';

// ───── Distill session → skill ──────────────────────────────────────────
const distilling = ref(false);
const distillResult = ref<AgentSkill | null>(null);
const distillError = ref('');

async function handleDistill() {
  if (!currentSessionKey.value || distilling.value) return;
  distilling.value = true;
  distillError.value = '';
  distillResult.value = null;
  try {
    await fetchDistillSkillStream(
      {session_key: currentSessionKey.value},
      ev => {
        if (ev.type === 'done') {
          distillResult.value = ev.skill;
          reloadSkills();
        } else if (ev.type === 'error') {
          distillError.value = ev.message || '凝练失败';
        }
      }
    );
  } catch (err: any) {
    if (err?.name !== 'AbortError') {
      distillError.value = err?.message || '请求失败';
    }
  } finally {
    distilling.value = false;
  }
}

function closeDistillResult() {
  distillResult.value = null;
  distillError.value = '';
}

// ───── 导出对话分享图 ──────────────────────────────────────────────────
const exportingImage = ref(false);

async function handleExportImage() {
  if (exportingImage.value) return;
  if (running.value) {
    window.$message?.info?.('AI 正在回复，完成后再导出分享图');
    return;
  }
  const node = scrollEl.value?.querySelector<HTMLElement>('.conversation');
  if (!node) {
    window.$message?.error?.('未找到可导出的任务内容');
    return;
  }
  exportingImage.value = true;
  try {
    await exportConversationAsImage({
      node,
      title: currentSession.value?.title || '新任务',
      brandName: brand.qaSidebarTitle,
      assistantName: brand.assistantName,
      exchangeCount: messages.value.filter(m => m.role === 'user' && !isFunctionalUserMsg(m)).length
    });
  } catch (err: any) {
    console.error('[qa-glass] 导出分享图失败', err);
    window.$message?.error?.('生成分享图失败，请重试');
  } finally {
    exportingImage.value = false;
  }
}

const isMobile = ref(false);

function checkResponsive() {
  // 嵌入模式：强制收起侧栏，避免挤占抽屉空间
  if (props.embedded) {
    isMobile.value = false;
    sidebarOpen.value = false;
    composerRef.value?.resetHeight();
    return;
  }
  const w = window.innerWidth;
  const narrow = w < 960;
  isMobile.value = narrow;
  if (boardPanelVisible.value) {
    // 面板在场：侧栏一律收起，把空间让给对话+板（用户仍可手动点汉堡展开）
    sidebarOpen.value = false;
    if (narrow) nextTick(() => composerRef.value?.resetHeight());
  } else if (narrow) {
    // 窄屏默认收起；用户主动点击汉堡才展开
    sidebarOpen.value = false;
    // 重算 textarea 高度（桌面↔手机切换后按当前内容得到正确高度）
    nextTick(() => composerRef.value?.resetHeight());
  } else {
    sidebarOpen.value = true;
    // 切回桌面时清掉行内高度，让 rows 属性接管
    composerRef.value?.resetHeight();
  }
}
// 面板开合即联动侧栏：打开→收起让位；收起→桌面宽度恢复展开
watch(boardPanelVisible, v => {
  if (props.embedded) return;
  if (v) sidebarOpen.value = false;
  else if (window.innerWidth >= 960) sidebarOpen.value = true;
});

function closeSidebarIfMobile() {
  if (isMobile.value) sidebarOpen.value = false;
}

const route = useRoute();
const router = useRouter();
const authStore = useAuthStore();

/** 用户名：与侧栏底部用户信息条（UserAvatar）同源——nickName 优先、缺省回落 userName。
 *  备用：卷首标题暂用职业名（「xx职业的工作台」），日后想改回「xx(用户名)的工作台」直接用它 */
// eslint-disable-next-line @typescript-eslint/no-unused-vars
const userDisplayName = computed(() => authStore.userInfo.nickName || authStore.userInfo.userName);

function handleOpenWorkflow() {
  router.push({name: 'ai_workflow'});
}

// 标准编号点击：事件委托到 document，匹配 .std-no-active
function handleStdNoClick(ev: MouseEvent) {
  const target = ev.target as HTMLElement | null;
  if (!target) return;
  const link = target.closest<HTMLElement>('.std-no-active');
  if (!link) return;
  ev.preventDefault();
  ev.stopPropagation();
  const stdNo = link.dataset.stdNo || '';
  // 从 DOM 或缓存中查找 ID
  const stdId = link.dataset.stdId || stdNoCache.get(stdNo)?.id || '';
  if (stdId) {
    selectedStdId.value = stdId;
    showStdDetail.value = true;
  } else {
    window.$message?.info?.(`标准 ${stdNo} 正在验证中，请稍后再试`);
  }
}

// 复制代码块：事件委托到 document，匹配 .code-copy-btn
async function handleCodeCopyClick(ev: MouseEvent) {
  const target = ev.target as HTMLElement | null;
  if (!target) return;
  const btn = target.closest<HTMLButtonElement>('.code-copy-btn');
  if (!btn) return;
  ev.preventDefault();
  ev.stopPropagation();
  const wrapper = btn.closest<HTMLElement>('.code-block');
  const codeEl = wrapper?.querySelector<HTMLElement>('pre code');
  const text = codeEl?.innerText ?? '';
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.left = '-9999px';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
    }
    btn.classList.add('is-done');
    const textEl = btn.querySelector<HTMLElement>('.ccb-text');
    const prevText = textEl?.textContent ?? '复制';
    if (textEl) textEl.textContent = '已复制';
    window.setTimeout(() => {
      btn.classList.remove('is-done');
      if (textEl) textEl.textContent = prevText;
    }, 1500);
  } catch (err) {
    btn.classList.add('is-error');
    window.setTimeout(() => btn.classList.remove('is-error'), 1500);
  }
}

// 把当前会话状态同步到 URL：sid=会话key 或 sid=new（草稿），wk=伴侣面板挂板（草稿会话刷新后也能恢复面板），并清掉首页一次性参数
function syncSessionToUrl() {
  // 嵌入模式：不修改宿主页面的 URL
  if (props.embedded) return;
  const q = {...route.query};
  const sidVal = currentSessionKey.value || 'new';
  let changed = false;
  for (const k of ['t', 'skill', 'target'] as const) {
    if (q[k] !== undefined) {
      delete q[k];
      changed = true;
    }
  }
  if (q.sid !== sidVal) {
    q.sid = sidVal;
    changed = true;
  }
  const wkVal = attachedWorkflowKey.value || '';
  if (wkVal) {
    if (q.wk !== wkVal) {
      q.wk = wkVal;
      changed = true;
    }
  } else if (q.wk !== undefined) {
    delete q.wk;
    changed = true;
  }
  if (changed) router.replace({query: q});
}

// currentSessionKey 任何变化都同步到 URL，覆盖发送首条消息后草稿落库的场景
watch(currentSessionKey, () => syncSessionToUrl());

// ───── 每日简报 ──────────────────────────────────────────────────────────────

const briefState = reactive({
  visible: false,
  loading: false,
  topHtml: '',
  middleHtml: '',
  skills: [] as Array<{ display: string; prompt: string }>,
  error: null as string | null,
});

// 简报收起后橱窗重新挂载：游标重新测量 + 静默拉取最新功能数据（管理页新配的功能/案例无需整页刷新）
watch(() => briefState.visible, (visible) => {
  if (!visible) {
    nextTick(measureChapterCursor);
    loadShowcaseActions();
  }
});

// 每日简报总开关：默认关闭，localStorage 持久化（用户菜单里勾选）。
// 关闭 = 隐藏入口（侧栏灰卡 + 首屏浅蓝 pill，样张同款位置）+ 不做任何自动生成
const briefEnabled = ref<boolean>(localStg.get('qaBriefEnabled') ?? false);
watch(briefEnabled, (v) => localStg.set('qaBriefEnabled', v));

// 编辑个人资料弹窗
const profileModalOpen = ref(false);

function handleOpenProfile() {
  if (isMobile.value) sidebarOpen.value = false; // 移动端先收侧栏再开弹窗
  profileModalOpen.value = true;
}

// ── D 版加载动画状态 ──
const dlpTwEl = ref<HTMLElement | null>(null);
const dlpStep = ref(0);
const dlpSecVis = ref(0);
const dlpFootText = ref('正在生成今日简报…');
const dlpCount = ref(0);

const dlpDateStr = computed(() => {
  const d = new Date();
  return `${d.getFullYear()}.${String(d.getMonth() + 1).padStart(2, '0')}.${String(d.getDate()).padStart(2, '0')}  ${['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][d.getDay()]}`;
});

let dlpTimers: ReturnType<typeof setTimeout>[] = [];

function startDlpAnimation() {
  dlpTimers.forEach(clearTimeout);
  dlpTimers = [];
  dlpStep.value = 0;
  dlpSecVis.value = 0;
  dlpCount.value = 0;
  dlpFootText.value = '正在生成今日简报…';

  const phrases = [
    '正在读取今日动态…',
    '分析标准政策动态…',
    '整理行业要闻…',
    '提取技术前沿信息…',
    '汇总市场数据…',
    '分析关键信息中…',
    '整理简报结构中…',
    '生成摘要内容…',
    '校对简报内容…',
    '正在生成今日简报…',
  ];
  let pi = 0, ci = 0;
  function type() {
    if (!briefState.loading) return;
    const el = dlpTwEl.value;
    if (!el) { dlpTimers.push(setTimeout(type, 100)); return; }
    if (ci <= phrases[pi].length) {
      el.textContent = phrases[pi].slice(0, ci);
      ci++;
      dlpTimers.push(setTimeout(type, ci === phrases[pi].length + 1 ? 3500 : 58));
    } else {
      ci = 0; pi = (pi + 1) % phrases.length;
      el.textContent = '';
      dlpTimers.push(setTimeout(type, 300));
    }
  }
  type();

  // 步骤：均匀分布在 4 分钟内（读取→分析→整理→生成）
  [0, 60_000, 120_000, 210_000].forEach((delay, i) => {
    dlpTimers.push(setTimeout(() => { if (briefState.loading) dlpStep.value = i + 1; }, delay));
  });

  // 章节骨架：前 25 秒内逐一出现，不必等太久
  [500, 8_000, 15_000, 23_000].forEach((delay, i) => {
    dlpTimers.push(setTimeout(() => { if (briefState.loading) dlpSecVis.value = i + 1; }, delay));
  });

  // footer 文字每 30 秒切换一次
  const footerPhrases = [
    '正在读取今日动态…',
    '分析关键信息中…',
    '整理简报结构中…',
    '生成摘要内容…',
    '校对并完善中…',
    '即将完成，请稍候…',
    '正在生成今日简报…',
    '内容生成中，预计还需一会儿…',
    '快完成了，请耐心等待…',
    '正在做最后整理…',
  ];
  footerPhrases.forEach((t, i) => {
    dlpTimers.push(setTimeout(() => { if (briefState.loading) dlpFootText.value = t; }, i * 30_000));
  });

  // 条动态数字：60 秒内从 0 跳到 42，之后停住
  dlpTimers.push(setTimeout(() => {
    let c = 0;
    const ti = setInterval(() => {
      if (!briefState.loading) { clearInterval(ti); return; }
      c = Math.min(c + 1, 42);
      dlpCount.value = c;
      if (c >= 42) clearInterval(ti);
    }, 1400);
    dlpTimers.push(ti as unknown as ReturnType<typeof setTimeout>);
  }, 2000));
}

watch(() => briefState.loading, (loading) => {
  if (loading) {
    nextTick(() => startDlpAnimation());
  } else {
    dlpTimers.forEach(clearTimeout);
    dlpTimers = [];
  }
});

// 后端返回 generating（另一请求正在生成）时的延迟重试间隔
const BRIEF_RETRY_MS = 8_000;
let briefRetryTimer: ReturnType<typeof setTimeout> | null = null;
// 在途简报流的 abort 控制器：关闭总开关时中止生成
let briefAbortCtrl: AbortController | null = null;

function scheduleBriefRetry() {
  if (briefRetryTimer) return;
  briefRetryTimer = setTimeout(async () => {
    briefRetryTimer = null;
    if (!briefState.loading) return;
    if (briefState.topHtml || briefState.middleHtml) return; // 内容已由在途流送达
    await fetchDailyBriefStreamOnce();
  }, BRIEF_RETRY_MS);
}

// 用户菜单切换「启用今日简报」：关闭时中止在途流、收起面板、清空半截内容
function setBriefEnabled(v: boolean) {
  briefEnabled.value = v;
  if (v) return;
  briefAbortCtrl?.abort();
  if (briefRetryTimer) {
    clearTimeout(briefRetryTimer);
    briefRetryTimer = null;
  }
  briefState.visible = false;
  briefState.loading = false;
  briefState.topHtml = '';
  briefState.middleHtml = '';
  briefState.skills = [];
  briefState.error = null;
}

// 单次流请求；generating 态由 scheduleBriefRetry() 接力，不重置已有内容
async function fetchDailyBriefStreamOnce() {
  let isGenerating = false;
  try {
    await fetchDailyBriefStream(
      (event: DailyBriefEvent) => {        if (event.type === 'cached') {
          // 等待后续 section 事件
        } else if (event.type === 'generating') {
          // 后端正在生成中（另一请求已触发）：保持 loading，稍后重试
          isGenerating = true;
        } else if (event.type === 'section') {
          if (event.name === 'top') {
            briefState.topHtml = event.html;
          } else if (event.name === 'middle') {
            briefState.middleHtml = event.html;
          }
        } else if (event.type === 'skills') {
          briefState.skills = event.items || [];
        } else if (event.type === 'done') {
          if (isGenerating && !briefState.topHtml && !briefState.middleHtml) {
            scheduleBriefRetry();
          } else {
            briefState.loading = false;
          }
        } else if (event.type === 'error') {
          briefState.loading = false;
          briefState.error = `简报生成失败：${event.message}`;
        }
      },
      briefAbortCtrl?.signal
    );
  } catch (e: any) {
    if (e?.name === 'AbortError') return; // 关闭总开关时主动中止，静默即可
    briefState.loading = false;
    briefState.error = `简报生成失败：${e?.message || String(e)}`;
  }
}

async function triggerDailyBriefIfNeeded(expand = true) {
  if (!briefEnabled.value) return; // 总开关未启用：不自动生成
  const uid = authStore.userInfo?.userId;
  if (!uid) return;

  // 已有流在途（生成未结束）：不重复发请求；在途流会持续回填内容
  if (briefState.loading) {
    if (expand) briefState.visible = true;
    return;
  }

  if (expand) briefState.visible = true;
  briefState.loading = true;
  briefState.topHtml = '';
  briefState.middleHtml = '';
  briefState.skills = [];
  briefState.error = null;

  briefAbortCtrl = new AbortController();
  await fetchDailyBriefStreamOnce();
}

function useBriefSkill(prompt: string) {
  briefState.visible = false;
  inputText.value = prompt;
  nextTick(() => composerRef.value?.focus());
}

function openBrief() {
  if (briefState.visible) return;
  // 内容已就绪（缓存命中 / 后台预生成完成）：直接展开，不清空重取
  if (!briefState.loading && (briefState.topHtml || briefState.middleHtml)) {
    briefState.visible = true;
    return;
  }
  // 生成中：只展开面板复用 loading 卡片；否则触发新生成
  triggerDailyBriefIfNeeded();
}

// 阻止方向键滚动页面，让 iframe 内的游戏能独占方向键控制
function handleGlobalKeydown(e: KeyboardEvent) {
  const ARROW_KEYS = new Set(["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"]);
  if (!ARROW_KEYS.has(e.key)) return;
  const tag = (e.target as HTMLElement)?.tagName;
  const isEditable =
    tag === "INPUT" ||
    tag === "TEXTAREA" ||
    (e.target as HTMLElement)?.isContentEditable;
  if (!isEditable) e.preventDefault();
}

/** 技能起手文案：「编辑」技能带引导话术，其余技能仅预填 @召唤 */
function buildSkillPrefill(skill: string, target: string): string {
  if (skill === '编辑' && target) return `@编辑 @${target} 帮我修改这个技能，我想调整的是：`;
  return target ? `@${skill} @${target} ` : `@${skill} `;
}

// ── 「创建技能」首次说明弹窗：第一次点击时展示，用户显式确认知晓后（localStg）不再出现 ──
const skillIntroShow = ref(false);

/** 侧栏「创建技能」：首次点击弹说明弹窗；已确认知晓的，直接进入创建流程 */
function handleCreateSkill() {
  if (!localStg.get('qaSkillCreateIntroSeen')) {
    closeSidebarIfMobile(); // 移动端先收侧栏，让弹窗清晰可见
    skillIntroShow.value = true;
    return;
  }
  prefillCreateSkill();
}

/** 侧栏「创建技能」行右侧「?」：随时重新查看说明弹窗（不受首次知晓标记限制） */
function reopenSkillIntro() {
  closeSidebarIfMobile();
  skillIntroShow.value = true;
}

/** ×/点遮罩：仅关闭弹窗，不算知晓——下次点击「创建技能」仍会展示 */
function dismissSkillIntro() {
  skillIntroShow.value = false;
}

/** 「知道了」：用户显式确认知晓，之后不再展示 */
function ackSkillIntro() {
  skillIntroShow.value = false;
  localStg.set('qaSkillCreateIntroSeen', true);
}

/** 「现在就创建」：确认知晓并直接进入创建流程 */
function startSkillFromIntro() {
  ackSkillIntro();
  prefillCreateSkill();
}

/** 与技能面板「创建技能 → 到会话中创建」同行为：
 *  预填起手文案并聚焦输入框，由 AI 在对话里把需求凝练成技能落库 */
function prefillCreateSkill() {
  const prefill = '帮我创建一个技能：';
  inputText.value = inputText.value.trim() ? `${inputText.value.replace(/\s+$/, '')} ${prefill}` : prefill;
  closeSidebarIfMobile();
  nextTick(() => composerRef.value?.focus());
}

/* ─── 主题（极光玻璃 / Kimi 风，状态与持久化见 qa-theme.ts） ─── */
/** 旧 codex 图鉴首屏回滚开关：2026-08 起双主题统一走样张新首屏，此开关仅备回滚 */
const legacyCodexEnabled = ref(false);
/** 空会话 + 简报未展开 = 新首屏（wordmark + 大输入框主角 + pill 排，双主题共用）
 *  注意：script setup 闭包里 ref/computed 不自动解包，必须 .value
 *  历史加载期间（loadingKeys）不算空会话——否则慢切换会闪现首屏 */
const showNewWelcome = computed(() =>
  messages.value.length === 0 && !briefState.visible && !loadingKeys.has(currentSessionKey.value));
/** 会话切换中：历史未返回，既不渲染首屏也不渲染对话，显示轻量加载占位 */
const isSwitchingSession = computed(() =>
  !!currentSessionKey.value && loadingKeys.has(currentSessionKey.value) && messages.value.length === 0);
const kimiWordMain = computed(() => brand.qaSidebarTitle);

/** 探索案例区数据：分类 → 案例两级平铺（每条案例带所属技能供卡上回显；无案例的分类不出现）
 *  ——传给 WelcomeShowcase。分组顺序沿用 exploreGroups（后端 sort_order 序），与 pill 排一致。
 *  pill/技能区开关、滚动锚点、缩略图扫动等交互全部内聚在 WelcomeShowcase 组件里 */
const exploreCaseGroups = computed(() =>
  exploreGroups.value
    .map(g => ({
      cat: g.cat,
      icon: g.icon,
      cases: g.actions.flatMap(a => a.examples.map(example => ({ action: a, example })))
    }))
    .filter(g => g.cases.length > 0)
);

onMounted(async () => {
  applyQaTheme();
  await Promise.all([reloadSessions(), reloadSkills(), reloadExperts(), loadConnectors(), loadShowcaseActions(), loadOnboarding(), loadChatModePref()]);

  const initialSkill = route.query.skill;
  const initialTarget = route.query.target;
  const initialSid = typeof route.query.sid === 'string' ? route.query.sid : '';
  const fromWorkbench = route.query.t !== undefined;

  if (props.sessionKey) {
    // 工作流页面嵌入：加载指定会话（或新建）
    if (sessions.value.some(s => s.sessionKey === props.sessionKey)) {
      await loadSession(props.sessionKey);
    } else {
      await startNewSession();
    }
  } else if (props.embedded && props.workflowKey) {
    // 工作流画板嵌入：默认加载本工作流最近对话过的会话（列表已按 update_time 倒序）；无历史则新会话
    if (sessions.value.length) await loadSession(sessions.value[0].sessionKey);
    else await startNewSession();
  } else if (props.prefill) {
    // 嵌入抽屉模式：预填知识标题，直接进入新会话（简报默认折叠，后台预生成）
    inputText.value = props.prefill;
    triggerDailyBriefIfNeeded(false);
    nextTick(() => composerRef.value?.focus());
  } else if (typeof initialSkill === 'string' && initialSkill) {
    // 首页跳转并指定技能
    await startNewSession();
    inputText.value = buildSkillPrefill(initialSkill, typeof initialTarget === 'string' ? initialTarget : '');
    nextTick(() => composerRef.value?.focus());
  } else if (initialSid === 'new' || fromWorkbench) {
    // 首页跳转或明确新建会话（简报默认折叠，后台预生成）
    await startNewSession();
    triggerDailyBriefIfNeeded(false);
  } else if (initialSid && sessions.value.some(s => s.sessionKey === initialSid)) {
    // 指定了具体会话
    await loadSession(initialSid);
  } else {
    // 无路由参数：新建空白会话，简报默认折叠，后台预生成，点侧栏灰卡 / 首屏 pill 展开
    await startNewSession();
    triggerDailyBriefIfNeeded(false);
  }

  // 伴侣面板：URL 带 wk 时恢复挂板（草稿会话没有 workflowKey 归属，靠 URL 恢复；带板会话由 watch(currentSessionKey) 恢复）
  if (!props.embedded) {
    const urlWk = typeof route.query.wk === 'string' ? route.query.wk : '';
    if (urlWk) {
      attachedWorkflowKey.value = urlWk;
      boardPanelOpen.value = true;
    }
  }

  checkResponsive();
  window.addEventListener('resize', checkResponsive);
  window.addEventListener('resize', measureChapterCursor);
  document.addEventListener('click', handleStdNoClick);
  document.addEventListener('click', handleCodeCopyClick);
  document.addEventListener('click', handleTruncateOutsideClick);
  if (!props.embedded) document.addEventListener('keydown', handleGlobalKeydown);
  nextTick(updateActiveQuestion);
});

// 首页再次跳过来时（组件已挂载、onMounted 不再触发），通过 watch 响应
watch(
  () => route.query.t,
  async (t, prev) => {
    if (props.embedded) return;
    if (!t || t === prev) return;
    const skill = route.query.skill;
    const target = route.query.target;
    await startNewSession();
    if (typeof skill === 'string' && skill) {
      inputText.value = buildSkillPrefill(skill, typeof target === 'string' ? target : '');
      await reloadSkills();
    } else {
      inputText.value = '';
    }
    nextTick(() => composerRef.value?.focus());
  }
);

// 画板内切换到另一个工作流（组件已挂载）：重拉本工作流会话并加载最近一条（无历史则新会话）
watch(
  () => props.workflowKey,
  async (wk, prev) => {
    if (!props.embedded || !wk || wk === prev) return;
    await reloadSessions();
    if (sessions.value.length) await loadSession(sessions.value[0].sessionKey);
    else await startNewSession();
  }
);

onBeforeUnmount(() => {
  if (briefRetryTimer) {
    clearTimeout(briefRetryTimer);
    briefRetryTimer = null;
  }
  window.removeEventListener('resize', checkResponsive);
  window.removeEventListener('resize', measureChapterCursor);
  window.removeEventListener('pointermove', onPanelResizeMove);
  window.removeEventListener('pointerup', onPanelResizeUp);
  document.removeEventListener('click', handleStdNoClick);
  document.removeEventListener('click', handleCodeCopyClick);
  document.removeEventListener('click', handleTruncateOutsideClick);
  if (!props.embedded) document.removeEventListener('keydown', handleGlobalKeydown);
  for (const key of Object.keys(activeChatAborts)) {
    activeChatAborts[key]?.abort();
  }
  for (const key of Object.keys(pollingTimers)) {
    clearTimeout(pollingTimers[key]);
    delete pollingTimers[key];
  }
});

// 暴露给父组件（工作流弹窗用它切换/新建会话、读取会话列表、感知响应中状态以锁定画布编辑）
defineExpose({
  sessions,
  currentSessionKey,
  running,
  loadSession,
  startNewSession,
  reloadSessions,
  /** 程序化发送一条消息（工作流画板用：人工核查作答后自动触发 Agent 响应）。running 中或空文本会被 sendSingle 内部拒绝 */
  sendMessage: sendSingle,
});
</script>

<template>
  <div :class="{ 'sidebar-collapsed': !sidebarOpen, 'is-embedded': embedded, 'kimi-welcome-on': showNewWelcome, 'board-panel-open': boardPanelVisible, 'board-panel-resizing': boardPanelResizing, 'board-panel-chat-cramped': chatColumnCramped }" :style="boardPanelGridStyle" class="qa-shell">
    <div aria-hidden="true" class="qa-grain" />

    <Transition name="kb-toast-fade">
      <div v-if="kbToast.visible" :class="`kb-toast kb-toast-${kbToast.tone}`">
        <span class="kb-toast-mark">§</span>
        <span class="kb-toast-msg">{{ kbToast.message }}</span>
      </div>
    </Transition>

    <!-- ─── Sidebar (history) ────────────────────────────────────────── -->
    <div
      v-if="sidebarOpen && isMobile"
      class="qa-sidebar-mask"
      aria-hidden="true"
      @click="sidebarOpen = false"
    />
    <QASidebar
      :grouped-sessions="groupedSessions"
      :current-session-key="currentSessionKey"
      :sessions="sessions"
      :renaming-key="renamingKey"
      :renaming-title="renamingTitle"
      :running-sessions="runningSessions"
      :brief-enabled="briefEnabled"
      @new-session="handleSidebarNewSession"
      @toggle-sidebar="!embedded && (sidebarOpen = !sidebarOpen)"
      @open-tasks="taskDrawerOpen = true"
      @open-workflow="handleOpenWorkflow"
      @open-dataset="() => composerRef?.openSkillPanel('dataset')"
      @open-skill="() => composerRef?.openSkillPanel()"
      @create-skill="handleCreateSkill"
      @show-skill-intro="reopenSkillIntro"
      @open-search="searchModalShow = true"
      @open-profile="handleOpenProfile"
      @open-brief="openBrief()"
      @update:brief-enabled="setBriefEnabled"
      @load-session="loadSession"
      @start-rename="startRename"
      @commit-rename="commitRename"
      @cancel-rename="cancelRename"
      @toggle-star="toggleStar"
      @delete-session="deleteSession"
      @update:renaming-title="renamingTitle = $event"
    />

    <SessionSearchModal v-model:show="searchModalShow" @select="handleSearchSelect" />

    <!-- 编辑个人资料 -->
    <ProfileModal v-model:show="profileModalOpen" />

    <!-- 「创建技能」首次说明弹窗（仅用户点「知道了/现在就创建」才算知晓，×/遮罩关闭下次仍展示） -->
    <SkillIntroModal :show="skillIntroShow" @dismiss="dismissSkillIntro" @ack="ackSkillIntro" @start="startSkillFromIntro" />

    <!-- 首次强制引导：多选专家（入库）→ 勾选技能；仅初次进入弹一次，无二次设置入口 -->
    <OnboardingModal
      v-model:show="showOnboarding"
      :experts="onboardingExperts"
      :actions="allShowcaseActions"
      :groups="onboardingGroups.length ? onboardingGroups : allShowcaseGroupDefs"
      @confirm="handleOnboardingConfirm"
    />

    <!-- ─── Main column ────────────────────────────────────────────── -->
    <main class="qa-main">
      <QATopBar
        v-if="!hideTopbar"
        :current-session-title="currentSession?.title || '新任务'"
        :messages-length="messages.length"
        :sedimenting-kb="sedimentingKb"
        :distilling="distilling"
        :has-session-key="!!currentSessionKey"
        :sediment-menu-open="sedimentMenuOpen"
        :exporting-image="exportingImage"
        :streaming="running"
        :sidebar-collapsed="!sidebarOpen"
        @toggle-sidebar="!embedded && (sidebarOpen = !sidebarOpen)"
        @new-session="handleSidebarNewSession"
        @go-to-nian="$router.push('/ai/nian')"
        @sediment-session="handleSedimentSession"
        @distill="handleDistill"
        @toggle-sediment-menu="sedimentMenuOpen = !sedimentMenuOpen"
        @close-sediment-menu="closeSedimentMenu"
        @pick-sediment="onPickSediment"
        @export-image="handleExportImage"
      />

      <div class="qa-stage">
        <!-- 每日简报面板（入口统一在侧栏灰卡 + 首屏浅蓝 pill，见样张 ink-theme-preview.html） -->
        <Transition name="brief-fade">
          <section v-if="briefState.visible" class="qa-brief">
            <!-- 加载中：今日简报生成中卡片 -->
            <div v-if="briefState.loading && !briefState.topHtml && !briefState.middleHtml" class="dlp-card">
              <div class="dlp-topbar">
                <div class="dlp-logo">
                  <div class="dlp-logo-icon">
                    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
                      <rect x="2" y="1" width="12" height="14" rx="2" stroke="white" stroke-width="1.4" />
                      <line x1="5" y1="5" x2="11" y2="5" stroke="white" stroke-width="1.4" stroke-linecap="round" />
                      <line x1="5" y1="8" x2="11" y2="8" stroke="white" stroke-width="1.4" stroke-linecap="round" />
                      <line x1="5" y1="11" x2="8.5" y2="11" stroke="white" stroke-width="1.4" stroke-linecap="round" />
                    </svg>
                  </div>
                  <div class="dlp-logo-name">今日简报</div>
                </div>
                <div class="dlp-topright">
                  <div class="dlp-date-chip">{{ dlpDateStr }}</div>
                  <div class="dlp-status-badge"><span class="dlp-bdot"></span>AI 生成中</div>
                </div>
              </div>
              <div class="dlp-center">
                <div class="dlp-ow">
                  <div class="dlp-ring dlp-ring--1"></div>
                  <div class="dlp-ring dlp-ring--2"></div>
                  <div class="dlp-orb dlp-orb--1"><div class="dlp-od"></div></div>
                  <div class="dlp-orb dlp-orb--2"><div class="dlp-od"></div></div>
                  <div class="dlp-orb dlp-orb--3"><div class="dlp-od"></div></div>
                  <div class="dlp-core">
                    <div class="dlp-core-ring dlp-core-ring--outer"></div>
                    <div class="dlp-core-ring dlp-core-ring--inner"></div>
                    <div class="dlp-core-dot"></div>
                  </div>
                </div>
                <div class="dlp-center-right">
                  <div ref="dlpTwEl" class="dlp-tw-txt"></div>
                  <div class="dlp-tw-sub">AI 正在整理今日重要信息</div>
                  <div class="dlp-prog"><div class="dlp-pf"></div></div>
                  <div class="dlp-steps">
                    <div class="dlp-stp" :class="dlpStep >= 1 ? (dlpStep > 1 ? 'done' : 'active') : ''">
                      <span class="dlp-stpdot"></span>读取
                    </div>
                    <div class="dlp-stp" :class="dlpStep >= 2 ? (dlpStep > 2 ? 'done' : 'active') : ''">
                      <span class="dlp-stpdot"></span>分析
                    </div>
                    <div class="dlp-stp" :class="dlpStep >= 3 ? (dlpStep > 3 ? 'done' : 'active') : ''">
                      <span class="dlp-stpdot"></span>整理
                    </div>
                    <div class="dlp-stp" :class="dlpStep >= 4 ? 'active' : ''">
                      <span class="dlp-stpdot"></span>生成
                    </div>
                  </div>
                </div>
              </div>
              <div class="dlp-sec-grid">
                <div class="dlp-sec" :class="{ 'dlp-sec--v': dlpSecVis >= 1 }" style="--dlp-c: #3b82f6">
                  <div class="dlp-sec-head"><div class="dlp-sec-dot"></div><div class="dlp-sec-tag">政策动态</div><div class="dlp-sec-cnt">处理中</div></div>
                  <div class="dlp-lns"><div class="dlp-ln" style="width:90%"></div><div class="dlp-ln" style="width:74%"></div><div class="dlp-ln" style="width:83%"></div></div>
                </div>
                <div class="dlp-sec" :class="{ 'dlp-sec--v': dlpSecVis >= 2 }" style="--dlp-c: #8b5cf6">
                  <div class="dlp-sec-head"><div class="dlp-sec-dot"></div><div class="dlp-sec-tag">行业要闻</div><div class="dlp-sec-cnt">处理中</div></div>
                  <div class="dlp-lns"><div class="dlp-ln" style="width:88%"></div><div class="dlp-ln" style="width:94%"></div><div class="dlp-ln" style="width:68%"></div></div>
                </div>
                <div class="dlp-sec" :class="{ 'dlp-sec--v': dlpSecVis >= 3 }" style="--dlp-c: #0ea5e9">
                  <div class="dlp-sec-head"><div class="dlp-sec-dot"></div><div class="dlp-sec-tag">技术前沿</div><div class="dlp-sec-cnt">处理中</div></div>
                  <div class="dlp-lns"><div class="dlp-ln" style="width:78%"></div><div class="dlp-ln" style="width:92%"></div></div>
                </div>
                <div class="dlp-sec" :class="{ 'dlp-sec--v': dlpSecVis >= 4 }" style="--dlp-c: #f59e0b">
                  <div class="dlp-sec-head"><div class="dlp-sec-dot"></div><div class="dlp-sec-tag">市场数据</div><div class="dlp-sec-cnt">处理中</div></div>
                  <div class="dlp-lns"><div class="dlp-ln" style="width:62%"></div><div class="dlp-ln" style="width:76%"></div></div>
                </div>
              </div>
              <div class="dlp-foot">
                <div class="dlp-footl"><div class="dlp-sp"></div><div class="dlp-ft">{{ dlpFootText }}</div></div>
                <div class="dlp-stats">
                  <div class="dlp-stat"><div class="dlp-sn">{{ dlpCount }}</div><div class="dlp-sl">条动态</div></div>
                  <div class="dlp-stat"><div class="dlp-sn">4</div><div class="dlp-sl">个模块</div></div>
                </div>
              </div>
            </div>
            <template v-else>
              <div class="brief-content">
                <!-- 上：html 内容 -->
                <div v-if="briefState.topHtml" class="brief-section brief-section--top">
                  <HtmlRender :html="briefState.topHtml" />
                </div>
                <!-- 中：html 内容 -->
                <div v-if="briefState.middleHtml" class="brief-section brief-section--middle">
                  <HtmlRender :html="briefState.middleHtml" />
                </div>
                <!-- 下：推荐技能 -->
                <div v-if="briefState.skills.length > 0" class="brief-section brief-section--skills">
                  <p class="brief-skills-label">推荐技能</p>
                  <div class="brief-skills-list">
                    <button
                      v-for="skill in briefState.skills"
                      :key="skill.prompt"
                      class="brief-skill-chip"
                      type="button"
                      @click="useBriefSkill(skill.prompt)"
                    >
                      {{ skill.display }}
                      <span class="bsc-arrow">→</span>
                    </button>
                  </div>
                </div>
                <!-- 错误状态 -->
                <div v-if="briefState.error" class="brief-error">{{ briefState.error }}</div>
              </div>
            </template>
          </section>
        </Transition>

        <!-- 简报关闭按钮（stage 层级，不受 brief 内部层叠影响） -->
        <button
          v-if="briefState.visible"
          class="brief-close-btn"
          title="收起简报"
          @click="briefState.visible = false"
        >
          ×
        </button>

        <section v-if="!briefState.visible" ref="scrollEl" class="qa-feed" @scroll.passive="handleFeedScroll">
          <!-- ─── 新首屏上半（双主题，样张同款）：浅蓝 pill + 品牌 wordmark ─── -->
          <div v-if="showNewWelcome" class="kimi-welcome">
            <button
              v-if="briefEnabled"
              type="button"
              class="kimi-brief-pill"
              :class="{ 'is-loading': briefState.loading }"
              @click="openBrief()"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" width="14" height="14">
                <path d="M7 4v12m0 0-3-3m3 3 3-3M17 20V8m0 0-3 3m3-3 3 3" />
              </svg>
              {{ briefState.loading ? '简报生成中…' : (briefState.topHtml || briefState.middleHtml) ? '今日简报已生成' : '今日简报' }}
            </button>
            <div class="kimi-wordmark">
              <div class="kimi-wordmark-main">{{ kimiWordMain }}</div>
            </div>
          </div>

          <!-- 切换会话且历史未返回：轻量加载占位（抑制首屏闪现，见 isSwitchingSession） -->
          <div v-if="isSwitchingSession" class="session-switching">
            <span class="session-switching-dots"><i /><i /><i /></span>
            <span>正在加载任务…</span>
          </div>

          <!-- ─── 能力图鉴 CODEX（旧首屏，仅回滚开关打开时渲染；代码留存备参照） ─── -->
          <div
            v-if="legacyCodexEnabled && messages.length === 0"
            class="qa-showcase"
            @mousemove="onCodexStageMove"
            @mouseleave="onCodexStageLeave"
          >
            <!-- 环境层：点阵 + 当前章节号水印 -->
            <div class="codex-ambient" aria-hidden="true">
              <span class="codex-dots" />
              <Transition name="codex-ghost" mode="out-in">
                <!-- 书桌模式：章节号水印换成专家印记——这一页是「你的」 -->
                <span v-if="deskMode && sessionExpert" key="desk" class="codex-ghostno codex-ghostno--glyph">
                  <SvgIcon :icon="sessionExpert.icon || 'mdi:account-tie-outline'" />
                </span>
                <span v-else :key="activeChapterNo" class="codex-ghostno">{{ activeChapterNo }}</span>
              </Transition>
            </div>

            <!-- 卷首 + 章节索引：翻进条目页（看案例）时整块折起，把高度让给案例列表 -->
            <div class="codex-masthead" :class="{ 'is-collapsed': activeCodexFeature }">
              <div class="codex-masthead-in">
                <!--
 卷首：下面那排功能卡的「头」——与卡片同族（圆角 / 图标牌 / 同一组色相），
                   但存在感压到最低：一抹淡彩 + 发丝边框，不抢卡片的戏。
                   书桌模式只换文案（职业名），样式与「全部功能」完全一致 
-->
                <header class="codex-front">
                  <div class="codex-front-left">
                    <div class="codex-mark">
                      <SvgIcon v-if="deskMode && sessionExpert" :icon="sessionExpert.icon || 'mdi:account-tie-outline'" />
                      <template v-else>№</template>
                    </div>
                    <h1 class="codex-title">{{ deskMode && sessionExpert ? `${sessionExpert.name}的工作台` : '我能帮你做什么' }}</h1>
                  </div>
                  <div class="codex-front-right">
                    <!-- 图鉴统计：功能/常用数 · 案例总数 + 收录日期，版权页式小字 -->
                    <div class="codex-meta">
                      <span>{{
                        deskMode
                          ? SHOW_CASES
                            ? `${showcaseActions.length} 项常用 · ${codexPlateTotal} 个案例`
                            : `${showcaseActions.length} 项常用`
                          : SHOW_CASES
                            ? `${showcaseGroups.length} 类场景 · ${showcaseActions.length} 项功能 · ${codexPlateTotal} 个案例`
                            : `${showcaseGroups.length} 类场景 · ${showcaseActions.length} 项功能`
                      }}</span>
                      <span class="codex-meta-hint">{{ codexDateStr }}</span>
                    </div>
                    <!-- 已引导用户：切换「我的功能 / 全部功能」 -->
                    <div v-if="isOnboarded" class="codex-myctl">
                      <button type="button" class="codex-myctl-btn" :class="{active: showAllActions}" @click="toggleShowAllActions">
                        <SvgIcon :icon="showAllActions ? 'mdi:star-circle-outline' : 'mdi:view-grid-outline'" />
                        <span>{{ showAllActions ? '返回我的功能' : '查看全部功能' }}</span>
                      </button>
                    </div>
                  </div>
                </header>

                <!-- 章节索引 + 极光游标（书桌模式没有章节） -->
                <nav v-if="!deskMode && showcaseGroups.length > 1" ref="chapterNavRef" class="codex-chapters">
                  <span class="codex-cursor" :style="{ left: `${chapterCursor.left}px`, top: `${chapterCursor.top}px`, width: `${chapterCursor.width}px` }" aria-hidden="true" />
                  <button
                    v-for="(group, gi) in showcaseGroups"
                    :key="group.cat"
                    :ref="el => setChapterTabRef(el as Element | null, gi)"
                    type="button"
                    class="codex-chapter"
                    :class="{ 'is-active': activeShowcaseGroup?.cat === group.cat }"
                    @click="activeShowcaseCat = group.cat"
                  >
                    <span class="codex-chapter-no">{{ pad2(gi + 1) }}</span>
                    <span class="codex-chapter-name">{{ group.cat }}</span>
                    <span class="codex-chapter-count">{{ group.actions.length }}</span>
                  </button>
                </nav>
              </div>
            </div>

            <!-- 一章一屏：目录（功能卡）⇄ 条目（该功能全部案例，列表内部滚） -->
            <Transition name="codex-swap" mode="out-in">
              <section v-if="!showcaseBlankKind && activeShowcaseGroup" :key="activeShowcaseGroup.cat" class="codex-body">
                <Transition name="codex-page" mode="out-in">
                  <!-- 目录：该类型下全部功能，整卡可点，翻到条目页 -->
                  <div v-if="!activeCodexFeature" key="grid" class="codex-grid">
                    <article
                      v-for="(action, ai) in activeShowcaseGroup.actions"
                      :key="`${activeShowcaseGroup.cat}-${action.id}`"
                      class="codex-card"
                      :style="codexCardStyle(action, ai)"
                      :title="`查看「${action.name}」的案例`"
                      @click="openCodexFeature(action)"
                      @mousemove="onCodexCardMove"
                      @mouseleave="onCodexCardLeave"
                    >
                      <div class="codex-card-head">
                        <span class="codex-card-no" aria-hidden="true">{{ pad2(ai + 1) }}</span>
                        <span class="codex-card-titles">
                          <span class="codex-card-name">{{ action.name }}</span>
                          <span class="codex-card-tags">
                            <span v-if="action.skillKey" class="codex-callno">@{{ action.skillKey }}</span>
                            <!-- 「全部功能」视图里，给已订阅的卡盖个小章 -->
                            <span
                              v-if="isOnboarded && showAllActions && mySubscribed.has(action.id)"
                              class="codex-mine"
                              title="已订阅 · 在「我的功能」中常驻"
                            >已订阅</span>
                          </span>
                        </span>
                        <!-- 功能图标回显：技能 svg/图片直显，旧 iconify 名回落 SvgIcon -->
                        <span class="codex-card-ico" aria-hidden="true">
                          <span v-if="customIconHtml(action.icon)" v-html="customIconHtml(action.icon)" />
                          <SvgIcon v-else :icon="action.icon || 'mdi:lightning-bolt'" />
                        </span>
                      </div>

                      <p v-if="action.description" class="codex-card-desc">{{ action.description }}</p>

                      <!-- 卡脚：试一试 = 填 @技能进输入框；右侧案例数 = 提示整卡可点进条目 -->
                      <div class="codex-card-foot">
                        <button
                          type="button"
                          class="codex-card-try"
                          :title="action.skillKey ? `试用 @${action.skillKey}` : `以「${action.name}」开问`"
                          @click.stop="insertActionSkill(action)"
                        >
                          试一试 <i>↗</i>
                        </button>
                        <template v-if="SHOW_CASES">
                          <span v-if="action.examples.length" class="codex-card-cases">
                            <b>{{ action.examples.length }}</b> 个案例 <i>→</i>
                          </span>
                          <span v-else class="codex-card-cases codex-card-cases--none">案例征集中</span>
                        </template>
                      </div>
                    </article>
                  </div>

                  <!-- 条目：一个功能的全部案例（内部滚动，案例再多也不破单屏） -->
                  <div v-else key="entry" class="codex-entry" :style="codexEntryStyle(activeCodexFeature)">
                    <div class="codex-entry-top">
                      <button type="button" class="codex-back" @click="activeCodexFeatureId = null">
                        <i>←</i> 返回目录
                      </button>
                      <button
                        type="button"
                        class="codex-try"
                        :title="activeCodexFeature.skillKey ? `试用 @${activeCodexFeature.skillKey}` : `以「${activeCodexFeature.name}」开问`"
                        @click="insertActionSkill(activeCodexFeature)"
                      >
                        试一试 <i>↗</i>
                      </button>
                    </div>

                    <div class="codex-entry-head">
                      <span class="codex-entry-no" aria-hidden="true">{{ activeCodexFeatureNo }}</span>
                      <span class="codex-entry-ico" aria-hidden="true">
                        <span v-if="customIconHtml(activeCodexFeature.icon)" v-html="customIconHtml(activeCodexFeature.icon)" />
                        <SvgIcon v-else :icon="activeCodexFeature.icon || 'mdi:lightning-bolt'" />
                      </span>
                      <h2 class="codex-entry-name">{{ activeCodexFeature.name }}</h2>
                      <span v-if="activeCodexFeature.skillKey" class="codex-callno">@{{ activeCodexFeature.skillKey }}</span>
                    </div>
                    <p v-if="activeCodexFeature.description" class="codex-entry-desc">{{ activeCodexFeature.description }}</p>

                    <template v-if="SHOW_CASES">
                    <div class="codex-entry-rule" aria-hidden="true">
                      <span v-if="activeCodexFeature.examples.length">{{ activeCodexFeature.examples.length }} 个案例 · 点一条直接开问</span>
                      <span v-else>案例征集中</span>
                    </div>

                    <div v-if="activeCodexFeature.examples.length" class="codex-cases">
                      <button
                        v-for="(ex, ei) in activeCodexFeature.examples"
                        :key="ex.id"
                        type="button"
                        class="codex-case"
                        :class="{
                          'is-loading': exampleLoadingId === ex.id,
                          'is-dim': exampleLoadingId !== null && exampleLoadingId !== ex.id
                        }"
                        :style="{ '--delay': `${Math.min(ei * 40, 400)}ms` }"
                        :disabled="exampleLoadingId !== null"
                        :title="exampleLoadingId === ex.id ? '加载中…' : `加载案例：${ex.title}`"
                        @click="handleLoadExample(ex)"
                        @mouseenter="scrubExampleId = ex.id"
                        @mousemove="onShowcaseThumbMove($event, ex)"
                        @mouseleave="onShowcaseThumbLeave()"
                      >
                        <!-- 左侧齐边媒体面板：悬停扫动切帧、高光扫过、可放大 -->
                        <span v-if="exampleImages(ex).length" class="codex-case-media">
                          <img
                            :src="showcaseImgUrl(exampleImages(ex)[scrubExampleId === ex.id ? scrubIndex : 0])"
                            :alt="ex.title"
                            loading="lazy"
                          />
                          <span v-if="exampleImages(ex).length > 1" class="codex-case-frame">
                            {{ (scrubExampleId === ex.id ? scrubIndex : 0) + 1 }}/{{ exampleImages(ex).length }}
                          </span>
                          <span
                            class="codex-case-zoom"
                            title="查看大图"
                            @click.stop="openImgLightbox(showcaseImgUrl(exampleImages(ex)[scrubExampleId === ex.id ? scrubIndex : 0]), ex.title)"
                          >
                            <SvgIcon icon="mdi:arrow-expand-all" />
                          </span>
                        </span>
                        <!-- 右侧分区文字脚：mono 标签 + 标题 + 试一试胶囊 -->
                        <span class="codex-case-foot">
                          <span class="codex-case-main">
                            <span class="codex-case-kicker">
                              CASE {{ pad2(ei + 1) }}<template v-if="activeCodexFeature.skillKey"> · @{{ activeCodexFeature.skillKey }}</template>
                            </span>
                            <span class="codex-case-title">{{ ex.title }}</span>
                          </span>
                          <span class="codex-case-try">
                            <template v-if="exampleLoadingId === ex.id"><span class="codex-case-try-spin" aria-hidden="true" /> 加载中…</template>
                            <template v-else>试一试 <i>→</i></template>
                          </span>
                        </span>
                        <!-- 加载进度条：fork 较慢时沿卡片底边往返滑动 -->
                        <span v-if="exampleLoadingId === ex.id" class="codex-case-loading" aria-hidden="true" />
                      </button>
                    </div>
                    <p v-else class="codex-entry-empty">还没有收录案例 —— 点右上角「试一试」，你就是第一个用它的人。</p>
                    </template>
                  </div>
                </Transition>
              </section>

              <!--
 空白章节：无功能可看时的「待收录」跨页——幽灵卡预演目录形状，
                 把空白变成有意为之的留白，而不是看起来像 bug 的空屏 
-->
              <section v-else key="blank" class="codex-body codex-body--blank">
                <div class="codex-blank" :class="`codex-blank--${showcaseBlankKind}`">
                  <template v-if="showcaseBlankKind === 'loading'">
                    <p class="codex-blank-kicker">READING …</p>
                    <p class="codex-blank-sub">正在翻开图鉴</p>
                  </template>

                  <template v-else-if="showcaseBlankKind === 'mine'">
                    <p class="codex-blank-kicker">MY DESK · EMPTY</p>
                    <h2 class="codex-blank-title">你的书桌还空着</h2>
                    <p class="codex-blank-sub">订阅的功能已不在架上 —— 重新挑几个顺手的，或先翻翻全部功能。</p>
                    <div class="codex-blank-actions">
                      <button type="button" class="codex-blank-btn codex-blank-btn--solid" @click="composerRef?.openSkillPanel('skill')">
                        <SvgIcon icon="mdi:view-grid-outline" /><span>去技能商店挑选</span>
                      </button>
                      <button type="button" class="codex-blank-btn" @click="toggleShowAllActions">
                        <SvgIcon icon="mdi:view-grid-outline" /><span>查看全部功能</span>
                      </button>
                    </div>
                  </template>

                  <template v-else>
                    <p class="codex-blank-kicker">№ 00 · UNWRITTEN</p>
                    <h2 class="codex-blank-title">这一章还空着</h2>
                    <p class="codex-blank-sub">功能正在筹备中 —— 第一张卡片录入后，就会出现在这里。</p>
                    <p class="codex-blank-note">管理员可在「快捷功能管理」中配置功能与案例</p>
                  </template>

                  <!-- 幽灵卡：虚线框 + 淡彩条纹，预演将来功能卡的形状 -->
                  <div class="codex-blank-ghosts" aria-hidden="true">
                    <span v-for="gi in 3" :key="gi" class="codex-blank-ghost" :style="{'--delay': `${gi * 90}ms`}">
                      <i class="cbg-head" /><i class="cbg-line" /><i class="cbg-line cbg-line--short" /><i class="cbg-foot" />
                    </span>
                  </div>
                </div>
              </section>
            </Transition>
          </div>

          <!-- Conversation -->
          <div v-if="visibleMessages.length > 0" class="conversation">
            <article
              v-for="msg in visibleMessages"
              :key="msg.id"
              :class="[`exchange-${msg.role}`, {continuation: continuationIds.has(msg.id), 'pre-continuation': preContinuationIds.has(msg.id)}]"
              class="exchange"
            >
              <!-- USER -->
              <template v-if="msg.role === 'user'">
                <div :data-msg-id="msg.id" class="user-question">
                  <span class="q-mark">Q.</span>
                  <div class="q-body">
                    <NTooltip
                      placement="top-start"
                      :show-arrow="false"
                      :delay="200"
                      trigger="hover"
                      :disabled="!clampedMsgIds.has(msg.id)"
                      class="q-tooltip-popover"
                      :style="{
                        background: '#ffffff',
                        color: '#334155',
                        border: '1px solid rgba(30, 64, 175, 0.12)',
                        borderRadius: '6px',
                        boxShadow: '0 12px 32px rgba(15, 23, 42, 0.12)',
                        padding: '12px 14px',
                        maxWidth: '560px',
                        maxHeight: '40vh',
                        overflowY: 'auto',
                        fontSize: '13px',
                        lineHeight: '1.7',
                        letterSpacing: '0',
                        whiteSpace: 'pre-wrap',
                        wordBreak: 'break-word'
                      }"
                    >
                      <template #trigger>
                        <p :ref="el => registerQTextRef(el as HTMLElement | null, msg.id)" class="q-text">{{ msg.content }}</p>
                      </template>
                      {{ msg.content }}
                    </NTooltip>
                    <!-- 截断按钮：绝对定位悬浮在气泡左侧（不占气泡内部空间），hover user-question 时显示 -->
                    <template v-if="msg.serverId">
                      <button
                        v-if="truncateConfirmMsgId !== msg.serverId"
                        class="answer-truncate q-truncate"
                        :disabled="truncatingMsgId === msg.serverId"
                        title="从此处截断（删除此问题及之后的全部任务）"
                        @click.stop="truncateConfirmMsgId = msg.serverId!"
                      >
                        <span class="ae-icon">✂</span>
                        <span>截断</span>
                      </button>
                      <button
                        v-else
                        class="answer-truncate answer-truncate--confirm q-truncate"
                        :disabled="truncatingMsgId === msg.serverId"
                        title="确认删除此问题及之后的全部任务"
                        @click.stop="truncateFromMessage(msg.serverId!)"
                      >
                        <span class="ae-icon">⚠</span>
                        <span>{{ truncatingMsgId === msg.serverId ? '删除中…' : '确认截断' }}</span>
                      </button>
                    </template>
                    <div v-if="msg.attachments && msg.attachments.length" class="q-attachments">
                      <NImageGroup v-if="msg.attachments.some(a => a.isImage)">
                        <div v-for="att in msg.attachments.filter(a => a.isImage)" :key="att.path" class="q-att-item">
                          <div class="q-att-img-wrap">
                            <NImage
                              :src="buildAttachmentUrl(att.path)"
                              :alt="att.name"
                              object-fit="cover"
                              :img-props="{ class: 'q-att-img', loading: 'lazy' }"
                            />
                          </div>
                        </div>
                      </NImageGroup>
                      <div v-for="att in msg.attachments.filter(a => !a.isImage)" :key="att.path" class="q-att-item">
                        <button
                          v-if="isMarkdownFile(att.name) || isOfficePreviewable(att.name) || isCsvFile(att.name) || isVideoFile(att.name)"
                          type="button"
                          class="q-att-file q-att-md"
                          :title="`预览 ${att.name}`"
                          @click="openAttachmentPreview(att)"
                        >
                          <span :class="'af-ext-' + fileExtGroup(att.name)" class="af-ext">{{ fileExt(att.name) }}</span>
                          <span class="q-att-name">{{ att.name }}</span>
                          <span class="q-att-size">{{ formatFileSize(att.size) }}</span>
                          <span class="q-att-md-hint">预览</span>
                        </button>
                        <a v-else :href="buildAttachmentUrl(att.path)" :download="att.name" class="q-att-file">
                          <span :class="'af-ext-' + fileExtGroup(att.name)" class="af-ext">{{ fileExt(att.name) }}</span>
                          <span class="q-att-name">{{ att.name }}</span>
                          <span class="q-att-size">{{ formatFileSize(att.size) }}</span>
                        </a>
                      </div>
                    </div>
                  </div>
                </div>
              </template>

              <!-- ASSISTANT -->
              <div v-else-if="msg.role === 'assistant'" class="assistant-response">
                <!-- 过程时间线（dsh 两段式）：思考/叙述/工具/子代理/清单；done 后折叠为摘要行。
                     openTextId 指向的打开中 text 条目正由结果区渲染，时间线不重复展示（D2 无跳动）。 -->
                <!-- 续接段（问卷作答后的回复）照常渲染自己的时间线：问卷前后虽属同一次回答，
                     但作答后 agent 的工具调用/子代理委派等过程不能丢——done 后折叠为摘要行，
                     视觉上仍是一条紧凑的灰行，不会切断正文连续性。 -->
                <ProcessTimeline
                  v-if="msg.process && msg.process.some(i => i.id !== msg.openTextId && i.kind !== 'questionnaire')"
                  v-model:collapsed="msg.processCollapsed"
                  :items="msg.process"
                  :live="msg.loading"
                  :duration-ms="msg.processDurationMs"
                  :open-text-id="msg.openTextId"
                />

                <!-- Loading：打字机已在输出且无工具调用时，不再呈现"思考中" -->
                <div v-if="msg.loading && (msg.currentTool || !msg.content)" class="loading-line">
                  <span class="orbit-dual">
                    <span class="star" /><span class="star" />
                    <span class="inner-ring"><span class="inner-star" /></span>
                  </span>
                  <span class="loading-text">{{ msg.currentTool ? `执行 · ${msg.currentTool}` : '思考中...' }}</span>
                </div>

                <!-- Error -->
                <div v-if="msg.error" class="error-line">
                  <span class="error-tag">异常</span>
                  <span>{{ msg.error }}</span>
                </div>

                <!-- 用户主动停止：非异常，中性提示 -->
                <div v-if="msg.stopped" class="stopped-line">
                  <span class="stopped-tag">已停止</span>
                  <span>用户主动停止了本次回答</span>
                </div>

                <!-- Answer -->
                <div v-if="msg.contentHtml || msg.contentSegments?.length" class="answer" :class="{ streaming: msg.loading }">
                  <!--
 头像方块：字面文本置空，「同」由 ::after 渲染——避免靠 font-size:0 藏文字，
                     该写法在导出图（html-to-image 克隆渲染）里会藏不住而漏出「A.同」两层 
-->
                  <div class="answer-mark" aria-hidden="true"></div>
                  <!-- inline 模式：分段内容包在 flex:1 的 wrapper 里 -->
                  <template v-if="msg.contentSegments?.length">
                    <div class="answer-body-segments">
                      <template v-for="(seg, si) in msg.contentSegments" :key="si">
                        <!-- eslint-disable-next-line vue/no-v-html -->
                        <div v-if="seg.type === 'html'" class="answer-body" :class="{ 'is-last-segment': si === msg.contentSegments.length - 1 }" v-html="seg.html" />
                        <ArtifactList
                          v-else-if="seg.type === 'artifact' && getArtifactById(msg, seg.id).length"
                          :artifacts="getArtifactById(msg, seg.id)"
                          :inline="true"
                        />
                        <SurveyCard
                          v-else-if="seg.type === 'questionnaire'"
                          :qid="seg.id"
                          :questions="getQuestionnaireById(msg, seg.id)?.questions || []"
                          :answers="qnAnswers.get(msg.id)?.get(seg.id)"
                          :disabled="running"
                          @submit="onSurveySubmit"
                        />
                      </template>
                    </div>
                  </template>
                  <!-- 普通模式：单个 answer-body，与原来完全一致 -->
                  <div v-else class="answer-body" v-html="msg.contentHtml" />
                  <div class="answer-actions">
                    <button class="answer-export" title="导出为 Markdown" @click="exportMessageAsMd(msg)">
                      <span class="ae-icon">↧</span>
                      <span>导出 md</span>
                    </button>
                  </div>
                </div>
              </div>
            </article>
          </div>
        </section>

        <!-- ─── Question minimap (right-edge track) ─────────────────────── -->
        <!--
 hover 判定挂在可见的 rail 上而非外层 aside：aside 是 top:0/bottom:0 全高
           透明区，挂它上面时鼠标上下移出弹窗仍算悬停、永不收折 
-->
        <aside
          v-if="questionList.length"
          :class="{ 'is-hover': trackHover }"
          class="qa-track"
        >
          <div ref="railEl" class="qa-track-rail" @mouseenter="handleRailEnter" @mouseleave="trackHover = false">
            <span class="qa-track-line" />
            <button
              v-for="q in questionList"
              :key="q.id"
              :class="{ active: q.id === activeQuestionId }"
              :title="q.text"
              class="qa-track-tick"
              type="button"
              @click="jumpToQuestion(q.id)"
            >
              <span class="qa-track-dot" />
              <span class="qa-track-meta">
                <span class="qa-track-seq">Q{{ String(q.seq).padStart(2, '0') }}</span>
                <span class="qa-track-text">{{ q.text }}</span>
              </span>
            </button>
          </div>
        </aside>

        <!-- ─── Jump-to-bottom round button ─────────────────────────────── -->
        <Transition name="qa-jumpdown">
          <button
            v-if="!shouldFollow && messages.length > 0"
            class="qa-jumpdown"
            type="button"
            title="回到最新"
            aria-label="回到最新"
            @click="jumpToBottomAndFollow"
          >
            <span class="qa-jumpdown-arrow" aria-hidden="true">↓</span>
          </button>
        </Transition>
      </div>

      <!-- Composer -->
      <QAComposer
        ref="composerRef"
        v-model="inputText"
        v-model:skill-active-index="skillActiveIndex"
        :running="running"
        :attached-files="attachedFiles"
        :chat-mode="chatModePref"
        :is-mobile="isMobile"
        :popup-down="showNewWelcome"
        :filtered-skills="filteredSkills"
        :skill-popup-open="skillPopupOpen"
        :current-session-key="currentSessionKey || null"
        :active-connectors="activeConnectors"
        :session-expert="currentExpertInfo"
        :expert-candidates="expertCandidates"
        :board-enabled="!embedded"
        :attached-board="attachedBoardInfo"
        :board-picker-open="boardPickerOpen"
        :board-picker-mode="boardPickerMode"
        :board-list="boardPickerList"
        :board-list-loading="boardPickerLoading"
        :sustained-armed="sustainedWorkOn"
        @send="handleSend"
        @stop="handleStop"
        @file-select="uploadFiles"
        @remove-attachment="removeAttachment"
        @preview-attachment="handleAttachmentPreview"
        @insert-skill="insertSkill"
        @input="() => handleInput()"
        @keydown="handleKeydown"
        @paste="handlePaste"
        @close-skill-popup="closeSkillPopup"
        @skill-panel-change="onSkillPanelChange"
        @expert-chat="handleSummonExpert"
        @remove-expert="handleRemoveSessionExpert"
        @summon-expert="handleSummonExpert"
        @open-board-picker="openBoardPicker"
        @close-board-picker="closeBoardPicker"
        @attach-board="attachBoard"
        @create-board="createBoardInPanel"
        @detach-board="detachBoard"
        @expand-board="expandBoardToPage"
        @toggle-sustained="sustainedWorkOn = !sustainedWorkOn"
        @select-chat-mode="m => applyChatModePref({mode: m})"
        @select-thinking-level="lv => applyChatModePref({thinkingLevel: lv})"
      />

      <!-- ─── 新首屏下半：探索区独立组件（标准池灰带 + pill/技能区 + 探索案例） ─── -->
      <WelcomeShowcase
        v-if="showNewWelcome"
        :is-generic="isGeneric"
        :groups="exploreGroups"
        :case-groups="SHOW_CASES ? exploreCaseGroups : []"
        :loading-example-id="exampleLoadingId"
        @use-skill="insertActionSkill"
        @load-example="handleLoadExample"
      />
    </main>

    <!--
 伴侣面板：流程编排 / 应用制作挂进对话（grid 第三轨道；仅独立模式，嵌入/窄屏不渲染）。
         板是主角：默认占内容区 2/3（1fr : 2fr），分隔条可拖宽并记忆。
         内容与流程编排专页共挂同一画板成品组件 board-shell，对话经 boardBridge 注入 
-->
    <div v-if="boardPanelVisible" class="board-panel-col">
      <div class="bp-resizer" title="拖动调整比例，双击回到 2/3 默认" @pointerdown="onPanelResizeStart" @dblclick="resetPanelRatio" />
      <BoardPanel
        ref="boardPanelRef"
        :workflow-key="attachedWorkflowKey || ''"
        :qa-bridge="boardBridge"
        @close="closeBoardPanel"
        @switched="attachBoard"
        @created="createBoardInPanel"
        @not-found="onPanelNotFound"
        @preview-attachment="att => (attPreview = att)"
      />
    </div>

    <!-- Distill result modal -->
    <div v-if="distillResult || distillError" class="distill-mask" @click.self="closeDistillResult">
      <div class="distill-modal">
        <header class="dm-head">
          <span class="dm-tag">{{ distillResult ? 'SKILL·SAVED' : 'DISTILL·FAILED' }}</span>
          <span class="dm-line" />
          <button class="dm-close" @click="closeDistillResult">×</button>
        </header>

        <div v-if="distillResult" class="dm-body">
          <div class="dm-row">
            <span class="dm-label">KEY</span>
            <code class="dm-key">@{{ distillResult.skillKey }}</code>
          </div>
          <div class="dm-row">
            <span class="dm-label">NAME</span>
            <span class="dm-value">{{ distillResult.name }}</span>
          </div>
          <div v-if="distillResult.description" class="dm-row">
            <span class="dm-label">DESC</span>
            <span class="dm-value">{{ distillResult.description }}</span>
          </div>
          <div class="dm-row dm-row-block">
            <span class="dm-label">SKILL.MD</span>
            <pre class="dm-prompt">{{ distillResult.skillMd }}</pre>
          </div>
          <p class="dm-foot">
            下次在输入框敲 <code>@{{ distillResult.skillKey }}</code> 即可调用。
          </p>
        </div>

        <div v-else class="dm-body">
          <p class="dm-error">{{ distillError }}</p>
        </div>
      </div>
    </div>

    <!-- 附件统一预览：markdown / Office / 视频 / 图片放大（与工作流画板共享同一组件） -->
    <AttachmentPreviewModal :att="attPreview" @close="attPreview = null" />

    <!-- 标准详情抽屉 -->
    <StdDetailDrawer v-model:show="showStdDetail" :standard-id="selectedStdId" />

    <!-- 定时任务抽屉 -->
    <TaskDrawer v-model:show="taskDrawerOpen" @load-session="loadSession" @fill="onTaskDrawerFill" />

    <!-- 图片放大预览（案例大图等） -->
    <div v-if="imgLightbox.visible" class="img-lightbox-mask" @click="closeImgLightbox">
      <img :src="imgLightbox.src" :alt="imgLightbox.name" class="img-lightbox-img" @click.stop />
      <span class="img-lightbox-name">{{ imgLightbox.name }}</span>
      <button type="button" class="img-lightbox-close" @click="closeImgLightbox">✕</button>
    </div>
  </div>
</template>

<style scoped>

.qa-shell {
  /* ─── 背景 & 表面 ─── */
  --paper: #f5f7fb;
  --paper-deep: #eaf0f9;
  --paper-soft: #ffffff;
  --surface: rgba(255, 255, 255, 0.42);
  --surface-strong: rgba(255, 255, 255, 0.62);
  --surface-deep: rgba(255, 255, 255, 0.78);
  --highlight: rgba(255, 255, 255, 0.95);

  /* ─── 墨色 ─── */
  --ink: #0f172a;
  --ink-2: #334155;
  --ink-3: #64748b;
  --ink-4: #94a3b8;

  /* ─── 边框 ─── */
  --rule: rgba(30, 64, 175, 0.1);
  --rule-soft: rgba(30, 64, 175, 0.06);
  --border: rgba(30, 64, 175, 0.1);
  --border-strong: rgba(30, 64, 175, 0.18);
  --border-glow: rgba(30, 64, 175, 0.25);

  /* ─── 强调色 ─── */
  --accent: #1e40af;
  --accent-soft: rgba(30, 64, 175, 0.08);
  --accent-deep: #1e3a8a;
  --gold: #0891b2;

  /* ─── 色板 ─── */
  --c-blue: #1e40af;
  --c-blue-2: #2563eb;
  --c-sky: #0ea5e9;
  --c-cyan: #0891b2;
  --c-violet: #4f46e5;
  --c-mint: #10b981;

  /* ─── 极光渐变 ─── */
  --aurora: linear-gradient(110deg, var(--c-blue) 0%, var(--c-blue-2) 35%, var(--c-sky) 70%, var(--c-cyan) 100%);

  /* ─── 语义令牌（双主题共用，kimi 块覆盖；2026-08 整体换语言） ─── */
  --grad-brand: linear-gradient(110deg, #1e40af 0%, #2563eb 35%, #0ea5e9 70%, #0891b2 100%);
  --on-primary: #ffffff;
  --newchat-bg: linear-gradient(110deg, #1e40af 0%, #2563eb 35%, #0ea5e9 70%, #0891b2 100%);
  --newchat-ink: #ffffff;
  --card-bg: rgba(255, 255, 255, 0.62);
  --card-border: transparent;
  --card-shadow: 0 1px 2px rgba(15, 23, 42, 0.06);
  --input-shadow: 0 24px 64px -28px rgba(30, 64, 175, 0.35);
  --r-newchat: 16px;
  --side-bg: rgba(255, 255, 255, 0.42);
  --side-border: transparent;
  --side-blur: blur(28px) saturate(160%);
  --ambient:
    radial-gradient(700px 420px at 12% -6%, rgba(37, 99, 235, 0.1), transparent 62%),
    radial-gradient(760px 460px at 96% 108%, rgba(8, 145, 178, 0.09), transparent 60%);
  --side-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.95), inset 0 0 0 1px rgba(255, 255, 255, 0.4), inset -1px 0 0 rgba(255, 255, 255, 0.6);
  --head-rule: rgba(30, 64, 175, 0.08);
  --fill-hover: rgba(30, 64, 175, 0.06);
  --fill-2: rgba(30, 64, 175, 0.08);
  --line-hair: rgba(30, 64, 175, 0.1);
  --blue: #1e40af;
  --blue-bg: rgba(30, 64, 175, 0.08);
  --send-off: #94a3b8;
  --kbd-bg: rgba(255, 255, 255, 0.22);
  --kbd-ink: rgba(255, 255, 255, 0.9);
  --active-bg: rgba(30, 64, 175, 0.08);
  --active-shadow: none;
  --active-ink: #1e40af;
  --bubble-user: rgba(30, 64, 175, 0.09);
  --mark-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.5), 0 4px 14px -4px rgba(30, 64, 175, 0.55);
  --popup-bg: rgba(255, 255, 255, 0.96);
  --popup-blur: blur(40px) saturate(200%);
  --popup-border: rgba(30, 64, 175, 0.12);
  --popup-shadow: 0 16px 48px -12px rgba(30, 64, 175, 0.25), 0 4px 14px -6px rgba(15, 23, 42, 0.1);

  /* ─── 阴影系统（2026-08 舒适度微调：蓝调 alpha 收敛，向中性靠拢） ─── */
  --shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.04), 0 4px 16px -8px rgba(30, 64, 175, 0.09);
  --shadow-md: 0 1px 2px rgba(15, 23, 42, 0.05), 0 12px 32px -12px rgba(30, 64, 175, 0.13);
  --shadow-lg: 0 1px 2px rgba(15, 23, 42, 0.05), 0 24px 64px -20px rgba(30, 64, 175, 0.2);
  --shadow-glow: 0 8px 32px -10px rgba(30, 64, 175, 0.32);

  /* ─── 字体（2026-08 样张对齐：双主题统一系统字体栈） ─── */
  --font-display: -apple-system, 'PingFang SC', 'HarmonyOS Sans SC', 'Hiragino Sans GB', 'Microsoft YaHei', system-ui, sans-serif;
  --font-body: -apple-system, 'PingFang SC', 'HarmonyOS Sans SC', 'Hiragino Sans GB', 'Microsoft YaHei', system-ui, sans-serif;
  --font-mono: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace;

  position: relative;
  display: grid;
  /* 三轨道：侧栏 / 对话主列 / 伴侣面板列（默认 0，board-panel-open 时伸出）。
     不给 grid-template-columns 加过渡动画：开合/拖宽每帧变轨道，动画会拖影变形且存在卡死先例——面板直接出现 */
  grid-template-columns: 272px 1fr 0px;
  height: 100%;
  width: 100%;
  background: var(--paper);
  color: var(--ink);
  font-family: var(--font-body);
  overflow: hidden;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

/* 布局全局给的 transition-300 会顺着 all 系属性渗进来，显式压掉，防轨道变化被动画化 */
.qa-shell,
.qa-shell.sidebar-collapsed,
.qa-shell.board-panel-open {
  transition: none;
}

.qa-shell.sidebar-collapsed {
  grid-template-columns: 0px 1fr 0px;
}

/* 伴侣面板伸出（流程编排/应用制作挂进对话）：板是主角，默认占内容区 2/3（1fr : 2fr）；
   拖动分隔条后改由 .qa-shell 行内 gridTemplateColumns 接管（px）。侧栏收展两态都要带第三轨道 */
.qa-shell.board-panel-open {
  grid-template-columns: 272px 1fr 2fr;
}

.qa-shell.board-panel-open.sidebar-collapsed {
  grid-template-columns: 0px 1fr 2fr;
}

/* 面板列容器：resizer + BoardPanel */
.board-panel-col {
  position: relative;
  display: flex;
  min-width: 0;
  height: 100%;
}

.board-panel-col > * {
  flex: 1;
  min-width: 0;
}

/* 分隔条：贴面板左缘，hover/拖动时亮一条品牌色细线 */
.bp-resizer {
  position: absolute;
  left: -4px;
  top: 0;
  bottom: 0;
  width: 8px;
  z-index: 30;
  cursor: col-resize;
  flex: none;
}

.bp-resizer::after {
  content: '';
  position: absolute;
  left: 3px;
  top: 0;
  bottom: 0;
  width: 2px;
  background: transparent;
  transition: background 0.15s ease;
}

.bp-resizer:hover::after {
  background: rgba(37, 99, 235, 0.35);
}

/* 拖动中：全局换拖拽光标、停文本选择；面板内 iframe 吞指针事件会打断拖动，整体禁掉 */
.qa-shell.board-panel-resizing {
  cursor: col-resize;
  user-select: none;
}

.qa-shell.board-panel-resizing iframe {
  pointer-events: none;
}

/* 伴侣面板在场时对话列变窄：顶栏按钮收进图标态（同 960px 媒体查询的语言，但按面板态触发而非视口宽），
   标题区靠 min-width:0 省略号截断，按钮不再被挤变形 */
.qa-shell.board-panel-open :deep(.qa-topbar) {
  gap: 8px;
  padding: 0 12px;
}

.qa-shell.board-panel-open :deep(.topbar-kb .td-text-nian),
.qa-shell.board-panel-open :deep(.topbar-distill .td-text),
.qa-shell.board-panel-open :deep(.ms-text) {
  display: none;
}

.qa-shell.board-panel-open :deep(.topbar-distill) {
  padding: 6px 9px;
  margin-left: 0;
}

.qa-shell.board-panel-open :deep(.topbar-kb) {
  padding: 7px;
  margin-left: 0;
}

/* 氛围层（2026-08 换语言：动画光球/点阵退役，改样张同款静态双斑 aurora，
   见 .qa-shell::before 的 --ambient；本节点保留仅为兼容旧 DOM） */
.qa-grain {
  display: none;
}

.qa-grain::before,
.qa-grain::after {
  content: '';
  position: absolute;
  border-radius: 50%;
  filter: blur(120px);
  will-change: transform;
}

.qa-grain::before {
  width: 680px;
  height: 680px;
  top: -200px;
  right: -160px;
  background: radial-gradient(circle, #1e40af 0%, transparent 65%);
  opacity: 0.24;
  animation: qa-aurora-1 26s ease-in-out infinite;
}

.qa-grain::after {
  width: 720px;
  height: 720px;
  bottom: -260px;
  left: -200px;
  background: radial-gradient(circle, #0891b2 0%, transparent 65%);
  opacity: 0.22;
  animation: qa-aurora-2 32s ease-in-out infinite;
}

@keyframes qa-aurora-1 {
  0%, 100% { transform: translate(0, 0) scale(1); }
  50%      { transform: translate(60px, 40px) scale(1.08); }
}

@keyframes qa-aurora-2 {
  0%, 100% { transform: translate(0, 0) scale(1); }
  50%      { transform: translate(-40px, -60px) scale(1.05); }
}

@media (prefers-reduced-motion: reduce) {
  .qa-grain::before,
  .qa-grain::after { animation: none !important; }
}

/* 第三颗 orb：退役（静态双斑取代，见 ::before） */
.qa-shell::after {
  display: none;
}

@keyframes qa-aurora-3 {
  0%, 100% { transform: translate(0, 0) scale(1); }
  33%      { transform: translate(-50px, 30px) scale(0.96); }
  66%      { transform: translate(40px, -25px) scale(1.04); }
}

/* 样张同款静态 aurora：玻璃 = 双斑柔光；kimi = none */
.qa-shell::before {
  content: '';
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 0;
  background: var(--ambient, none);
}

/* ─── SIDEBAR ──────────────────────────────────────────────────────── */
.qa-sidebar {
  position: relative;
  z-index: 2;
  background: rgba(241, 244, 250, 0.55);
  backdrop-filter: blur(28px) saturate(180%);
  -webkit-backdrop-filter: blur(28px) saturate(180%);
  border-right: 1px solid var(--rule);
  overflow: hidden;
  transition: opacity 0.3s;
  box-shadow: inset -1px 0 0 rgba(255, 255, 255, 0.6);
}

.qa-shell.sidebar-collapsed .qa-sidebar {
  opacity: 0;
}

.sidebar-inner {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-width: 286px;
  padding: 24px 20px 16px;
  gap: 18px;
}

.sidebar-header {
  padding-bottom: 16px;
  border-bottom: 1px dashed var(--rule);
}

.brand {
  display: flex;
  align-items: center;
  gap: 13px;
}

.brand-mark {
  font-family: var(--font-display);
  font-size: 42px;
  line-height: 1;
  font-weight: 300;
  font-style: normal;
  color: var(--accent);
  margin-bottom: -4px;
}

.brand-title {
  font-family: var(--font-display);
  font-weight: 500;
  font-size: 17px;
  color: var(--ink);
  letter-spacing: 0.02em;
  line-height: 1.2;
}

.new-chat {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 11px 14px;
  background: var(--ink);
  color: var(--paper);
  border: none;
  border-radius: 11px;
  font-family: var(--font-body);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s;
  letter-spacing: 0.02em;
  position: relative;
  overflow: hidden;
}

.new-chat::before {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--accent);
  transform: translateX(-100%);
  transition: transform 0.32s cubic-bezier(0.22, 1, 0.36, 1);
}

.new-chat:hover::before {
  transform: translateX(0);
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

.sessions-nav {
  flex: 1;
  overflow-y: auto;
  margin: -4px -8px;
  padding: 4px 8px;
}

.sessions-nav::-webkit-scrollbar {
  width: 5px;
}

.sessions-nav::-webkit-scrollbar-track {
  background: transparent;
}

.sessions-nav::-webkit-scrollbar-thumb {
  background: var(--rule);
  border-radius: 3px;
}

.session-group {
  margin-bottom: 18px;
}

.session-group-label {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 4px 8px;
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.06em;
  color: var(--ink-3);
  font-weight: 600;
}

.group-line {
  flex: 1;
  height: 1px;
  background: var(--rule);
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
  padding: 8px 10px;
  font-size: 13px;
  color: var(--ink-2);
  cursor: pointer;
  border-left: 2px solid transparent;
  margin-bottom: 1px;
  transition: all 0.15s;
  position: relative;
}

.session-item::before {
  content: '';
  position: absolute;
  inset: 0;
  background: rgba(30, 64, 175, 0.04);
  opacity: 0;
  transition: opacity 0.15s;
}

.session-item:hover::before {
  opacity: 1;
}

.session-item:hover {
  border-left-color: var(--rule);
}

.session-item.active {
  background: var(--paper-soft);
  border-left-color: var(--accent);
  color: var(--ink);
  font-weight: 500;
}

.session-item.active::before {
  opacity: 0;
}

.session-dot {
  width: 4px;
  height: 4px;
  background: var(--ink-4);
  border-radius: 50%;
  flex-shrink: 0;
  transition: background 0.15s;
}

.session-item.active .session-dot {
  background: var(--accent);
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
  border-radius: 3px;
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
  padding-top: 12px;
  border-top: 1px dashed var(--rule);
  font-family: var(--font-mono);
  font-size: 9px;
  color: var(--ink-3);
  letter-spacing: 0.12em;
  text-align: center;
}

.qa-main {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

/* Topbar 基础样式（高度/间距/边框）统一收在 QATopBar.vue 组件内，
   这里只保留场景覆盖（welcome sticky / ink 主题 / 媒体查询），不再重复定义基线 */

.topbar-back {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  background: transparent;
  border: 1px solid var(--rule);
  color: var(--ink-2);
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.005em;
  font-weight: 700;
  cursor: pointer;
  border-radius: 9px;
  transition: border-color 0.15s, color 0.15s;
}

.topbar-back:hover {
  border-color: var(--ink);
  color: var(--accent);
}

.tb-icon {
  font-family: var(--font-display);
  font-size: 14px;
  line-height: 1;
}

.topbar-distill {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 6px 12px;
  margin-left: 8px;
  background: transparent;
  border: 1px solid var(--accent);
  color: var(--accent);
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.005em;
  font-weight: 700;
  cursor: pointer;
  border-radius: 9px;
  transition: all 0.15s;
}

.topbar-distill:hover:not(:disabled) {
  background: var(--accent);
  color: var(--paper);
}

.topbar-distill:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.td-icon {
  font-family: var(--font-display);
  font-size: 14px;
  line-height: 1;
  font-style: normal;
  font-weight: 500;
}

/* ─── 沉淀按钮（手机端的合并下拉） ────────────────────────────── */
.topbar-sediment-mobile {
  display: none; /* 桌面隐藏，960 以下打开 */
  position: relative;
  margin-left: 8px;
}

.topbar-sediment-trigger {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.topbar-sediment-trigger.is-open {
  background: var(--accent);
  color: var(--paper);
}

.td-caret {
  font-size: 9px;
  line-height: 1;
  letter-spacing: 0;
  display: inline-block;
  transform: translateY(1px);
}

.sediment-menu {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  z-index: 30;
  min-width: 220px;
  background: #ffffff;
  border: 1px solid var(--rule);
  border-radius: 12px;
  box-shadow: 0 12px 28px rgba(15, 23, 42, 0.16);
  padding: 6px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  animation: rise 0.18s ease-out;
}

.sm-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 12px;
  background: transparent;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  text-align: left;
  font-family: var(--font-body);
  color: var(--ink);
  transition: background 0.15s;
}

.sm-item:hover:not(:disabled) {
  background: var(--accent-soft);
}

.sm-item:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.sm-mark {
  font-family: var(--font-display);
  font-size: 18px;
  line-height: 1.1;
  color: var(--accent);
  font-weight: 500;
  flex-shrink: 0;
}

.sm-body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.sm-title {
  font-size: 14px;
  font-weight: 600;
  letter-spacing: 0.02em;
  color: var(--ink);
}

.sm-sub {
  font-size: 12px;
  color: var(--ink-3);
  line-height: 1.45;
}

/* ─── DISTILL MODAL ──────────────────────────────────────────────── */
.distill-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.32);
  z-index: 200;
  display: flex;
  align-items: center;
  justify-content: center;
  animation: rise 0.22s ease-out;
}

.distill-modal {
  width: min(560px, calc(100vw - 48px));
  background: rgba(255, 255, 255, 0.98);
  backdrop-filter: blur(40px) saturate(200%);
  border: 1px solid rgba(30, 64, 175, 0.12);
  border-radius: 18px;
  box-shadow: 0 24px 64px -20px rgba(30, 64, 175, 0.28);
}

.dm-head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 18px;
  border-bottom: 1px solid var(--rule);
  background: var(--paper-deep);
}

.dm-tag {
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.06em;
  color: var(--accent);
}

.dm-line {
  flex: 1;
  height: 1px;
  background: linear-gradient(to right, var(--accent), transparent);
  opacity: 0.4;
}

.dm-close {
  background: none;
  border: none;
  font-size: 22px;
  line-height: 1;
  color: var(--ink-3);
  cursor: pointer;
}

.dm-close:hover {
  color: var(--ink);
}

.dm-body {
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.dm-row {
  display: flex;
  gap: 14px;
  align-items: baseline;
}

.dm-row-block {
  flex-direction: column;
  align-items: stretch;
  gap: 6px;
}

.dm-label {
  font-family: var(--font-mono);
  font-size: 9.5px;
  font-weight: 700;
  letter-spacing: 0.06em;
  color: var(--ink-3);
  min-width: 56px;
}

.dm-key {
  font-family: var(--font-mono);
  font-size: 13px;
  background: rgba(30, 64, 175, 0.06);
  color: var(--accent-deep);
  padding: 2px 8px;
  border-radius: 6px;
  font-weight: 700;
}

.dm-value {
  font-family: var(--font-display);
  font-size: 15px;
  color: var(--ink);
  font-weight: 600;
}

.dm-prompt {
  background: rgba(30, 64, 175, 0.03);
  border: 1px solid rgba(30, 64, 175, 0.08);
  padding: 12px 14px;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.7;
  color: var(--ink-2);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 260px;
  overflow-y: auto;
  margin: 0;
  border-radius: 10px;
}

.dm-foot {
  font-family: var(--font-body);
  font-size: 13px;
  color: var(--ink-3);
  margin: 4px 0 0;
}

.dm-foot code {
  font-family: var(--font-mono);
  font-style: normal;
  font-size: 12px;
  background: rgba(30, 64, 175, 0.06);
  color: var(--accent-deep);
  padding: 2px 7px;
  border: 1px solid var(--rule-soft);
  border-radius: 6px;
}

.dm-error {
  font-family: var(--font-body);
  color: #b91c1c;
  margin: 0;
}

.sk-badge {
  font-family: var(--font-mono);
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.06em;
  background: rgba(30, 64, 175, 0.06);
  color: #1e40af;
  padding: 2px 7px;
  border-radius: 999px;
}

.topbar-meta {
  flex: 1;
  display: flex;
  align-items: baseline;
  gap: 10px;
  font-size: 13px;
  overflow: hidden;
}

.meta-eyebrow {
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.06em;
  color: var(--ink-3);
}

.meta-divider {
  color: var(--ink-4);
  font-family: var(--font-display);
  font-size: 16px;
  font-style: normal;
}

.meta-title {
  font-family: var(--font-display);
  font-weight: 600;
  font-size: 16px;
  color: var(--ink);
  font-style: normal;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  letter-spacing: -0.005em;
}

/* Feed */
.qa-stage {
  position: relative;
  flex: 1;
  min-height: 0;
  display: flex;
}

.qa-feed {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  scrollbar-gutter: stable; /* 消息增多滚动条出现时不挤压内容、不抖动 */
}

/* ─── 每日简报面板 ──────────────────────────────────────────────── */
.qa-brief {
  position: absolute;
  top: 8px;
  right: 16px;
  bottom: 0;
  left: 16px;
  z-index: 3;
  overflow-y: auto;
  scrollbar-gutter: stable;
  display: flex;
  flex-direction: column;
  padding: 0;
  gap: 0;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.42);
  backdrop-filter: blur(40px) saturate(200%);
  -webkit-backdrop-filter: blur(40px) saturate(200%);
  border: 1px solid rgba(30, 64, 175, 0.1);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.95),
    inset 0 0 0 1px rgba(255, 255, 255, 0.4),
    0 1px 2px rgba(15, 23, 42, 0.04),
    0 8px 24px -8px rgba(30, 64, 175, 0.18);
}

/* 简报关闭按钮（stage 层级） */
.brief-close-btn {
  position: absolute;
  top: 18px;
  right: 26px;
  z-index: 20;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: 10px;
  border: 1px solid rgba(30, 64, 175, 0.12);
  background: rgba(255, 255, 255, 0.78);
  backdrop-filter: blur(12px);
  color: var(--ink-3, #64748b);
  font-size: 18px;
  line-height: 1;
  cursor: pointer;
  transition: all 0.18s ease;
  pointer-events: auto;
}
.brief-close-btn:hover {
  background: rgba(255, 255, 255, 0.95);
  color: var(--ink-2, #334155);
  border-color: rgba(30, 64, 175, 0.22);
  box-shadow: 0 2px 8px -2px rgba(30, 64, 175, 0.18);
}

/* 简报出入动效 */
.brief-fade-enter-active {
  transition: opacity 0.3s ease;
}
.brief-fade-leave-active {
  transition: opacity 0.2s ease;
}
.brief-fade-enter-from,
.brief-fade-leave-to {
  opacity: 0;
}

/* 内容区渐入 */
.brief-content {
  animation: brief-content-in 0.4s ease both;
}
@keyframes brief-content-in {
  from { opacity: 0; }
  to   { opacity: 1; }
}

/* 简报滚动条 */
.qa-brief::-webkit-scrollbar {
  width: 5px;
}
.qa-brief::-webkit-scrollbar-track {
  background: transparent;
  margin: 8px 0;
}
.qa-brief::-webkit-scrollbar-thumb {
  background: rgba(30, 64, 175, 0.08);
  border-radius: 3px;
}
.qa-brief::-webkit-scrollbar-thumb:hover {
  background: rgba(30, 64, 175, 0.18);
}

.brief-section {
  width: 100%;
}

.brief-section + .brief-section {
  margin-top: 16px;
}

.brief-section :deep(.html-render),
.brief-section :deep(iframe) {
  width: 100%;
  border-radius: 4px;
}

.brief-section--skills {
  padding: 20px 0 8px;
  border-top: 1px solid var(--rule, #e2e8f0);
}

.brief-skills-label {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--ink-4, #94a3b8);
  margin: 0 0 12px;
}

.brief-skills-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.brief-skill-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  border: 1px solid rgba(30, 64, 175, 0.12);
  background: rgba(255, 255, 255, 0.62);
  color: #1e40af;
  font-family: var(--font-body);
  font-size: 13px;
  font-weight: 600;
  border-radius: 11px;
  cursor: pointer;
  transition: all 0.2s ease;
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.95), inset 0 0 0 1px rgba(255,255,255,0.4);
}

.brief-skill-chip:hover {
  background: linear-gradient(110deg, #1e40af 0%, #2563eb 35%, #0ea5e9 70%, #0891b2 100%);
  color: #fff;
  border-color: transparent;
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.4), 0 4px 14px -2px rgba(30,64,175,0.45);
  transform: translateY(-1px);
}

.bsc-arrow {
  font-size: 12px;
  opacity: 0.6;
  transition: opacity 0.15s, transform 0.15s;
}

.brief-skill-chip:hover .bsc-arrow {
  opacity: 1;
  transform: translateX(3px);
}

.brief-error {
  margin-top: 16px;
  padding: 12px 16px;
  border: 1px solid rgba(220, 38, 38, 0.15);
  border-left: 3px solid #dc2626;
  background: rgba(254, 242, 242, 0.8);
  backdrop-filter: blur(20px);
  color: #b91c1c;
  font-family: var(--font-mono);
  font-size: 12px;
  border-radius: 10px;
}

/* ── D 版简报加载动画（dlp = d-light-polished）── */
.brief-loading-hint { display: none; }
.blh-dot            { display: none; }

.dlp-card {
  position: relative;
  z-index: 2;
  background: #fff;
  border-radius: 16px;
  padding: 28px 32px;
  box-shadow: 0 1px 2px rgba(0,0,0,.04), 0 4px 16px rgba(0,0,0,.06), 0 16px 40px rgba(0,0,0,.04);
  animation: dlp-in .4s ease-out both;
}
@keyframes dlp-in {
  from { opacity: 0; transform: translateY(6px); }
  to   { opacity: 1; transform: translateY(0); }
}

/* topbar */
.dlp-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
}
.dlp-logo { display: flex; align-items: center; gap: 9px; }
.dlp-logo-icon {
  width: 28px; height: 28px; border-radius: 7px;
  background: linear-gradient(135deg, #3b82f6, #6366f1);
  display: flex; align-items: center; justify-content: center;
}
.dlp-logo-name { font-size: 13px; font-weight: 700; color: #0f172a; letter-spacing: -.01em; }
.dlp-topright  { display: flex; align-items: center; gap: 10px; }
.dlp-date-chip {
  font-size: 10px; color: #94a3b8; background: #f1f5f9;
  padding: 2px 9px; border-radius: 20px; font-variant-numeric: tabular-nums;
}
.dlp-status-badge {
  display: inline-flex; align-items: center; gap: 5px;
  background: linear-gradient(135deg, rgba(59,130,246,.07), rgba(99,102,241,.07));
  border: 1px solid rgba(99,102,241,.2); color: #6366f1;
  font-size: 9px; font-weight: 700; padding: 2px 9px; border-radius: 20px; letter-spacing: .1em;
}
.dlp-bdot {
  width: 4px; height: 4px; border-radius: 50%; background: #6366f1;
  animation: dlp-blink .8s step-end infinite;
}
@keyframes dlp-blink { 50% { opacity: 0; } }

/* center row: orbit + typewriter */
.dlp-center {
  display: flex; align-items: center; gap: 24px;
  margin-bottom: 20px; padding: 16px 20px;
  background: linear-gradient(135deg, #f8fafc, #f1f5f9);
  border-radius: 12px; border: 1px solid #e8edf5;
}
.dlp-ow { width: 88px; height: 88px; position: relative; flex-shrink: 0; }
.dlp-ring { position: absolute; border-radius: 50%; border: 1px solid rgba(99,102,241,.12); }
.dlp-ring--1 { inset: 14px; }
.dlp-ring--2 { inset: 4px; }
.dlp-orb { position: absolute; inset: 0; animation: dlp-rot var(--dur) linear infinite var(--dir, normal); }
.dlp-od  { position: absolute; border-radius: 50%; background: var(--col); box-shadow: 0 0 5px var(--col); }
.dlp-orb--1           { --dur: 2.6s; }
.dlp-orb--1 .dlp-od   { width: 7px; height: 7px; top: 9px; left: 50%; margin-left: -3.5px; --col: #60a5fa; }
.dlp-orb--2           { --dur: 1.9s; --dir: reverse; }
.dlp-orb--2 .dlp-od   { width: 5px; height: 5px; top: 1px; left: 50%; margin-left: -2.5px; --col: #a78bfa; }
.dlp-orb--3           { --dur: 3.8s; }
.dlp-orb--3 .dlp-od   { width: 5px; height: 5px; top: -1px; left: 50%; margin-left: -2.5px; --col: #34d399; }
@keyframes dlp-rot { to { transform: rotate(360deg); } }
.dlp-core {
  position: absolute; inset: 0; margin: auto;
  width: 30px; height: 30px; border-radius: 50%;
  background: linear-gradient(135deg, #3b82f6, #6366f1);
  box-shadow: 0 0 12px rgba(59,130,246,.4);
  display: flex; align-items: center; justify-content: center; z-index: 4;
  animation: dlp-core-pulse 2.5s ease-in-out infinite;
}
@keyframes dlp-core-pulse {
  0%, 100% { box-shadow: 0 0 12px rgba(59,130,246,.4); }
  50%       { box-shadow: 0 0 20px rgba(59,130,246,.7); }
}
.dlp-core-ring {
  position: absolute;
  border-radius: 50%;
  border: 1.5px solid rgba(255,255,255,.5);
  border-top-color: transparent;
}
.dlp-core-ring--outer {
  width: 20px; height: 20px;
  animation: dlp-spin-cw .9s linear infinite;
}
.dlp-core-ring--inner {
  width: 12px; height: 12px;
  border-color: rgba(255,255,255,.35);
  border-bottom-color: transparent;
  animation: dlp-spin-ccw 1.3s linear infinite;
}
.dlp-core-dot {
  width: 4px; height: 4px; border-radius: 50%;
  background: #fff; opacity: .9;
  position: absolute;
}
@keyframes dlp-spin-cw  { to { transform: rotate(360deg); } }
@keyframes dlp-spin-ccw { to { transform: rotate(-360deg); } }
.dlp-center-right { flex: 1; min-width: 0; }
.dlp-tw-txt {
  font-size: 14px; font-weight: 600; color: #1e293b; min-height: 20px;
  font-family: 'JetBrains Mono', monospace; letter-spacing: .01em;
}
.dlp-tw-txt::after {
  content: ''; display: inline-block; width: 2px; height: .9em;
  background: #3b82f6; vertical-align: text-bottom; margin-left: 2px;
  animation: dlp-blink .9s step-end infinite;
}
.dlp-tw-sub { font-size: 11px; color: #94a3b8; margin-top: 5px; letter-spacing: .04em; }
.dlp-prog { margin-top: 10px; height: 3px; background: #e2e8f0; border-radius: 2px; overflow: hidden; }
.dlp-pf {
  height: 100%; width: 0;
  background: linear-gradient(90deg, #3b82f6, #6366f1, #8b5cf6);
  background-size: 200% 100%; border-radius: 2px;
  animation: dlp-fill 270s cubic-bezier(.4,0,.2,1) forwards, dlp-sweep 2s linear infinite;
}
@keyframes dlp-fill  { to { width: 90%; } }
@keyframes dlp-sweep { 0% { background-position: 0 0; } 100% { background-position: -200% 0; } }
.dlp-steps { display: flex; margin-top: 9px; }
.dlp-stp {
  display: flex; align-items: center; gap: 4px;
  font-size: 10px; color: #94a3b8;
  font-family: 'JetBrains Mono', monospace;
  transition: color .35s; padding-right: 13px; position: relative;
}
.dlp-stp:not(:last-child)::after { content: '›'; position: absolute; right: 3px; opacity: .3; }
.dlp-stp.active { color: #3b82f6; }
.dlp-stp.done   { color: #22c55e; }
.dlp-stpdot { width: 4px; height: 4px; border-radius: 50%; background: currentColor; flex-shrink: 0; }

/* 2×2 章节网格 */
.dlp-sec-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 20px; }
.dlp-sec {
  background: #f8fafc; border: 1px solid #e8edf5; border-radius: 10px; padding: 12px 14px;
  opacity: 0; transform: translateY(7px); transition: opacity .4s ease, transform .4s ease;
}
.dlp-sec--v { opacity: 1; transform: none; }
.dlp-sec-head { display: flex; align-items: center; gap: 6px; margin-bottom: 9px; }
.dlp-sec-dot  { width: 6px; height: 6px; border-radius: 50%; background: var(--dlp-c); box-shadow: 0 0 4px var(--dlp-c); flex-shrink: 0; }
.dlp-sec-tag  { font-size: 10px; font-weight: 600; color: var(--dlp-c); letter-spacing: .1em; text-transform: uppercase; }
.dlp-sec-cnt  { font-size: 9px; color: #cbd5e1; margin-left: auto; }
.dlp-lns { display: flex; flex-direction: column; gap: 5px; }
.dlp-ln {
  height: 8px; border-radius: 3px;
  background: linear-gradient(90deg, #f1f5f9 25%, #e8edf5 50%, #f1f5f9 75%);
  background-size: 200% 100%; animation: dlp-shimmer 2s linear infinite;
}
@keyframes dlp-shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }

/* footer */
.dlp-foot  { display: flex; align-items: center; justify-content: space-between; }
.dlp-footl { display: flex; align-items: center; gap: 7px; }
.dlp-sp {
  width: 13px; height: 13px;
  border: 2px solid #e2e8f0; border-top-color: #3b82f6;
  border-radius: 50%; animation: dlp-spin .8s linear infinite;
}
@keyframes dlp-spin { to { transform: rotate(360deg); } }
.dlp-ft    { font-size: 11px; color: #64748b; font-family: 'JetBrains Mono', monospace; }
.dlp-stats { display: flex; gap: 16px; }
.dlp-stat  { display: flex; flex-direction: column; align-items: flex-end; }
.dlp-sn    { font-size: 15px; font-weight: 700; color: #3b82f6; font-variant-numeric: tabular-nums; line-height: 1; }
.dlp-sl    { font-size: 9px; color: #94a3b8; margin-top: 2px; letter-spacing: .04em; }



.qa-feed::-webkit-scrollbar {
  width: 8px;
}

.qa-feed::-webkit-scrollbar-track {
  background: transparent;
}

.qa-feed::-webkit-scrollbar-thumb {
  background: var(--rule);
  border-radius: 4px;
}

.qa-feed::-webkit-scrollbar-thumb:hover {
  background: var(--ink-4);
}

/* ─── JUMP-TO-BOTTOM ROUND BUTTON ─────────────────────────────────────
   只在脱离跟随时浮现：圆形、墨蓝边、半透明白底配毛玻璃。点击 → 平滑回到底
   并恢复跟随。位置固定在 stage 底部居中偏下，避开右侧 minimap、让出 composer。 */
.qa-jumpdown {
  position: absolute;
  left: 50%;
  bottom: 18px;
  transform: translateX(-50%);
  z-index: 5;
  width: 38px;
  height: 38px;
  border-radius: 50%;
  border: 1px solid rgba(30, 64, 175, 0.22);
  background: rgba(255, 255, 255, 0.78);
  backdrop-filter: blur(14px) saturate(180%);
  -webkit-backdrop-filter: blur(14px) saturate(180%);
  color: var(--accent);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  box-shadow:
    0 4px 14px rgba(15, 23, 42, 0.10),
    0 1px 2px rgba(15, 23, 42, 0.04),
    inset 0 0 0 1px rgba(255, 255, 255, 0.6);
  transition:
    transform 0.22s cubic-bezier(0.22, 1, 0.36, 1),
    box-shadow 0.22s ease,
    background 0.22s ease,
    border-color 0.22s ease;
}

.qa-jumpdown::before {
  /* 一圈极淡的脉冲光晕，提示「这里有新内容」，不抢眼 */
  content: '';
  position: absolute;
  inset: -4px;
  border-radius: 50%;
  border: 1px solid rgba(30, 64, 175, 0.22);
  opacity: 0;
  animation: qa-jumpdown-pulse 2.4s ease-out infinite;
  pointer-events: none;
}

@keyframes qa-jumpdown-pulse {
  0%   { opacity: 0.55; transform: scale(0.86); }
  70%  { opacity: 0;    transform: scale(1.18); }
  100% { opacity: 0;    transform: scale(1.18); }
}

.qa-jumpdown:hover {
  transform: translateX(-50%) translateY(-2px);
  background: #ffffff;
  border-color: rgba(30, 64, 175, 0.4);
  box-shadow:
    0 8px 22px rgba(30, 64, 175, 0.18),
    0 1px 2px rgba(15, 23, 42, 0.06),
    inset 0 0 0 1px rgba(255, 255, 255, 0.7);
}

.qa-jumpdown:active {
  transform: translateX(-50%) translateY(0);
  box-shadow:
    0 2px 8px rgba(15, 23, 42, 0.08),
    inset 0 0 0 1px rgba(255, 255, 255, 0.6);
}

.qa-jumpdown-arrow {
  font-family: var(--font-display);
  font-size: 18px;
  font-weight: 400;
  line-height: 1;
  margin-top: 1px; /* 视觉居中（箭头字形偏顶） */
  transition: transform 0.22s cubic-bezier(0.22, 1, 0.36, 1);
}

.qa-jumpdown:hover .qa-jumpdown-arrow {
  transform: translateY(2px);
}

.qa-jumpdown-enter-from,
.qa-jumpdown-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(8px) scale(0.85);
}

.qa-jumpdown-enter-to,
.qa-jumpdown-leave-from {
  opacity: 1;
  transform: translateX(-50%) translateY(0) scale(1);
}

.qa-jumpdown-enter-active,
.qa-jumpdown-leave-active {
  transition:
    opacity 0.22s ease,
    transform 0.22s cubic-bezier(0.22, 1, 0.36, 1);
}

@media (prefers-reduced-motion: reduce) {
  .qa-jumpdown::before { animation: none; }
}

/* ─── QUESTION TRACK (right-edge minimap) ─────────────────────────── */
/* 收起态只占右缘一条窄缝（34px），减少窄屏遮挡与误触；展开要快（0.18s），不拖泥带水 */
.qa-track {
  position: absolute;
  top: 0;
  bottom: 0;
  right: 6px;
  width: 34px;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  z-index: 4;
  transition: width 0.18s cubic-bezier(0.22, 1, 0.36, 1);
}

/* 展开只向左拓宽，右缘保持 6px 不动：若右缘跟着位移，收在右缘附近的鼠标会被
   移出命中区 → mouseleave 收折 → 又移回命中区 → 无限收折/展开抖动 */
.qa-track.is-hover {
  width: 296px;
}

.qa-track-rail {
  position: relative;
  width: 100%;
  max-height: calc(100% - 48px);
  padding: 18px 4px;
  display: flex;
  flex-direction: column;
  justify-content: safe center;
  gap: 4px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 14px;
  transition: background 0.18s ease,
              box-shadow 0.18s ease,
              border-color 0.18s ease,
              padding 0.18s ease;
  overflow-y: auto;
  overflow-x: hidden;
  scrollbar-width: none;
}

.qa-track-rail::-webkit-scrollbar {
  display: none;
}

.qa-track.is-hover .qa-track-rail {
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.96), rgba(245, 246, 248, 0.96));
  border-color: var(--rule);
  box-shadow: 0 18px 48px -28px rgba(15, 23, 42, 0.28),
              0 4px 14px -10px rgba(15, 23, 42, 0.16);
  backdrop-filter: blur(8px);
  padding: 18px 16px;
}

.qa-track-line {
  display: none;
}

.qa-track-tick {
  position: relative;
  appearance: none;
  background: none;
  border: 0;
  cursor: pointer;
  display: flex;
  align-items: center;
  width: 100%;
  min-height: 24px;
  flex-shrink: 0;
  padding: 6px 0;
  color: var(--ink-3);
  text-align: left;
  font-family: var(--font-body);
  box-sizing: border-box;
  border-radius: 8px;
  transition: color 0.15s ease, background 0.15s ease, padding 0.18s ease;
}

.qa-track.is-hover .qa-track-tick {
  padding-left: 14px;
  padding-right: 38px;
}

.qa-track.is-hover .qa-track-tick:hover {
  background: rgba(30, 64, 175, 0.05);
}

.qa-track-dot {
  position: absolute;
  right: 4px;
  top: 50%;
  width: 16px;
  height: 3px;
  background: var(--ink-4);
  opacity: 0.85;
  border-radius: 2px;
  transform: translateY(-50%);
  transition: width 0.18s cubic-bezier(0.22, 1, 0.36, 1),
              height 0.18s ease,
              background 0.15s ease,
              opacity 0.15s ease;
}

.qa-track-tick:hover .qa-track-dot {
  background: var(--ink-2);
  opacity: 1;
  width: 20px;
}

.qa-track-tick.active .qa-track-dot {
  width: 20px;
  height: 4px;
  background: var(--accent);
  opacity: 1;
}

.qa-track.is-hover .qa-track-dot {
  width: 22px;
  height: 2px;
  opacity: 0.55;
}

.qa-track.is-hover .qa-track-tick.active .qa-track-dot {
  width: 28px;
  height: 3px;
  opacity: 1;
}

.qa-track-meta {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: baseline;
  gap: 8px;
  opacity: 0;
  visibility: hidden;
  pointer-events: none;
  transform: translateX(6px);
  transition: opacity 0.16s ease, transform 0.16s ease;
  contain: layout style;
}

.qa-track.is-hover .qa-track-meta {
  opacity: 1;
  visibility: visible;
  pointer-events: auto;
  transform: translateX(0);
}

.qa-track-seq {
  flex-shrink: 0;
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 500;
  letter-spacing: 0.06em;
  color: var(--ink-4);
}

.qa-track-tick.active .qa-track-seq {
  color: var(--accent);
}

.qa-track-text {
  flex: 1;
  min-width: 0;
  font-size: 12.5px;
  line-height: 1.45;
  /* 统一单行省略（完整文案在 tick 的 title 悬浮提示）：原先收起态文字被窄宽度
     折成两行、展开态缩回一行，行高随 hover 变化导致横条间距改变；单行定高后
     挪前挪后间距完全一致 */
  color: var(--ink-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.qa-track-tick:hover .qa-track-text {
  color: var(--ink);
}

.qa-track-tick.active .qa-track-text {
  color: var(--ink);
  font-weight: 500;
}

@media (max-width: 768px) {
  .qa-track {
    display: none;
  }
}

/* ─── CODEX：简报收起 + 会话为空时的能力图鉴（单屏布局，不滚屏） ───────── */
.qa-showcase {
  position: relative;
  max-width: 1120px;
  margin: 0 auto;
  padding: 26px 40px 18px;
  height: 100%;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* 环境层：点阵 + 巨型章节编号水印（随章节切换淡变） */
.codex-ambient {
  position: absolute;
  inset: -24px; /* 超出画布一圈，视差漂移时不露边 */
  overflow: hidden;
  pointer-events: none;
  z-index: 0;
  transform: translate3d(var(--parx, 0), var(--pary, 0), 0);
  transition: transform 0.9s cubic-bezier(0.22, 1, 0.36, 1);
}

.codex-dots {
  position: absolute;
  inset: 0;
  background-image: radial-gradient(circle at 1px 1px, rgba(30, 64, 175, 0.08) 1px, transparent 0);
  background-size: 24px 24px;
  -webkit-mask-image: linear-gradient(to bottom, rgba(0, 0, 0, 0.95), transparent 78%);
  mask-image: linear-gradient(to bottom, rgba(0, 0, 0, 0.95), transparent 78%);
}

.codex-ghostno {
  position: absolute;
  top: -30px;
  right: 10px;
  font-family: var(--font-display);
  font-weight: 800;
  font-size: 180px;
  line-height: 1;
  letter-spacing: -0.04em;
  color: rgba(30, 64, 175, 0.05);
  font-variant-numeric: tabular-nums;
  user-select: none;
}

/* 书桌模式：章节号水印换成职业印记，同样极淡，像书桌垫板下的压印 */
.codex-ghostno--glyph {
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 148px;
  color: rgba(8, 145, 178, 0.055);
}

.codex-ghostno--glyph :deep(svg),
.codex-ghostno--glyph svg {
  width: 1em;
  height: 1em;
}

.codex-ghost-enter-active,
.codex-ghost-leave-active {
  transition: opacity 0.3s ease, transform 0.3s ease;
}

.codex-ghost-enter-from {
  opacity: 0;
  transform: translateY(14px) scale(0.98);
}

.codex-ghost-leave-to {
  opacity: 0;
  transform: translateY(-10px) scale(1.01);
}

.codex-masthead,
.codex-front,
.codex-chapters,
.codex-body {
  position: relative;
  z-index: 1;
}

/* 卷首收展：目录页完整呈现；翻进条目页整块折起（grid-rows 1fr→0fr），
   案例列表顺势长高 —— 与 codex-page 翻页同步发生，像翻开新的一跨页 */
.codex-masthead {
  flex-shrink: 0;
  display: grid;
  grid-template-rows: 1fr;
  transition: grid-template-rows 0.42s cubic-bezier(0.22, 1, 0.36, 1);
}

.codex-masthead.is-collapsed {
  grid-template-rows: 0fr;
}

.codex-masthead-in {
  min-height: 0;
  overflow: hidden; /* BFC：兜住子级 margin，折叠时才能真的收到 0 */
  transition: opacity 0.26s ease, transform 0.4s cubic-bezier(0.22, 1, 0.36, 1);
}

.codex-masthead.is-collapsed .codex-masthead-in {
  opacity: 0;
  transform: translateY(-10px);
  pointer-events: none;
}

/* 卷首：下面那排功能卡的「头」——同族但存在感最低：
   没有底色块、没有阴影，只一抹 5% 淡彩 + 发丝边框，比任何一张卡片都安静；
   圆角 / 图标牌 / --ca 色相与卡片同源，一眼同族，不抢戏 */
.codex-front {
  --ca: #1e40af;
  --ca2: #2563eb;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 14px;
  padding: 13px 16px;
  background-image: linear-gradient(160deg, color-mix(in srgb, var(--ca) 5%, transparent), transparent 55%);
  border: 1px solid color-mix(in srgb, var(--ca) 9%, transparent);
  border-radius: 10px;
  animation: codex-rise 0.5s backwards cubic-bezier(0.22, 1, 0.36, 1);
}

/* 脚下一条极淡的渐变细线：唯一的「头」记号——色相从这里淡淡地发牌给下面的卡片 */
.codex-front::after {
  content: '';
  position: absolute;
  left: 18px;
  right: 18px;
  bottom: 0;
  height: 2px;
  background: linear-gradient(90deg, transparent, color-mix(in srgb, var(--ca) 32%, transparent), transparent);
  pointer-events: none;
}

.codex-front-left {
  display: flex;
  align-items: center;
  gap: 14px;
  min-width: 0;
}

/* № / 职业印记：与功能卡同款的淡彩图标牌（静态，不翻章） */
.codex-mark {
  width: 38px;
  height: 38px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: var(--font-display);
  font-weight: 800;
  font-size: 20px;
  color: var(--ca);
  background: linear-gradient(135deg, color-mix(in srgb, var(--ca) 7%, transparent), color-mix(in srgb, var(--ca2) 13%, transparent));
  border: 1px solid color-mix(in srgb, var(--ca) 16%, transparent);
  border-radius: 6px;
}

/* 书桌模式墨块里放职业印记，与 № 同规格 */
.codex-mark :deep(svg),
.codex-mark svg {
  width: 1em;
  height: 1em;
}

.codex-title {
  margin: 0;
  font-family: var(--font-display);
  font-size: 23px;
  font-weight: 800;
  letter-spacing: -0.02em;
  line-height: 1.15;
  color: var(--ink);
}

.codex-front-right {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-shrink: 0;
}

/* 图鉴统计：版权页式小字，mono 右对齐两行，比卷首里任何元素都安静 */
.codex-meta {
  display: flex;
  flex-direction: column;
  gap: 3px;
  align-items: flex-end;
  text-align: right;
}

.codex-meta span {
  font-family: var(--font-mono);
  font-size: 9.5px;
  letter-spacing: 0.06em;
  color: var(--ink-3);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.codex-meta .codex-meta-hint {
  color: var(--ink-4);
}

/* 已引导用户：我的功能 / 全部功能切换 + 功能设置入口 */
.codex-myctl {
  display: flex;
  align-items: center;
  gap: 8px;
}

.codex-myctl-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 26px;
  padding: 0 11px;
  border: 1px solid rgba(30, 64, 175, 0.16);
  border-radius: 99px;
  background: rgba(255, 255, 255, 0.5);
  color: var(--ink-3);
  font-family: var(--font-display);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.02em;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.18s ease;
}

.codex-myctl-btn :deep(svg),
.codex-myctl-btn svg {
  font-size: 14px;
}

.codex-myctl-btn:hover {
  border-color: rgba(30, 64, 175, 0.35);
  color: var(--accent);
  background: rgba(255, 255, 255, 0.75);
}

.codex-myctl-btn.active {
  border-color: rgba(30, 64, 175, 0.4);
  background: var(--accent-soft);
  color: var(--accent);
}

/* 章节索引 + 极光游标 */
.codex-chapters {
  position: relative;
  display: flex;
  flex-wrap: wrap;
  gap: 2px 6px;
  margin-bottom: 14px;
  flex-shrink: 0;
}

.codex-cursor {
  position: absolute;
  top: 0;
  height: 3px;
  border-radius: 2px;
  background: var(--aurora);
  background-size: 200% 100%;
  animation: codex-holo 5.2s ease-in-out infinite;
  box-shadow: 0 2px 8px -2px rgba(30, 64, 175, 0.5);
  transition:
    left 0.32s cubic-bezier(0.22, 1, 0.36, 1),
    top 0.32s cubic-bezier(0.22, 1, 0.36, 1),
    width 0.32s cubic-bezier(0.22, 1, 0.36, 1);
}

.codex-chapter {
  display: inline-flex;
  align-items: baseline;
  gap: 8px;
  padding: 5px 12px 7px;
  background: transparent;
  border: none;
  cursor: pointer;
  font-family: var(--font-body);
  transition: transform 0.2s;
}

.codex-chapter:hover {
  transform: translateY(-1px);
}

.codex-chapter-no {
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.1em;
  color: var(--ink-4);
  transition: color 0.2s;
}

.codex-chapter-name {
  font-size: 14.5px;
  font-weight: 700;
  letter-spacing: -0.01em;
  color: var(--ink-2);
  transition: color 0.2s;
}

.codex-chapter-count {
  font-family: var(--font-mono);
  font-size: 9px;
  font-weight: 700;
  color: var(--ink-4);
  border: 1px solid var(--rule);
  border-radius: 999px;
  padding: 1px 6px;
  align-self: center;
  transition: all 0.2s;
}

.codex-chapter.is-active .codex-chapter-no {
  color: var(--accent);
}

.codex-chapter.is-active .codex-chapter-name {
  color: var(--ink);
  font-weight: 800;
}

.codex-chapter.is-active .codex-chapter-count {
  color: var(--accent);
  border-color: rgba(30, 64, 175, 0.3);
  background: var(--accent-soft);
}

/* 卡片区：吃满剩余高度；内容少时垂直居中，塞不下时退化为可滚 */
.codex-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  scrollbar-width: none;
}

.codex-body::-webkit-scrollbar {
  display: none;
}

/* 列宽封顶（auto-fill + 296px 上限）：只有一两张卡时不再被 1fr 拉满整行，
   整排水平居中摆放——卡片宽度稳定，不随数量变化；
   垂直方向不居中，从上往下依次排列；顶部留白拉开与卷首的距离 */
.codex-grid {
  margin: 14px 0 0;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(250px, 296px));
  justify-content: center;
  grid-auto-rows: auto;
  gap: 14px;
}

/* 入场用 backwards 填充：动画结束后不霸占 transform，悬停位移才生效 */
@keyframes codex-rise {
  from { opacity: 0; transform: translateY(14px); }
  to   { opacity: 1; transform: translateY(0); }
}

/* 镭射流光（同 nian 灵感卡 holo-shift 手法）：200% 渐变来回扫 */
@keyframes codex-holo {
  0%, 100% { background-position: 0% 0%; }
  50%      { background-position: 100% 0%; }
}

/* ── 目录卡：整卡可点，翻到该功能的条目页 ── */
.codex-card {
  --ca: #1e40af;
  --ca2: #2563eb;
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 9px;
  padding: 15px 15px 13px;
  background: var(--surface-deep);
  background-image: linear-gradient(160deg, color-mix(in srgb, var(--ca) 6%, transparent), transparent 46%);
  border: 1px solid var(--rule);
  border-radius: 10px;
  box-shadow: inset 0 1px 0 var(--highlight), var(--shadow-sm);
  cursor: pointer;
  overflow: hidden;
  transition: border-color 0.22s, box-shadow 0.22s, transform 0.22s;
  animation: codex-rise 0.5s var(--delay, 0s) backwards cubic-bezier(0.22, 1, 0.36, 1);
}

/* 灯光：一盏跟着光标走的检视灯（悬停才点亮） */
.codex-card::before {
  content: '';
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: radial-gradient(240px circle at var(--mx, 50%) var(--my, 40%), color-mix(in srgb, var(--ca) 15%, transparent), transparent 70%);
  opacity: 0;
  transition: opacity 0.3s;
}

.codex-card:hover {
  border-color: color-mix(in srgb, var(--ca) 35%, transparent);
  box-shadow: inset 0 1px 0 var(--highlight), 0 1px 2px rgba(15, 23, 42, 0.05), 0 12px 32px -12px color-mix(in srgb, var(--ca) 32%, transparent);
  transform: perspective(800px) rotateX(var(--tilt-x, 0deg)) rotateY(var(--tilt-y, 0deg)) translateY(-3px);
}

.codex-card:hover::before {
  opacity: 1;
}

/* 按下：纸片回弹 */
.codex-card:active {
  transform: perspective(800px) translateY(-1px) scale(0.985);
  transition-duration: 0.08s;
}

/* 顶部镭射防伪丝线：拉满整条顶边，本卡色相流光（相位按卡序错开，悬停微提亮）；
   渐变整体混白压成粉彩、不透明度调低，做安静的点缀而非视觉主角 */
.codex-card::after {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 4px;
  background: linear-gradient(90deg, var(--ca) 0%, var(--ca2) 30%, color-mix(in srgb, var(--ca2) 40%, #fff) 50%, var(--ca2) 70%, var(--ca) 100%);
  background-size: 200% 100%;
  animation: codex-holo 4.6s ease-in-out infinite;
  animation-delay: var(--holo, 0s);
  opacity: 0.92;
  transition: opacity 0.25s, box-shadow 0.25s;
}

.codex-card:hover::after {
  opacity: 1;
  box-shadow: 0 3px 12px -2px color-mix(in srgb, var(--ca) 70%, transparent);
}

/* 卡头：编号 + 名称 + 索书号 */
.codex-card-head {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
}

.codex-card-no {
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.1em;
  color: color-mix(in srgb, var(--ca) 45%, transparent);
  flex-shrink: 0;
  transition: color 0.2s;
}

.codex-card:hover .codex-card-no {
  color: var(--ca);
}

.codex-card-titles {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  flex: 1;
}

.codex-card-name {
  font-family: var(--font-display);
  font-size: 15.5px;
  font-weight: 800;
  letter-spacing: -0.015em;
  color: var(--ink);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  transition: color 0.2s;
}

.codex-card:hover .codex-card-name {
  color: var(--ca);
}

.codex-callno {
  font-family: var(--font-mono);
  font-size: 9.5px;
  font-weight: 600;
  color: var(--ca);
  background: color-mix(in srgb, var(--ca) 8%, transparent);
  border: 1px solid color-mix(in srgb, var(--ca) 20%, transparent);
  padding: 1.5px 6px;
  border-radius: 3px;
  align-self: flex-start;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 索书号与「已订阅」章同行排布 */
.codex-card-tags {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  min-width: 0;
}

/* 「已订阅」小章：青色系、极克制，只在「全部功能」视图出现 */
.codex-mine {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-family: var(--font-mono);
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: var(--c-cyan);
  background: rgba(8, 145, 178, 0.08);
  border: 1px solid rgba(8, 145, 178, 0.24);
  padding: 1.5px 6px;
  border-radius: 3px;
  white-space: nowrap;
}

.codex-mine::before {
  content: '✓';
  font-size: 8.5px;
}

/* 功能图标牌：该色相的淡彩底，悬停整卡时翻成同色印章（微倾斜，像盖章） */
.codex-card-ico {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  flex-shrink: 0;
  font-size: 20px;
  color: var(--ca);
  background: linear-gradient(135deg, color-mix(in srgb, var(--ca) 7%, transparent), color-mix(in srgb, var(--ca2) 13%, transparent));
  border: 1px solid color-mix(in srgb, var(--ca) 18%, transparent);
  border-radius: 6px;
  box-shadow: inset 0 1px 0 var(--highlight);
  transition: color 0.25s, background 0.25s, border-color 0.25s, transform 0.25s, box-shadow 0.25s;
}

.codex-card:hover .codex-card-ico {
  color: #fff;
  background: linear-gradient(160deg, var(--ca), var(--ca2));
  border-color: transparent;
  box-shadow: 0 6px 16px -6px color-mix(in srgb, var(--ca) 75%, transparent);
  transform: translateY(-2px) rotate(-4deg);
}
/* 技能 svg/图片图标在瓦片内的尺寸（iconify 回落走 font-size 不受影响） */
.codex-card-ico :deep(svg),
.codex-entry-ico :deep(svg) {
  width: 22px;
  height: 22px;
  display: block;
}
.codex-card-ico :deep(img),
.codex-entry-ico :deep(img) {
  width: calc(100% - 6px);
  height: calc(100% - 6px);
  object-fit: cover;
  border-radius: 6px;
  display: block;
}

.codex-card-desc {
  margin: 0;
  font-size: 12px;
  line-height: 1.55;
  color: var(--ink-3);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* 卡脚：试一试（填 @技能进输入框）+ 案例数（提示整卡可点进条目） */
.codex-card-foot {
  margin-top: auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding-top: 9px;
  border-top: 1px dashed rgba(15, 23, 42, 0.08);
}

.codex-card-try {
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: var(--ca);
  background: transparent;
  border: 1px solid color-mix(in srgb, var(--ca) 30%, transparent);
  border-radius: 4px;
  padding: 3.5px 9px;
  cursor: pointer;
  transition: background 0.2s, border-color 0.2s, box-shadow 0.2s;
}

.codex-card-try i {
  font-style: normal;
  display: inline-block;
  transition: transform 0.2s;
}

.codex-card-try:hover {
  background: color-mix(in srgb, var(--ca) 9%, transparent);
  border-color: color-mix(in srgb, var(--ca) 55%, transparent);
  box-shadow: 0 3px 10px -4px color-mix(in srgb, var(--ca) 55%, transparent);
}

.codex-card-try:hover i {
  transform: translate(2px, -2px);
}

.codex-card-cases {
  font-family: var(--font-mono);
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--ink-4);
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
  transition: color 0.2s;
}

.codex-card-cases b {
  font-weight: 700;
  color: var(--ca);
}

.codex-card-cases i {
  font-style: normal;
  display: inline-block;
  opacity: 0.55;
  transition: transform 0.2s, opacity 0.2s;
}

.codex-card:hover .codex-card-cases {
  color: var(--ink-2);
}

.codex-card:hover .codex-card-cases i {
  transform: translateX(3px);
  opacity: 1;
}

.codex-card-cases--none {
  letter-spacing: 0.18em;
  opacity: 0.7;
}

/* ── 条目页：一个功能 + 它的全部案例（列表内部滚，页面仍是单屏） ── */
/* 条目页是案例阅读态（卷首已折起），栏目放宽到 940px，标本卡更舒展 */
.codex-entry {
  --ca: #1e40af;
  --ca2: #2563eb;
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  width: 100%;
  max-width: 940px;
  margin: 0 auto;
}

.codex-entry-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  flex-shrink: 0;
}

.codex-back {
  font-family: var(--font-mono);
  font-size: 10.5px;
  font-weight: 700;
  letter-spacing: 0.1em;
  color: var(--ink-3);
  background: transparent;
  border: none;
  padding: 4px 2px;
  cursor: pointer;
  transition: color 0.2s;
}

.codex-back i {
  font-style: normal;
  display: inline-block;
  transition: transform 0.2s;
}

.codex-back:hover {
  color: var(--accent);
}

.codex-back:hover i {
  transform: translateX(-3px);
}

/* 条目页主按钮：该功能色相的渐变实色 */
.codex-try {
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.1em;
  color: #fff;
  background: linear-gradient(160deg, var(--ca), var(--ca2));
  border: none;
  border-radius: 6px;
  padding: 7px 16px;
  cursor: pointer;
  box-shadow: 0 4px 14px -5px color-mix(in srgb, var(--ca) 75%, transparent);
  transition: transform 0.2s, box-shadow 0.2s, filter 0.2s;
}

.codex-try i {
  font-style: normal;
  display: inline-block;
  transition: transform 0.2s;
}

.codex-try:hover {
  transform: translateY(-1px);
  filter: brightness(1.06);
  box-shadow: 0 8px 20px -6px color-mix(in srgb, var(--ca) 80%, transparent);
}

.codex-try:hover i {
  transform: translate(2px, -2px);
}

.codex-entry-head {
  display: flex;
  align-items: baseline;
  gap: 12px;
  flex-shrink: 0;
}

.codex-entry-no {
  font-family: var(--font-mono);
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.1em;
  color: color-mix(in srgb, var(--ca) 60%, transparent);
}

/* 条目页图标牌：比目录卡更大一号，可悬停翻转 */
.codex-entry-ico {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 46px;
  height: 46px;
  flex-shrink: 0;
  font-size: 24px;
  color: var(--ca);
  background: linear-gradient(135deg, color-mix(in srgb, var(--ca) 8%, transparent), color-mix(in srgb, var(--ca2) 15%, transparent));
  border: 1px solid color-mix(in srgb, var(--ca) 20%, transparent);
  border-radius: 8px;
  align-self: center;
  box-shadow: inset 0 1px 0 var(--highlight);
  transition: color 0.25s, background 0.25s, border-color 0.25s, transform 0.25s, box-shadow 0.25s;
}

.codex-entry-ico:hover {
  color: #fff;
  background: linear-gradient(160deg, var(--ca), var(--ca2));
  border-color: transparent;
  box-shadow: 0 8px 20px -7px color-mix(in srgb, var(--ca) 78%, transparent);
  transform: translateY(-2px) rotate(-4deg);
}

.codex-entry-name {
  margin: 0;
  font-family: var(--font-display);
  font-size: 23px;
  font-weight: 800;
  letter-spacing: -0.02em;
  color: var(--ink);
  min-width: 0;
}

.codex-entry-head .codex-callno {
  align-self: center;
}

.codex-entry-desc {
  margin: 7px 0 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--ink-3);
  flex-shrink: 0;
}

/* 案例分隔线：短粗墨线 + 计数 + 细线收尾 */
.codex-entry-rule {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 16px 0 12px;
  flex-shrink: 0;
}

.codex-entry-rule::before {
  content: '';
  flex: 0 0 26px;
  height: 2px;
  background: linear-gradient(90deg, var(--ca), var(--ca2));
}

.codex-entry-rule span {
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.14em;
  color: var(--ink-3);
  white-space: nowrap;
}

.codex-entry-rule::after {
  content: '';
  flex: 1;
  height: 2px;
  border-radius: 1px;
  background: linear-gradient(90deg, var(--ca), var(--ca2) 28%, color-mix(in srgb, var(--ca2) 50%, #fff) 50%, var(--ca2) 72%, transparent 97%);
  background-size: 200% 100%;
  animation: codex-holo 5s ease-in-out infinite;
}

/* 案例列表：单列吃满剩余高度，内部滚动 —— 案例再多也不破单屏 */
.codex-cases {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
  scrollbar-width: none;
}

.codex-cases::-webkit-scrollbar {
  display: none;
}

/* 案例标本卡：左侧齐边媒体面板 + 右侧分区文字；悬停有边光 / 上浮 / 推镜 / 高光扫过 */
.codex-case {
  position: relative;
  display: flex;
  align-items: stretch;
  gap: 0;
  padding: 0;
  overflow: hidden;
  background: var(--surface-deep);
  background-image: linear-gradient(160deg, color-mix(in srgb, var(--ca) 4%, transparent), transparent 55%);
  border: 1px solid var(--rule);
  border-radius: 10px;
  box-shadow: inset 0 1px 0 var(--highlight);
  cursor: pointer;
  text-align: left;
  font-family: inherit;
  color: inherit;
  flex-shrink: 0;
  transition: border-color 0.25s, transform 0.25s, box-shadow 0.25s;
  animation: codex-rise 0.45s var(--delay, 0s) backwards cubic-bezier(0.22, 1, 0.36, 1);
}

.codex-case:hover {
  border-color: color-mix(in srgb, var(--ca) 42%, transparent);
  box-shadow:
    inset 3px 0 0 var(--ca),
    inset 0 1px 0 var(--highlight),
    0 16px 34px -16px color-mix(in srgb, var(--ca) 55%, transparent);
  transform: translateX(3px);
}

/* ── 左侧媒体面板：齐边、无白框，靠分区线与文字区分开 ── */
.codex-case-media {
  position: relative;
  width: 236px;
  flex: 0 0 236px;
  align-self: stretch;
  overflow: hidden;
  background: color-mix(in srgb, var(--ca) 6%, #fff);
}

.codex-case-media img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  transition: transform 0.5s cubic-bezier(0.22, 1, 0.36, 1);
}

.codex-case:hover .codex-case-media img {
  transform: scale(1.06);
}

/* 底部常驻暗角，保证帧计数等叠层在浅色图上也清晰 */
.codex-case-media::before {
  content: '';
  position: absolute;
  inset: auto 0 0 0;
  height: 46%;
  background: linear-gradient(to top, rgba(8, 12, 24, 0.5), transparent);
  pointer-events: none;
}

/* 悬停高光扫过，给静态截图一点"活"的质感 */
.codex-case-media::after {
  content: '';
  position: absolute;
  top: 0;
  left: -60%;
  width: 45%;
  height: 100%;
  background: linear-gradient(105deg, transparent, rgba(255, 255, 255, 0.38), transparent);
  transform: skewX(-14deg);
  pointer-events: none;
}

.codex-case:hover .codex-case-media::after {
  animation: codex-sheen 0.9s ease-out;
}

@keyframes codex-sheen {
  to { left: 130%; }
}

/* 多帧计数：悬停横向扫动时跟随切帧 */
.codex-case-frame {
  position: absolute;
  left: 8px;
  bottom: 7px;
  z-index: 1;
  font-family: var(--font-mono);
  font-size: 9.5px;
  font-weight: 700;
  letter-spacing: 0.1em;
  color: #fff;
  background: rgba(8, 12, 24, 0.55);
  border: 1px solid rgba(255, 255, 255, 0.18);
  border-radius: 5px;
  padding: 2px 6px;
  backdrop-filter: blur(2px);
  pointer-events: none;
}

/* 放大按钮：悬停浮现，点开展开大图（不触发案例加载） */
.codex-case-zoom {
  position: absolute;
  right: 8px;
  bottom: 7px;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  font-size: 14px;
  color: #fff;
  background: rgba(8, 12, 24, 0.5);
  border: 1px solid rgba(255, 255, 255, 0.18);
  border-radius: 7px;
  backdrop-filter: blur(2px);
  opacity: 0;
  transform: translateY(4px);
  transition: opacity 0.22s, transform 0.22s, background 0.2s;
  cursor: zoom-in;
}

.codex-case:hover .codex-case-zoom {
  opacity: 1;
  transform: translateY(0);
}

.codex-case-zoom:hover {
  background: color-mix(in srgb, var(--ca) 88%, #000);
}

/* ── 右侧文字区：与媒体面板用品牌色细线分区 ── */
.codex-case-foot {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 13px 16px 13px 18px;
}

.codex-case:has(.codex-case-media) .codex-case-foot {
  border-left: 1px solid color-mix(in srgb, var(--ca) 16%, var(--rule));
  transition: border-color 0.25s;
}

.codex-case:hover:has(.codex-case-media) .codex-case-foot {
  border-left-color: color-mix(in srgb, var(--ca) 45%, transparent);
}

.codex-case-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 5px;
}

/* mono 标签行：CASE 01 · @技能 —— 取代原来苍白的序号碎块 */
.codex-case-kicker {
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: color-mix(in srgb, var(--ca) 72%, var(--ink-3));
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  transition: color 0.25s;
}

.codex-case:hover .codex-case-kicker {
  color: var(--ca);
}

.codex-case-title {
  min-width: 0;
  font-size: 14.5px;
  font-weight: 700;
  letter-spacing: -0.01em;
  line-height: 1.4;
  color: var(--ink-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  transition: color 0.25s;
}

.codex-case:hover .codex-case-title {
  color: var(--ink);
}

/* 带图卡纵向余量足，标题允许两行；无图卡单行省略保持紧凑 */
.codex-case:has(.codex-case-media) .codex-case-title {
  white-space: normal;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* ── 试一试：常驻描边胶囊，悬停填实色，箭头右移 ── */
.codex-case-try {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-family: var(--font-mono);
  font-size: 10.5px;
  font-weight: 700;
  letter-spacing: 0.06em;
  color: var(--ca);
  white-space: nowrap;
  padding: 6px 12px;
  border: 1px solid color-mix(in srgb, var(--ca) 30%, transparent);
  border-radius: 999px;
  background: color-mix(in srgb, var(--ca) 6%, transparent);
  transition: color 0.22s, background 0.22s, border-color 0.22s, box-shadow 0.22s;
}

.codex-case-try i {
  font-style: normal;
  display: inline-block;
  transition: transform 0.22s;
}

.codex-case:hover .codex-case-try {
  color: #fff;
  background: linear-gradient(160deg, var(--ca), var(--ca2));
  border-color: transparent;
  box-shadow: 0 6px 16px -7px color-mix(in srgb, var(--ca) 80%, transparent);
}

.codex-case:hover .codex-case-try i {
  transform: translateX(3px);
}

/* ── 加载案例中：底边进度条往返 + 胶囊转圈 + 其余卡片压暗（disabled 防重复触发） ── */
.codex-case.is-loading {
  border-color: color-mix(in srgb, var(--ca) 45%, transparent);
  box-shadow: inset 3px 0 0 var(--ca), inset 0 1px 0 var(--highlight);
  transform: translateX(3px);
  cursor: progress;
}

.codex-case.is-dim {
  opacity: 0.45;
  filter: saturate(0.5);
  cursor: not-allowed;
}

.codex-case-loading {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 2px;
  z-index: 2;
  overflow: hidden;
  background: color-mix(in srgb, var(--ca) 14%, transparent);
  pointer-events: none;
}

.codex-case-loading::before {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: -40%;
  width: 40%;
  border-radius: 2px;
  background: linear-gradient(90deg, transparent, var(--ca), var(--ca2));
  animation: codex-case-load 1.1s cubic-bezier(0.45, 0, 0.45, 1) infinite;
}

@keyframes codex-case-load {
  to { left: 105%; }
}

.codex-case.is-loading .codex-case-try {
  color: #fff;
  background: linear-gradient(160deg, var(--ca), var(--ca2));
  border-color: transparent;
}

.codex-case-try-spin {
  width: 10px;
  height: 10px;
  border: 1.5px solid rgba(255, 255, 255, 0.45);
  border-top-color: #fff;
  border-radius: 50%;
  animation: codex-case-spin 0.7s linear infinite;
}

@keyframes codex-case-spin {
  to { transform: rotate(360deg); }
}

/* 窄屏：媒体面板转为顶部横幅，整页可滚，故文字置于其下不丢 */
@media (max-width: 640px) {
  /* 章节索引收窄：更小的内边距与字号，多章节换行时不那么占地 */
  .codex-chapter {
    padding: 4px 9px 6px;
    gap: 6px;
  }

  .codex-chapter-name {
    font-size: 13.5px;
  }

  .codex-case {
    flex-direction: column;
  }
  .codex-case-media {
    width: 100%;
    flex-basis: auto;
    aspect-ratio: 16 / 9;
  }
  .codex-case:has(.codex-case-media) .codex-case-foot {
    border-left: none;
    border-top: 1px solid color-mix(in srgb, var(--ca) 16%, var(--rule));
  }

  /* 目录卡列宽收窄，适配小屏 */
  .codex-grid {
    grid-template-columns: repeat(auto-fill, minmax(220px, 296px));
  }

  /* 空白章节幽灵卡收窄 */
  .codex-blank-ghost {
    width: 132px;
  }
}

@media (max-width: 480px) {
  /* 极窄屏单列铺满 */
  .codex-grid {
    grid-template-columns: 1fr;
  }

  /* 幽灵卡只留两张，避免溢出 */
  .codex-blank-ghost:last-child {
    display: none;
  }
}

.codex-entry-empty {
  margin: 0;
  font-size: 12.5px;
  color: var(--ink-4);
  flex-shrink: 0;
}

/* 目录 ⇄ 条目 翻页动画 */
.codex-page-enter-active,
.codex-page-leave-active {
  transition: opacity 0.16s ease, transform 0.16s cubic-bezier(0.22, 1, 0.36, 1);
}

.codex-page-enter-from {
  opacity: 0;
  transform: translateX(14px);
}

.codex-page-leave-to {
  opacity: 0;
  transform: translateX(-10px);
}

/* 章节切换动画 */
.codex-swap-enter-active,
.codex-swap-leave-active {
  transition: opacity 0.18s ease, transform 0.18s cubic-bezier(0.22, 1, 0.36, 1);
}

.codex-swap-enter-from {
  opacity: 0;
  transform: translateY(8px);
}

.codex-swap-leave-to {
  opacity: 0;
  transform: translateY(-6px);
}

/* ── 空白章节：橱窗无功能时的「待收录」跨页 ──
   与 codex 同一套语言（mono kicker + display 标题 + 发丝虚线），
   三张幽灵卡预演将来功能卡的形状，把空白变成有意为之的留白 */
.codex-body--blank {
  overflow: hidden;
}

.codex-blank {
  --ca: #1e40af;
  --ca2: #2563eb;
  margin: auto;
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  max-width: 640px;
  padding: 10px 24px 18px;
  animation: codex-rise 0.5s backwards cubic-bezier(0.22, 1, 0.36, 1);
}

.codex-blank-kicker {
  margin: 0;
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.16em;
  color: var(--ink-4);
}

.codex-blank-title {
  margin: 6px 0 0;
  font-family: var(--font-display);
  font-size: 22px;
  font-weight: 800;
  letter-spacing: -0.02em;
  line-height: 1.25;
  color: var(--ink);
}

.codex-blank-sub {
  margin: 4px 0 0;
  font-size: 13px;
  line-height: 1.75;
  color: var(--ink-3);
}

/* 全局空态的管理员指引小注：全场最安静的字 */
.codex-blank-note {
  margin: 12px 0 0;
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.04em;
  color: var(--ink-4);
}

/* 「我的」空态：重选 / 查看全部 两个动作 */
.codex-blank-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 10px;
  margin-top: 14px;
}

.codex-blank-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  font-family: var(--font-display);
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ca);
  background: transparent;
  border: 1px solid color-mix(in srgb, var(--ca) 28%, transparent);
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.2s, border-color 0.2s, transform 0.2s, filter 0.2s;
}

.codex-blank-btn:hover {
  background: color-mix(in srgb, var(--ca) 7%, transparent);
  border-color: color-mix(in srgb, var(--ca) 48%, transparent);
  transform: translateY(-1px);
}

.codex-blank-btn--solid {
  color: #fff;
  background: linear-gradient(135deg, var(--ca), var(--ca2));
  border-color: transparent;
}

.codex-blank-btn--solid:hover {
  background: linear-gradient(135deg, var(--ca), var(--ca2));
  filter: brightness(1.07);
}

.codex-blank-btn svg {
  width: 14px;
  height: 14px;
}

/* 幽灵卡：虚线框预演功能卡的形状，条纹代替内容 */
.codex-blank-ghosts {
  display: flex;
  gap: 12px;
  margin-top: 20px;
}

.codex-blank-ghost {
  width: 158px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 13px 11px;
  border: 1px dashed color-mix(in srgb, var(--ca) 22%, transparent);
  border-radius: 10px;
  background: color-mix(in srgb, var(--ca) 2%, transparent);
  animation: codex-rise 0.5s var(--delay, 0s) backwards cubic-bezier(0.22, 1, 0.36, 1);
}

.codex-blank-ghost i {
  display: block;
  height: 9px;
  border-radius: 4px;
  background: color-mix(in srgb, var(--ca) 9%, transparent);
}

.codex-blank-ghost .cbg-head {
  height: 13px;
  width: 62%;
}

.codex-blank-ghost .cbg-line {
  width: 100%;
}

.codex-blank-ghost .cbg-line--short {
  width: 72%;
}

.codex-blank-ghost .cbg-foot {
  width: 38%;
  margin-top: 2px;
}

/* 加载中：条纹轻轻呼吸，提示「正在备菜」而非死寂 */
.codex-blank--loading .codex-blank-ghost i {
  animation: codex-blank-pulse 1.3s ease-in-out infinite;
}

.codex-blank--loading .codex-blank-ghost i:nth-child(2) {
  animation-delay: 0.12s;
}

.codex-blank--loading .codex-blank-ghost i:nth-child(3) {
  animation-delay: 0.24s;
}

.codex-blank--loading .codex-blank-ghost i:nth-child(4) {
  animation-delay: 0.36s;
}

@keyframes codex-blank-pulse {
  0%,
  100% {
    opacity: 0.4;
  }
  50% {
    opacity: 1;
  }
}

@media (prefers-reduced-motion: reduce) {
  .codex-card::after,
  .codex-cursor,
  .codex-entry-rule::after,
  .codex-blank--loading .codex-blank-ghost i {
    animation: none;
  }

  .codex-masthead,
  .codex-masthead-in {
    transition: none;
  }
}

/* ─── CONVERSATION ───────────────────────────────────────────────── */
.conversation {
  max-width: 820px;
  margin: 0 auto;
  padding: 36px 48px 56px;
}

.exchange {
  margin-bottom: 56px;
  animation: rise 0.6s ease-out;
}

.exchange:last-child {
  margin-bottom: 24px;
}

/* ── 问卷续接：问卷前回复 + 作答后回复本质是同一次回答（问卷只是中间状态） ── */
/* 问卷宿主回复与续接段近乎贴着（仅剩问卷卡片自身 10px 下边距） */
.exchange.pre-continuation {
  margin-bottom: 0;
}

/* 续接段不重复 answer-mark，正文与上一段答案体左对齐（头像 28px + gap 16px） */
.exchange.continuation .answer-mark {
  display: none;
}

.exchange.continuation .answer {
  padding-top: 0;
}

.exchange.continuation .assistant-response {
  padding-left: 44px;
}

/* 贴着：压掉问卷卡片下边距与续接段首个块的上边距 */
.exchange.pre-continuation .answer-body-segments > :last-child {
  margin-bottom: 0;
}

.exchange.continuation .answer-body :deep(> :first-child) {
  margin-top: 0;
}

/* User question（样张语言：右对齐暖灰气泡，无分隔线） */
.user-question {
  display: flex;
  justify-content: flex-end;
  gap: 0;
  padding-bottom: 0;
  border-bottom: none;
}

/* Q. 标记退役（样张：用户气泡无标记） */
.q-mark {
  display: none;
  font-family: var(--font-display);
  font-style: normal;
  font-size: 11px;
  font-weight: 700;
  color: #fff;
  line-height: 1;
  flex-shrink: 0;
  letter-spacing: 0.06em;
  background: var(--aurora);
  padding: 4px 8px;
  border-radius: 999px;
  box-shadow: 0 2px 8px -2px rgba(30, 64, 175, 0.35);
  align-self: flex-start;
  margin-top: 3px;
}

.q-text {
  flex: 1;
  margin: 0;
  font-family: var(--font-body);
  font-size: 17px;
  font-weight: 600;
  line-height: 1.5;
  color: var(--ink);
  letter-spacing: -0.005em;
  white-space: pre-wrap;
  word-break: break-word;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 4;
  overflow: hidden;
  cursor: default;
}

.q-body {
  position: relative;
  flex: 0 1 auto;
  min-width: 0;
  max-width: 76%;
  background: var(--bubble-user);
  border-radius: 16px 16px 6px 16px;
  padding: 12px 18px;
}

.q-attachments {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}

.q-att-item {
  display: inline-flex;
}

.q-att-img-wrap {
  display: inline-block;
  line-height: 0;
}

.q-att-img-wrap :deep(.n-image img),
.q-att-img {
  max-width: 180px;
  max-height: 120px;
  border-radius: 10px;
  border: 1px solid rgba(30, 64, 175, 0.1);
  object-fit: cover;
  cursor: zoom-in;
  transition: transform 0.18s, border-color 0.18s;
}

.q-att-img-wrap:hover :deep(.n-image img),
.q-att-img-wrap:hover .q-att-img {
  transform: scale(1.03);
  border-color: var(--accent);
}

.q-att-file {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px 4px 5px;
  background: rgba(255, 255, 255, 0.62);
  border: 1px solid rgba(30, 64, 175, 0.1);
  border-radius: 8px;
  font-size: 12px;
  text-decoration: none;
  color: inherit;
  transition: all 0.18s;
  font-family: inherit;
  cursor: pointer;
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.95), inset 0 0 0 1px rgba(255,255,255,0.4);
}

button.q-att-file {
  appearance: none;
}

.q-att-md {
  position: relative;
}

.q-att-md:hover {
  background: var(--accent-soft);
}

.q-att-md-hint {
  font-family: var(--font-body);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.02em;
  color: #1e40af;
  padding: 2px 7px;
  border: 1px solid rgba(30, 64, 175, 0.18);
  border-radius: 999px;
  margin-left: 2px;
  flex-shrink: 0;
}

.q-att-file:hover {
  border-color: var(--accent);
}

.q-att-name {
  color: var(--ink-2);
  font-weight: 500;
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.q-att-size {
  color: var(--ink-4);
  font-size: 10px;
  flex-shrink: 0;
}

/* Assistant response */
.assistant-response {
  padding-top: 22px;
  display: flex;
  flex-direction: column;
  gap: 22px;
  min-width: 0;
}


/* ─── LOADING ────────────────────────────────────────────────────── */
.loading-line {
  display: inline-flex;
  align-items: center;
  gap: 14px;
  padding: 4px 0;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--ink-3);
  letter-spacing: 0.05em;
}

.orbit-dual {
  width: 32px;
  height: 32px;
  position: relative;
  border-radius: 50%;
  border: 1px dashed rgba(58, 91, 217, 0.2);
  animation: orbit-spin 4s linear infinite;
  flex-shrink: 0;
}

.orbit-dual .star {
  position: absolute;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  top: -3.5px;
  left: 50%;
  margin-left: -3.5px;
  background: #3a5bd9;
  box-shadow: 0 0 6px rgba(58, 91, 217, 0.45);
}

.orbit-dual .star:nth-child(2) {
  top: auto;
  bottom: -3.5px;
  background: #d94f7a;
  box-shadow: 0 0 6px rgba(217, 79, 122, 0.45);
}

.orbit-dual .inner-ring {
  position: absolute;
  inset: 7px;
  border-radius: 50%;
  border: 1px dashed rgba(123, 94, 167, 0.2);
  animation: orbit-spin 3s linear infinite reverse;
}

.orbit-dual .inner-star {
  position: absolute;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  top: -2.5px;
  left: 50%;
  margin-left: -2.5px;
  background: #7b5ea7;
  box-shadow: 0 0 5px rgba(123, 94, 167, 0.45);
}

/* 切换会话加载占位：历史未返回时顶替首屏，避免闪现 welcome（颜色走主题变量，双主题自适应） */
.session-switching {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 14px;
  min-height: 45vh;
  color: var(--ink-3);
  font-family: var(--font-mono);
  font-size: 12px;
  letter-spacing: 0.08em;
}

.session-switching-dots {
  display: inline-flex;
  gap: 5px;
}

.session-switching-dots i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--ink-4);
  animation: session-switch-pulse 1s ease-in-out infinite;
}

.session-switching-dots i:nth-child(2) {
  animation-delay: 0.15s;
}

.session-switching-dots i:nth-child(3) {
  animation-delay: 0.3s;
}

@keyframes session-switch-pulse {
  0%, 100% {
    opacity: 0.35;
    transform: translateY(0);
  }
  50% {
    opacity: 1;
    transform: translateY(-3px);
  }
}

@media (prefers-reduced-motion: reduce) {
  .session-switching-dots i {
    animation: none;
  }
}

/* Error */
.error-line {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  background: rgba(254, 243, 242, 0.8);
  backdrop-filter: blur(20px);
  border: 1px solid rgba(220, 38, 38, 0.15);
  border-left: 3px solid #b91c1c;
  border-radius: 10px;
  font-size: 13px;
  color: #7f1d1d;
}

.error-tag {
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.06em;
  font-weight: 700;
  text-transform: uppercase;
  background: #b91c1c;
  color: #fff;
  padding: 2px 8px;
  border-radius: 999px;
}

/* 用户主动停止：中性提示，与 error-line 同构但不用红色 */
.stopped-line {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  background: rgba(241, 245, 249, 0.8);
  backdrop-filter: blur(20px);
  border: 1px solid rgba(100, 116, 139, 0.18);
  border-left: 3px solid #64748b;
  border-radius: 10px;
  font-size: 13px;
  color: #475569;
}

.stopped-tag {
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.06em;
  font-weight: 700;
  text-transform: uppercase;
  background: #64748b;
  color: #fff;
  padding: 2px 8px;
  border-radius: 999px;
}

/* ─── ANSWER ─────────────────────────────────────────────────────── */
.answer {
  display: flex;
  gap: 16px;
  padding-top: 4px;
}

/* A. 标记 → 样张同款小方块头像（玻璃=极光渐变 / kimi=墨黑） */
.answer-mark {
  font-family: var(--font-display);
  font-style: normal;
  font-size: 0;
  font-weight: 700;
  color: #fff;
  line-height: 1;
  flex-shrink: 0;
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--grad-brand);
  padding: 0;
  border-radius: 8px;
  box-shadow: var(--mark-shadow, none);
  align-self: flex-start;
  margin-top: 2px;
}

.answer-mark::after {
  content: '同';
  font-size: 13px;
  font-weight: 700;
  color: #fff;
}

@keyframes answer-cursor-blink {
  0%, 49% { opacity: 1; }
  50%, 100% { opacity: 0; }
}

.answer.streaming .answer-body :deep(p:last-child)::after,
.answer.streaming .answer-body > p:last-child::after,
.answer.streaming .answer-body.is-last-segment :deep(p:last-child)::after,
.answer.streaming .answer-body.is-last-segment > p:last-child::after {
  content: '\25AE';
  font-size: 0.85em;
  color: var(--accent);
  margin-left: 2px;
  display: inline;
  animation: answer-cursor-blink 0.85s step-end infinite;
}

.answer.streaming .answer-body :deep(pre code),
.answer.streaming .answer-body.is-last-segment :deep(pre code) {
  -webkit-text-fill-color: #e2e8f0;
}

.answer {
  position: relative;
}

.answer-export {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  background: rgba(255, 255, 255, 0.62);
  border: 1px solid rgba(30, 64, 175, 0.1);
  color: var(--ink-3);
  font-family: var(--font-body);
  font-size: 11px;
  letter-spacing: 0.005em;
  font-weight: 600;
  cursor: pointer;
  border-radius: 8px;
  opacity: 0;
  transition: opacity 0.18s, border-color 0.18s, color 0.18s, background 0.18s;
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.95), inset 0 0 0 1px rgba(255,255,255,0.4);
}

.answer-actions {
  position: absolute;
  top: 0;
  right: 0;
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.answer:hover .answer-export {
  opacity: 1;
}

.answer-export:hover {
  border-color: var(--accent);
  color: var(--accent);
}

.answer-truncate {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  border: 1px solid rgba(30, 64, 175, 0.12);
  background: rgba(255, 255, 255, 0.62);
  color: var(--ink-3);
  font-family: var(--font-body);
  font-size: 11px;
  letter-spacing: 0.005em;
  font-weight: 600;
  cursor: pointer;
  border-radius: 8px;
  opacity: 0;
  transition: opacity 0.18s, border-color 0.18s, color 0.18s, background 0.18s;
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.95), inset 0 0 0 1px rgba(255,255,255,0.4);
}

.answer:hover .answer-truncate {
  opacity: 1;
}

.answer-truncate:hover {
  border-color: #e5752a;
  color: #e5752a;
}

.answer-truncate--confirm {
  opacity: 1 !important;
  border-color: #c0392b;
  color: #c0392b;
  animation: truncate-pulse 0.4s ease;
}

.answer-truncate--confirm:hover {
  border-color: #c0392b;
  color: #c0392b;
  background: rgba(192, 57, 43, 0.06);
}

.answer-truncate:disabled {
  opacity: 0.5;
  cursor: wait;
}

/* 用户问题上的截断按钮：绝对定位悬浮在气泡左侧，不占用气泡（用户输入包裹框）内部空间；
   默认隐藏，hover user-question 时显示 */
.q-truncate {
  position: absolute;
  top: 12px;
  right: 100%;
  margin: 0 10px 0 0;
  opacity: 0;
  white-space: nowrap;
}

.user-question:hover .q-truncate,
.q-truncate.answer-truncate--confirm {
  opacity: 1;
}

@keyframes truncate-pulse {
  0% { transform: scale(1); }
  40% { transform: scale(1.06); }
  100% { transform: scale(1); }
}

.ae-icon {
  font-family: var(--font-display);
  font-size: 12px;
  line-height: 1;
}

.answer-body-segments {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 0;
}

.answer-body {
  flex: 1;
  min-width: 0;
  font-family: var(--font-body);
  font-size: 15px;
  line-height: 1.85;
  color: var(--ink);
}

/* Markdown */
.answer-body :deep(h1),
.answer-body :deep(h2),
.answer-body :deep(h3),
.answer-body :deep(h4) {
  font-family: var(--font-display);
  font-weight: 700;
  margin: 24px 0 10px;
  color: var(--ink);
  letter-spacing: -0.015em;
  line-height: 1.3;
}

.answer-body :deep(h1) {
  font-size: 24px;
  font-weight: 700;
}

.answer-body :deep(h2) {
  font-size: 19px;
}

.answer-body :deep(h3) {
  font-size: 16px;
}

.answer-body :deep(h4) {
  font-size: 14px;
  font-family: var(--font-mono);
  letter-spacing: 0.04em;
  color: var(--ink-2);
}

.answer-body :deep(p) {
  margin: 10px 0;
}

.answer-body :deep(strong) {
  color: var(--ink);
  font-weight: 600;
}

.answer-body :deep(em) {
  font-style: italic;
  color: #1e40af;
  font-family: var(--font-body);
}

.answer-body :deep(ul), .answer-body :deep(ol) {
  padding-left: 24px;
  margin: 12px 0;
}

.answer-body :deep(li) {
  margin: 5px 0;
}

.answer-body :deep(li::marker) {
  color: var(--accent);
  font-weight: 600;
}

.answer-body :deep(code) {
  font-family: var(--font-mono);
  font-size: 12.5px;
  background: var(--paper-deep);
  color: var(--accent-deep);
  padding: 2px 7px;
  border-radius: 6px;
  border: 1px solid var(--rule-soft);
}

.answer-body :deep(pre) {
  background: #0f172a;
  color: #e2e8f0;
  padding: 16px 20px;
  border-radius: 12px;
  margin: 14px 0;
  border: 1px solid var(--rule);
  position: relative;
  max-width: 100%;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
}

.answer-body :deep(pre)::before {
  content: '';
  display: none;
}

.answer-body :deep(pre code) {
  background: transparent;
  border: none;
  color: inherit;
  padding: 0;
  font-size: 12.5px;
  line-height: 1.7;
}

/* ───── Code block with copy button ───── */
.answer-body :deep(.code-block) {
  position: relative;
  margin: 14px 0;
  max-width: 100%;
  min-width: 0;
}

.answer-body :deep(.code-block) > pre {
  margin: 0;
  max-width: 100%;
  overflow: hidden;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
}

.answer-body :deep(.code-block) > pre code {
  display: block;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
}

.answer-body :deep(.code-block-lang) {
  position: absolute;
  top: 8px;
  left: 14px;
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: rgba(226, 232, 240, 0.45);
  pointer-events: none;
  z-index: 1;
}

.answer-body :deep(.code-copy-btn) {
  position: absolute;
  top: 6px;
  right: 8px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  font-family: var(--font-body);
  font-size: 11px;
  font-weight: 600;
  line-height: 1;
  color: rgba(226, 232, 240, 0.7);
  background: rgba(15, 23, 42, 0.6);
  border: 1px solid rgba(226, 232, 240, 0.18);
  border-radius: 8px;
  cursor: pointer;
  backdrop-filter: blur(8px);
  opacity: 0;
  transition: all 0.18s ease;
  z-index: 2;
}

.answer-body :deep(.code-block:hover .code-copy-btn),
.answer-body :deep(.code-copy-btn:focus-visible) {
  opacity: 1;
}

.answer-body :deep(.code-copy-btn:hover) {
  color: #fff;
  border-color: rgba(226, 232, 240, 0.4);
  background: rgba(15, 23, 42, 0.85);
}

.answer-body :deep(.code-copy-btn.is-done) {
  opacity: 1;
  color: #4ade80;
  border-color: rgba(74, 222, 128, 0.45);
}

.answer-body :deep(.code-copy-btn.is-error) {
  opacity: 1;
  color: #f87171;
  border-color: rgba(248, 113, 113, 0.45);
}

.answer-body :deep(.ccb-icon) {
  font-size: 12px;
  line-height: 1;
}

.answer-body :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 16px 0;
  font-size: 13px;
  font-family: var(--font-body);
  border-top: 1px solid rgba(30, 64, 175, 0.15);
  border-bottom: 1px solid rgba(30, 64, 175, 0.15);
  border-radius: 10px;
  overflow: hidden;
}

.answer-body :deep(thead) {
  background: rgba(30, 64, 175, 0.04);
}

.answer-body :deep(th) {
  padding: 11px 14px;
  text-align: left;
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  font-weight: 700;
  color: var(--ink);
  border-bottom: 1px solid rgba(30, 64, 175, 0.12);
}

.answer-body :deep(td) {
  padding: 9px 14px;
  border-bottom: 1px solid var(--rule-soft);
  vertical-align: top;
}

.answer-body :deep(tbody tr:hover td) {
  background: var(--paper-soft);
}

.answer-body :deep(blockquote) {
  margin: 16px 0;
  padding: 12px 22px;
  border-left: 3px solid var(--accent);
  background: rgba(30, 64, 175, 0.04);
  border-radius: 0 12px 12px 0;
  font-style: normal;
  font-family: var(--font-body);
  color: var(--ink-2);
  font-size: 14.5px;
}

.answer-body :deep(a) {
  color: var(--accent);
  text-decoration: underline;
  text-decoration-thickness: 1px;
  text-underline-offset: 3px;
  transition: color 0.15s;
}

.answer-body :deep(a:hover) {
  color: var(--accent-deep);
}

.answer-body :deep(hr) {
  border: none;
  border-top: 1px solid var(--rule);
  margin: 22px 0;
}

/* ─── COMPOSER ───────────────────────────────────────────────────── */
/* 镂空背景：与 QAComposer.vue 内样式同步，整条透明，页面背景直接透出 */
.qa-composer {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  border-top: none;
  padding: 14px 24px 16px;
  background: transparent;
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
}

/* 输入框高度随内容自动拉高（QAComposer autoGrow，上限约 56vh）——大框切换按钮已删除 */

/* 纵向 flex 列里 auto 外边距会关掉 stretch——显式满宽，max-width + margin:auto 负责 820 居中 */
.composer-toolbar,
.attached-bar,
.composer-frame,
.composer-foot {
  width: 100%;
}

.composer-frame {
  position: relative;
  display: flex;
  align-items: flex-end;
  gap: 12px;
  max-width: 820px;
  margin: 0 auto;
  border: 1px solid rgba(30, 64, 175, 0.12);
  background: rgba(255, 255, 255, 0.62);
  padding: 12px 16px;
  border-radius: 18px;
  /* 只过渡颜色类属性：尺寸切换必须瞬时，避免 flex 插值撕裂 */
  transition: border-color 0.2s ease, background-color 0.2s ease, box-shadow 0.2s ease;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.95),
    inset 0 0 0 1px rgba(255, 255, 255, 0.4),
    0 1px 2px rgba(15, 23, 42, 0.04);
}

.composer-frame.is-dragover {
  border-color: rgba(30, 64, 175, 0.3);
  background: rgba(30, 64, 175, 0.06);
}

.composer-frame:focus-within {
  border-color: rgba(30, 64, 175, 0.3);
  background: rgba(255, 255, 255, 0.85);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.95),
    0 0 0 3px rgba(30, 64, 175, 0.08),
    0 8px 24px -8px rgba(30, 64, 175, 0.2);
}

.composer-frame.is-running {
  border-color: rgba(30, 64, 175, 0.25);
  background: rgba(30, 64, 175, 0.04);
}

.composer-prompt {
  font-family: var(--font-display);
  font-size: 18px;
  color: #1e40af;
  line-height: 1.6;
  font-weight: 700;
  flex-shrink: 0;
}

.composer-input {
  flex: 1;
  resize: vertical;
  border: none;
  outline: none;
  background: transparent;
  font-family: var(--font-body);
  font-size: 15px;
  line-height: 1.6;
  color: var(--ink);
  min-height: 72px;
  max-height: 280px;
  font-feature-settings: 'ss01';
}

.composer-input::placeholder {
  color: var(--ink-4);
  font-style: normal;
  font-family: var(--font-body);
}

.composer-input:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.composer-side {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

.char-count {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--ink-4);
  letter-spacing: 0.1em;
}

.btn-send, .btn-stop {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 18px;
  border: none;
  cursor: pointer;
  font-family: var(--font-body);
  font-size: 12.5px;
  font-weight: 600;
  letter-spacing: 0.005em;
  border-radius: 11px;
  transition: all 0.2s ease;
  text-transform: none;
}

.btn-send {
  background: linear-gradient(110deg, #1e40af 0%, #2563eb 35%, #0ea5e9 70%, #0891b2 100%);
  color: #fff;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.4),
    0 4px 14px -2px rgba(30, 64, 175, 0.45);
}

.btn-send:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.4),
    0 8px 24px -4px rgba(30, 64, 175, 0.55);
}

.btn-send:disabled {
  background: rgba(30, 64, 175, 0.12);
  color: var(--ink-4);
  box-shadow: none;
  cursor: not-allowed;
}

.btn-arrow {
  font-family: var(--font-display);
  font-size: 16px;
  letter-spacing: 0;
  text-transform: none;
  transition: transform 0.2s;
}

.btn-send:hover:not(:disabled) .btn-arrow {
  transform: translateX(4px);
}

.btn-stop {
  background: rgba(255, 255, 255, 0.62);
  color: #1e40af;
  border: 1px solid rgba(30, 64, 175, 0.25);
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.95), inset 0 0 0 1px rgba(255,255,255,0.4);
}

.btn-stop:hover {
  background: #1e40af;
  color: #fff;
  border-color: transparent;
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.4), 0 4px 14px -2px rgba(30,64,175,0.45);
}

.composer-foot {
  max-width: 820px;
  margin: 10px auto 0;
  text-align: center;
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--ink-4);
  letter-spacing: 0.04em;
  font-weight: 500;
}

/* ─── COMPOSER · toolbar & mode switch ───────────────────────────── */
.composer-toolbar {
  max-width: 820px;
  margin: 0 auto 10px;
  display: flex;
  align-items: center;
  gap: 16px;
}

.mode-switch {
  display: inline-flex;
  border: 1px solid rgba(30, 64, 175, 0.1);
  background: rgba(255, 255, 255, 0.62);
  border-radius: 10px;
  flex-shrink: 0;
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.95), inset 0 0 0 1px rgba(255,255,255,0.4);
}

.mode-tab {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 6px 14px;
  background: transparent;
  border: none;
  border-right: 1px solid rgba(30, 64, 175, 0.06);
  font-family: var(--font-body);
  font-size: 12px;
  letter-spacing: 0.005em;
  font-weight: 600;
  color: var(--ink-3);
  cursor: pointer;
  transition: all 0.18s;
  border-radius: 10px;
  text-transform: none;
}

.mode-tab:last-child {
  border-right: none;
}

.mode-tab:hover:not(:disabled) {
  color: var(--ink);
  background: rgba(30, 64, 175, 0.04);
}

.mode-tab.active {
  background: linear-gradient(110deg, #1e40af 0%, #2563eb 35%, #0ea5e9 70%, #0891b2 100%);
  color: #fff;
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.4), 0 2px 8px -2px rgba(30,64,175,0.3);
}

.mode-tab.active .mode-dot {
  background: var(--gold);
}

.mode-tab:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.mode-dot {
  width: 5px;
  height: 5px;
  background: var(--ink-4);
  border-radius: 50%;
  transition: background 0.15s;
}

.mode-count {
  font-family: var(--font-mono);
  font-size: 9px;
  padding: 1px 6px;
  background: rgba(30, 64, 175, 0.08);
  color: #1e40af;
  border-radius: 999px;
  font-weight: 700;
  letter-spacing: 0.05em;
}

.mode-tab.active .mode-count {
  background: rgba(255, 255, 255, 0.2);
  color: #fff;
}

.toolbar-hint {
  flex: 1;
  font-family: var(--font-body);
  font-size: 11.5px;
  color: var(--ink-3);
  line-height: 1.5;
  font-style: italic;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.toolbar-hint code {
  font-family: var(--font-mono);
  font-style: normal;
  font-size: 10.5px;
  background: var(--accent-soft);
  color: var(--accent-deep);
  padding: 2px 6px;
  border: 1px solid var(--rule-soft);
  border-radius: 5px;
  margin: 0 2px;
}

/* ─── COMPOSER · input wrapper (for popup anchoring) ─────────────── */
.composer-input-wrap {
  position: relative;
  flex: 1;
  display: flex;
}

.composer-input-wrap .composer-input {
  flex: 1;
}

/* ─── SKILL POPUP ────────────────────────────────────────────────── */
.skill-popup {
  position: absolute;
  left: -4px;
  right: -4px;
  bottom: calc(100% + 10px);
  z-index: 20;
  background: rgba(255, 255, 255, 0.96);
  backdrop-filter: blur(40px) saturate(200%);
  -webkit-backdrop-filter: blur(40px) saturate(200%);
  border: 1px solid rgba(30, 64, 175, 0.12);
  border-radius: 16px;
  box-shadow:
    0 16px 48px -12px rgba(30, 64, 175, 0.25),
    0 4px 14px -6px rgba(15, 23, 42, 0.1);
  max-height: 320px;
  display: flex;
  flex-direction: column;
  animation: popupRise 0.22s cubic-bezier(0.22, 1, 0.36, 1);
  overflow: hidden;
}

@keyframes popupRise {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.skill-popup-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 14px;
  border-bottom: 1px solid var(--rule);
  background: var(--paper-deep);
}

.sp-icon {
  font-family: var(--font-display);
  font-style: normal;
  font-size: 15px;
  color: #1e40af;
  font-weight: 700;
}

.sp-label {
  font-family: var(--font-mono);
  font-size: 9.5px;
  font-weight: 700;
  letter-spacing: 0.06em;
  color: var(--ink-2);
  text-transform: uppercase;
}

.sp-line {
  flex: 1;
  height: 1px;
  background: linear-gradient(to right, var(--rule), transparent);
}

.sp-count {
  font-family: var(--font-mono);
  font-size: 9px;
  color: var(--ink-3);
  letter-spacing: 0.12em;
}

.skill-list {
  list-style: none;
  padding: 0;
  margin: 0;
  overflow-y: auto;
  max-height: 240px;
}

.skill-list::-webkit-scrollbar {
  width: 5px;
}

.skill-list::-webkit-scrollbar-thumb {
  background: var(--rule);
}

.skill-item {
  display: flex;
  align-items: baseline;
  gap: 14px;
  padding: 9px 14px;
  cursor: pointer;
  border-left: none;
  border-bottom: 1px solid rgba(30, 64, 175, 0.05);
  border-radius: 10px;
  margin: 0 4px;
  transition: background 0.15s;
}

.skill-item:last-child {
  border-bottom: none;
}

.skill-item.active,
.skill-item:hover {
  background: rgba(30, 64, 175, 0.06);
  border-left-color: transparent;
}

.sk-name {
  font-family: var(--font-display);
  font-size: 14.5px;
  font-weight: 600;
  color: var(--ink);
  letter-spacing: -0.005em;
  min-width: 130px;
  flex-shrink: 0;
}

.skill-item.active .sk-name,
.skill-item:hover .sk-name {
  color: #1e40af;
  font-style: normal;
}

.sk-desc {
  font-family: var(--font-body);
  font-size: 12px;
  color: var(--ink-3);
  line-height: 1.5;
  flex: 1;
}

.skill-popup-foot {
  display: flex;
  gap: 18px;
  padding: 7px 14px;
  border-top: 1px solid var(--rule);
  background: var(--paper-deep);
  font-family: var(--font-mono);
  font-size: 9px;
  color: var(--ink-3);
  letter-spacing: 0.14em;
  font-weight: 500;
  text-transform: uppercase;
}

/* ─── BATCH FRAME ────────────────────────────────────────────────── */
/* ─── BATCH CARD ─────────────────────────────────────────────────── */
/* ─── ANIMATIONS ─────────────────────────────────────────────────── */
@keyframes rise {
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes orbit-spin {
  to {
    transform: rotate(360deg);
  }
}

/* ─── RESPONSIVE ─────────────────────────────────────────────────── */

/* sidebar 遮罩（窄屏点击关闭） */
.qa-sidebar-mask {
  position: fixed;
  inset: 0;
  z-index: 9;
  background: rgba(15, 23, 42, 0.32);
  backdrop-filter: blur(2px);
  animation: rise 0.18s ease-out;
}

@media (max-width: 960px) {
  /* ─── 手机端：纯净白底 ───────────────────────────── */
  .qa-shell {
    --paper: #ffffff;
    --paper-deep: #ffffff;
    --paper-soft: #ffffff;
    --surface: #ffffff;
    --surface-strong: #ffffff;
    background: #ffffff;
  }

  /* 手机端按钮再做小一些、贴更近底部，让 composer 上方区域不被压住 */
  .qa-jumpdown {
    width: 34px;
    height: 34px;
    bottom: 12px;
  }
  .qa-jumpdown-arrow {
    font-size: 16px;
  }

  .qa-grain {
    display: none;
  }

  /* ─── 手机端整体圆角化 ───────────────────────────────────
     触屏上一律圆角化、留呼吸距离 */
  .qa-topbar .topbar-back,
  .qa-topbar .topbar-side-btn,
  .qa-topbar .topbar-distill,
  .qa-topbar .topbar-kb,
  .btn-attach,
  .btn-send,
  .btn-stop,
  .new-chat,
  .session-item,
  .attached-item,
  .af-progress-bar,
  .af-progress-fill,
  .af-ext,
  .q-att-file,
  .q-att-img-wrap :deep(.n-image img),
  .q-att-img,
  .skill-popup,
  .skill-item,
  .sk-badge,
  .distill-mask .distill-modal,
  .md-preview-mask .md-preview-modal,
  .md-preview-mask .file-preview-modal,
  .kb-toast,
  .answer-body :deep(pre),
  .answer-export,
  .answer-truncate,
  .composer-frame {
    border-radius: 12px;
  }

  /* 顶部小按钮用更圆润的胶囊 */
  .qa-topbar .topbar-back,
  .qa-topbar .topbar-distill,
  .qa-topbar .topbar-kb,
  .qa-topbar .topbar-side-btn {
    border-radius: 999px;
  }

  .qa-shell {
    /* 窄屏不再用 grid 留出 sidebar 列；sidebar 改为浮层 */
    grid-template-columns: 1fr;
  }

  .qa-shell.sidebar-collapsed {
    grid-template-columns: 1fr;
  }

  .qa-sidebar {
    position: fixed;
    top: 0;
    left: 0;
    height: 100%;
    width: min(86vw, 320px);
    z-index: 10;
    background: var(--paper-deep);
    box-shadow: 4px 0 30px rgba(0, 0, 0, 0.18);
    transform: translateX(-100%);
    transition: transform 0.32s cubic-bezier(0.22, 1, 0.36, 1), opacity 0.3s;
  }

  .qa-shell:not(.sidebar-collapsed) .qa-sidebar {
    transform: translateX(0);
    opacity: 1;
  }

  .qa-shell.sidebar-collapsed .qa-sidebar {
    transform: translateX(-100%);
    opacity: 0;
    pointer-events: none;
  }

  .sidebar-inner {
    min-width: 0;
    padding: 20px 16px 14px;
  }

  /* showcase 解锁 overflow 后，其 ambient 层（inset:-24px + 视差漂移）会溢进
     feed 推出横向滚动条；纵向信息流本就不该有横轴 */
  .qa-feed {
    overflow-x: hidden;
  }

  /* 小屏解除单屏锁定：整页可滚，卡片两列 */
  .qa-showcase {
    height: auto;
    min-height: 100%;
    overflow: visible;
    padding: 26px 18px 40px;
  }

  .codex-body {
    overflow: visible;
  }

  .codex-grid {
    margin: 0;
    grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  }

  /* 条目页：随单屏锁定解除，案例列表改为跟整页滚动 */
  .codex-entry {
    flex: none;
    max-width: none;
  }

  .codex-cases {
    overflow: visible;
    flex: none;
  }

  .codex-ghostno {
    font-size: 130px;
    top: -24px;
  }

  .codex-front-right {
    display: none;
  }

  .conversation {
    padding: 28px 36px 44px;
  }

  .exchange {
    margin-bottom: 48px;
  }

  /* 窄屏 answer 改纵向堆叠，续接段无需为头像预留缩进 */
  .exchange.continuation .assistant-response {
    padding-left: 0;
  }

  /* topbar 紧凑 */
  .qa-topbar {
    padding: 10px 14px;
    gap: 8px;
  }

  .topbar-back .tb-text,
  .topbar-distill .td-text {
    display: none;
  }

  /* 手机端：两个独立沉淀按钮合并成一个"沉淀"下拉 */
  .topbar-only-desktop {
    display: none;
  }

  .topbar-sediment-mobile {
    display: inline-block;
    margin-left: 0;
  }

  .topbar-back,
  .topbar-distill {
    padding: 6px 9px;
    margin-left: 0;
  }

  .topbar-meta {
    gap: 6px;
    font-size: 12px;
  }

  .meta-eyebrow {
    display: none;
  }

  .meta-divider {
    display: none;
  }

  .meta-title {
    font-size: 14px;
  }

  /* 用户问题 / 助手回复 */
  .user-question {
    gap: 10px;
    padding-bottom: 16px;
  }

  /* 注意：不要在这里重置 .answer-mark 的 font-size——
     它靠 font-size:0 隐藏字面文本「A.」、只留 ::after 方块「同」，
     覆盖会让手机屏同时露出「A.同」两层（旧文字徽章时代的遗留规则已删） */

  .q-text {
    font-size: 17px;
    line-height: 1.5;
    /* 触屏不可达 hover tooltip，长问题直接展开避免被截断且不可读 */
    -webkit-line-clamp: unset;
    display: block;
    overflow: visible;
  }

  .answer {
    gap: 10px;
  }

  .answer-body {
    font-size: 14.5px;
    line-height: 1.78;
  }

  .answer-body :deep(h1) {
    font-size: 21px;
  }

  .answer-body :deep(h2) {
    font-size: 17px;
  }

  .answer-body :deep(h3) {
    font-size: 15px;
  }

  .answer-body :deep(pre) {
    padding: 12px 14px;
    font-size: 12px;
  }

  .answer-body :deep(table) {
    display: block;
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
  }

  /* 手机端隐藏「导出 md」：手机上极少有人在浏览器手动管理 md 文件 */
  .answer-export,
  .answer-truncate {
    display: none;
  }

  .answer {
    flex-direction: column;
  }

  .answer-mark {
    line-height: 1;
  }

  /* composer 整体 */
  .qa-composer {
    padding: 10px 12px max(12px, env(safe-area-inset-bottom));
  }

  .composer-toolbar {
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 8px;
  }

  .toolbar-hint {
    flex-basis: 100%;
    order: 3;
    white-space: normal;
    font-size: 11px;
    line-height: 1.45;
  }

  .composer-frame {
    flex-wrap: nowrap;
    align-items: flex-end;
    padding: 8px 10px 8px 10px;
    gap: 8px;
    border-radius: 18px;
  }

  .composer-prompt {
    display: none;
  }

  .btn-attach {
    margin-right: 0;
    width: 36px;
    height: 36px;
    border-radius: 12px;
    flex-shrink: 0;
    align-self: flex-end;
  }

  .composer-input-wrap {
    flex: 1 1 auto;
    min-width: 0;
    order: 0;
  }

  .composer-input {
    font-size: 16px; /* iOS 防自动放大：>=16px */
    min-height: 36px;
    max-height: none;
    padding: 6px 6px;
    line-height: 1.5;
    resize: none;
    overflow-y: hidden;
  }

  .composer-side {
    flex: 0 0 auto;
    justify-content: flex-end;
    order: 0;
    gap: 6px;
    align-self: flex-end;
  }

  /* 字符计数让位，免得挤 */
  .composer-side .char-count {
    display: none;
  }

  .btn-send, .btn-stop {
    padding: 0 16px;
    font-size: 12px;
    height: 36px;
    min-height: 36px;
    border-radius: 999px;
  }

  /* 附件条 */
  .attached-bar {
    margin: 0 0 6px;
  }

  .attached-item {
    max-width: 100%;
  }

  .af-name {
    max-width: 110px;
  }

  .af-remove {
    opacity: 1; /* 移动端无 hover，常驻显示 */
  }

  .session-del {
    opacity: 0.55; /* 同上 */
  }

  /* skill popup */
  .skill-popup {
    max-height: 50vh;
    left: -10px;
    right: -10px;
  }

  .skill-list {
    max-height: calc(50vh - 80px);
  }

  .skill-item {
    flex-wrap: wrap;
    gap: 4px 12px;
  }

  .sk-name {
    min-width: 0;
    font-size: 14px;
  }

  .sk-desc {
    flex-basis: 100%;
    font-size: 11.5px;
  }

  .skill-popup-foot {
    gap: 12px;
    font-size: 8.5px;
  }

  /* distill modal */
  .distill-modal {
    width: calc(100vw - 24px);
    max-height: calc(100vh - 48px);
    overflow-y: auto;
  }

  .dm-body {
    padding: 14px 14px;
    gap: 10px;
  }

  .dm-row {
    flex-direction: column;
    gap: 4px;
    align-items: stretch;
  }

  .dm-label {
    min-width: 0;
  }

  .dm-prompt {
    font-size: 11.5px;
    max-height: 220px;
  }

  /* 历史会话项点击区更大 */
  .session-item {
    padding: 11px 10px;
  }

  /* 隐藏右边问题 minimap：屏宽紧张 */
  .qa-track {
    display: none;
  }
}

@media (max-width: 480px) {
  .qa-topbar {
    padding: 8px 10px;
  }

  .topbar-back,
  .topbar-distill {
    padding: 6px 8px;
  }

  /* 极窄屏：知识库 + 沉淀到库 都退化成纯图标按钮，给会话标题留呼吸空间 */
  .topbar-kb .td-text,
  .topbar-distill .td-text {
    display: none;
  }

  .topbar-kb {
    padding: 6px 9px;
    margin-left: 4px;
    gap: 5px;
  }

  /* 标题在极窄屏下溢出隐藏，不再换行挤压按钮 */
  .topbar-meta {
    flex: 1;
    min-width: 0;
    overflow: hidden;
  }

  .meta-title {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .conversation {
    padding: 22px 34px 32px;
  }

  .qa-showcase {
    padding: 22px 14px 32px;
  }

  .codex-grid {
    grid-template-columns: 1fr;
  }

  /* 超窄屏卷首压缩成"紧凑封面"：
     图标牌与内边距收小，章节索引间距同步收紧，解开页首的拥挤感 */
  .codex-front {
    margin-bottom: 10px;
    padding: 11px 12px 12px;
  }

  .codex-front-left {
    gap: 10px;
  }

  .codex-mark {
    width: 30px;
    height: 30px;
    font-size: 15px;
  }

  .codex-title {
    font-size: 20px;
  }

  .codex-chapters {
    margin-bottom: 10px;
  }

  .codex-entry-name {
    font-size: 19px;
  }

  .codex-ghostno {
    font-size: 100px;
  }

  .composer-foot {
    font-size: 8px;
    letter-spacing: 0.18em;
  }
}

/* ─── 内嵌模式（workflow 浮窗等）：容器恒窄（≤440px），与视口宽度无关，
   视口断点在大屏桌面上不会触发，需按类名单独落一套"窄容器"布局 ─── */

/* 背景与板页（wf-page）对齐：
   - 不透明纸色底打透明，让浮窗自带的磨砂白底（rgba(255,255,255,.82)+blur）透上来，
     玻璃语言与板面其他浮层同源；
   - 3px 细噪点换成板页同款 22px 蓝调点阵；
   - 三颗大光球（680~720px / opacity .3+ / 无限动画）在 440px 窄窗里糊成满屏浓蓝，
     按板页 wf-aurora 的档位缩尺寸、静止、透明度压到 .09~.14——同一股氛围，只是小一号 */
.qa-shell.is-embedded {
  background: transparent;
}

.qa-shell.is-embedded::before {
  background-image: radial-gradient(rgba(30, 64, 175, 0.05) 1px, transparent 1px);
  background-size: 22px 22px;
  opacity: 1;
  mix-blend-mode: normal;
}

.qa-shell.is-embedded::after {
  width: 260px;
  height: 260px;
  top: 30%;
  right: -90px;
  opacity: 0.09;
  animation: none;
}

.qa-shell.is-embedded .qa-grain::before {
  width: 320px;
  height: 320px;
  top: -120px;
  right: -90px;
  opacity: 0.14;
  animation: none;
}

.qa-shell.is-embedded .qa-grain::after {
  width: 340px;
  height: 340px;
  bottom: -140px;
  left: -110px;
  opacity: 0.13;
  animation: none;
}

.qa-shell.is-embedded .qa-feed {
  /* showcase 解除 overflow:hidden 后，其 ambient 层（inset:-24px + 视差漂移）
     会溢进 feed 推出横向滚动条；纵向信息流本就不该有横轴 */
  overflow-x: hidden;
}

.qa-shell.is-embedded .qa-showcase {
  /* 解除单屏锁定：浮窗里改由 .qa-feed 整页滚动，卡片纵向流排 */
  height: auto;
  min-height: 100%;
  overflow: visible;
  padding: 20px 16px 14px;
}

.qa-shell.is-embedded .codex-body {
  overflow: visible;
}

.qa-shell.is-embedded .codex-grid {
  margin: 0;
  grid-template-columns: 1fr;
  gap: 12px;
}

.qa-shell.is-embedded .codex-entry {
  flex: none;
  max-width: none;
}

.qa-shell.is-embedded .codex-cases {
  overflow: visible;
  flex: none;
}

/* 卷首压缩成"紧凑封面"：右侧功能控制按钮整块收起（440px 容不下桌面横排），
   图标牌与标题收小 */
.qa-shell.is-embedded .codex-front {
  margin-bottom: 10px;
  padding: 11px 12px;
}

.qa-shell.is-embedded .codex-front-left {
  gap: 10px;
}

.qa-shell.is-embedded .codex-front-right {
  display: none;
}

.qa-shell.is-embedded .codex-mark {
  width: 30px;
  height: 30px;
  font-size: 15px;
}

.qa-shell.is-embedded .codex-title {
  font-size: 19px;
}

.qa-shell.is-embedded .codex-ghostno {
  font-size: 110px;
  top: -18px;
}

/* 章节索引与案例卡走窄屏形态 */
.qa-shell.is-embedded .codex-chapters {
  margin-bottom: 10px;
}

.qa-shell.is-embedded .codex-chapter {
  padding: 4px 9px 6px;
  gap: 6px;
}

.qa-shell.is-embedded .codex-chapter-name {
  font-size: 13px;
}

.qa-shell.is-embedded .codex-case {
  flex-direction: column;
}

.qa-shell.is-embedded .codex-case-media {
  width: 100%;
  flex-basis: auto;
  aspect-ratio: 16 / 9;
}

.qa-shell.is-embedded .codex-case:has(.codex-case-media) .codex-case-foot {
  border-left: none;
  border-top: 1px solid color-mix(in srgb, var(--ca) 16%, var(--rule));
}

/* 会话区收窄：48px 侧边距在 440px 浮窗里会吃掉大半内容宽，
   两侧对称收到 16px（Q./A. 圆徽在正文列内排版，无需左侧留白槽） */
.qa-shell.is-embedded .conversation {
  padding: 22px 16px 28px;
}

/* 窄窗问答布局：与手机端（≤960px）完全一致——Q. 圆徽与问题文字
   同行居左，A. 圆徽独占回答正文上方一行（flex 纵排）。
   ⚠ A. 行别改回 flex-row + wrap：正文 flex:1（basis 0%）与 actions
   （basis 100%）合计恰好"装得下"一行，flex 不换行，正文塌成 0 宽
   每行一字的竖排 */
.qa-shell.is-embedded .answer {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

/* 圆徽尺寸沿用基类 11px（与嵌入态 15px 正文同比例）——手机端 19px 是
   为大字号触屏配的，嵌进来会比正文还大，只借布局不借字号 */
.qa-shell.is-embedded .user-question {
  gap: 10px;
  padding-bottom: 16px;
}

/* "导出 md" 原绝对定位贴在正文右上角，窄窗里与首行文字重叠放大"右边挤"；
   改挂正文下方右对齐，平时 0 高度不占位，hover 时随按钮一起展开 */
.qa-shell.is-embedded .answer-actions {
  position: static;
  display: flex;
  justify-content: flex-end;
  height: 0;
  overflow: hidden;
  transition: height 0.18s ease;
}

.qa-shell.is-embedded .answer:hover .answer-actions {
  height: 28px;
}

/* ─── 嵌入态输入区收紧（子组件内部，:deep 穿透）───────────────────── */

/* 核心修复：.composer-input-wrap 只有 flex:1 没有 min-width:0，flex 项的
   auto 最小宽 = textarea 的 min-content，是一根撑不缩的刚性柱；字数统计
   一出现，flex-shrink:0 的 .composer-side 合计超宽，发送按钮被推出框外。
   wrap 与 textarea 同步补 min-width:0，让输入区回到弹性角色，计数、按钮
   都留在框内 */
.qa-shell.is-embedded :deep(.composer-input-wrap),
.qa-shell.is-embedded :deep(.composer-input) {
  min-width: 0;
}

/* 24px 侧 padding / 框内 12px 间距 / 72px 起步输入高都是 820px 桌面栏的
   比例；440px 浮窗里整体缩一档，把高度让给消息流。字数统计保留——它是
   输入反馈，修好弹性后不再挤走按钮 */
.qa-shell.is-embedded .qa-composer {
  padding: 10px 12px 12px;
}

.qa-shell.is-embedded :deep(.composer-toolbar) {
  gap: 8px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}

.qa-shell.is-embedded :deep(.composer-frame) {
  padding: 10px 12px;
  gap: 8px;
}

.qa-shell.is-embedded :deep(.btn-attach) {
  width: 28px;
  height: 28px;
}

.qa-shell.is-embedded :deep(.composer-prompt) {
  font-size: 16px;
}

.qa-shell.is-embedded :deep(.composer-input) {
  min-height: 56px;
}

.qa-shell.is-embedded :deep(.composer-side) {
  gap: 6px;
}

/* @ 技能弹层窄窗适配：字号间距收一档，条目为「图标 + 竖排两行」形态（名字/介绍各自截断） */
.qa-shell.is-embedded :deep(.skill-item) {
  gap: 8px;
  padding: 7px 10px;
}

.qa-shell.is-embedded :deep(.sk-icon) {
  width: 22px;
  height: 22px;
  border-radius: 7px;
}

.qa-shell.is-embedded :deep(.sk-name) {
  font-size: 13px;
}

.qa-shell.is-embedded :deep(.sk-desc) {
  font-size: 11.5px;
}

/* 窄窗里模式钮缩一档：字号间距收紧，保留 pill 形态（原 btn-expand 尺寸的接替者） */
.qa-shell.is-embedded :deep(.btn-mode) {
  height: 26px;
  padding: 0 8px;
  font-size: 12px;
  gap: 4px;
}

.qa-shell.is-embedded :deep(.btn-send),
.qa-shell.is-embedded :deep(.btn-stop) {
  padding: 7px 14px;
  gap: 6px;
}

/* 免责 + 备案行窄窗缩字，避免换行占高 */
.qa-shell.is-embedded :deep(.composer-foot) {
  margin-top: 8px;
  font-size: 9px;
}

/* 自动拉高上限 56vh 按视口算，浮窗里会一口吞掉整个窗口（浮窗本身才 660px）；
   窄窗形态改按固定上限收口，把高度让给消息流 */
.qa-shell.is-embedded :deep(.composer-input) {
  max-height: 200px;
}

/* ─── 拖拽上传覆盖层 ─────────────────────────────────────────── */
.drag-overlay {
  position: absolute;
  inset: 0;
  z-index: 50;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  background: rgba(30, 64, 175, 0.08);
  border-radius: 18px;
  pointer-events: none;
  color: #1e40af;
  font-size: 14px;
  font-weight: 600;
}

.drag-icon {
  font-size: 20px;
}

/* ─── 附件按钮 ────────────────────────────────────────────────── */
.btn-attach {
  flex-shrink: 0;
  width: 34px;
  height: 34px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(255, 255, 255, 0.62);
  border: 1px solid rgba(30, 64, 175, 0.1);
  border-radius: 10px;
  cursor: pointer;
  transition: all 0.2s ease;
  color: var(--ink-3);
  margin-right: 8px;
  align-self: flex-end; /* 大框模式下输入区拉高，附件按钮仍留在底边，不飘到左上角 */
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.95), inset 0 0 0 1px rgba(255,255,255,0.4);
}

.btn-attach:hover:not(:disabled) {
  border-color: var(--accent, #1e40af);
  color: var(--accent, #1e40af);
  transform: translateY(-1px);
}

.btn-attach:hover:not(:disabled) .attach-clip {
  transform: rotate(-14deg);
}

.btn-attach:active:not(:disabled) {
  transform: translateY(0) scale(0.92);
  transition-duration: 0.08s;
}

.btn-attach:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.attach-clip {
  display: block;
  transition: transform 0.25s cubic-bezier(0.34, 1.56, 0.64, 1);
  transform-origin: 50% 50%;
}

/* ─── 附件条 ──────────────────────────────────────────────────── */
.attached-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  max-width: 820px;
  margin: 0 auto 6px;
  padding: 0 2px;
}

.attached-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 8px 3px 4px;
  background: rgba(255, 255, 255, 0.62);
  border: 1px solid rgba(30, 64, 175, 0.1);
  border-radius: 8px;
  font-size: 11px;
  line-height: 1.3;
  max-width: 240px;
  transition: border-color 0.18s;
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.95), inset 0 0 0 1px rgba(255,255,255,0.4);
}

.attached-item:hover {
  border-color: var(--ink-4, #94a3b8);
}

.attached-item.has-error {
  border-color: #fca5a5;
  background: #fef2f2;
}

.af-ext {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 28px;
  height: 18px;
  padding: 0 4px;
  border-radius: 3px;
  font-size: 9px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.02em;
  color: #fff;
  background: #94a3b8;
  flex-shrink: 0;
}

.af-ext-pdf {
  background: #dc2626;
}

.af-ext-doc {
  background: #2563eb;
}

.af-ext-sheet {
  background: #16a34a;
}

.af-ext-ppt {
  background: #ea580c;
}

.af-ext-img {
  background: #7c3aed;
}

.af-ext-zip {
  background: #854d0e;
}

.af-ext-media {
  background: #0891b2;
}

.af-ext-code {
  background: #475569;
}

.af-ext-text {
  background: #64748b;
}

.af-ext-other {
  background: #94a3b8;
}

.af-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--ink-2, #334155);
  font-weight: 500;
  max-width: 130px;
}

.af-size {
  color: var(--ink-4, #94a3b8);
  flex-shrink: 0;
  font-size: 10px;
}

.af-progress-bar {
  width: 32px;
  height: 3px;
  background: var(--rule, #e2e8f0);
  border-radius: 2px;
  overflow: hidden;
  flex-shrink: 0;
}

.af-progress-fill {
  display: block;
  height: 100%;
  background: var(--accent, #1e40af);
  border-radius: 2px;
  transition: width 0.15s;
}

.af-error {
  color: #dc2626;
  font-weight: 600;
  font-size: 10px;
}

.af-remove {
  background: none;
  border: none;
  font-size: 13px;
  line-height: 1;
  color: var(--ink-4, #94a3b8);
  cursor: pointer;
  padding: 0 1px;
  flex-shrink: 0;
  opacity: 0;
  transition: opacity 0.15s, color 0.15s;
}

.attached-item:hover .af-remove {
  opacity: 1;
}

.af-remove:hover {
  color: #dc2626;
}

/* ─── 个人知识库：topbar 入口 + 沉淀 toast ─────────────────────────── */
.topbar-kb {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 6px 12px 6px 11px;
  margin-left: 8px;
  background: linear-gradient(110deg, rgba(30, 64, 175, 0.08) 0%, rgba(37, 99, 235, 0.07) 35%, rgba(14, 165, 233, 0.07) 70%, rgba(8, 145, 178, 0.08) 100%);
  border: 1px solid rgba(30, 64, 175, 0.22);
  color: #1e40af;
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.005em;
  font-weight: 700;
  cursor: pointer;
  border-radius: 10px;
  position: relative;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.6),
    0 1px 2px rgba(30, 64, 175, 0.06);
  transition: border-color 0.18s ease, color 0.18s ease, background 0.18s ease, box-shadow 0.2s ease;
}

.topbar-kb:hover {
  border-color: rgba(30, 64, 175, 0.42);
  color: #1e3a8a;
  background: linear-gradient(110deg, rgba(30, 64, 175, 0.14) 0%, rgba(37, 99, 235, 0.12) 35%, rgba(14, 165, 233, 0.12) 70%, rgba(8, 145, 178, 0.14) 100%);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.65),
    0 4px 12px -4px rgba(30, 64, 175, 0.28);
}

.topbar-kb .td-text-nian {
  font-family: var(--font-display);
  font-size: 15px;
  font-style: normal;
  font-weight: 600;
  line-height: 1;
  letter-spacing: 0;
  text-transform: none;
  background: linear-gradient(110deg, #1e40af 0%, #0891b2 100%);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}

.topbar-kb-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 16px;
  padding: 0 5px;
  background: linear-gradient(110deg, #1e40af 0%, #0891b2 100%);
  color: var(--paper);
  font-family: var(--font-mono);
  font-size: 9.5px;
  font-weight: 700;
  letter-spacing: 0.04em;
  border-radius: 999px;
  margin-left: 2px;
  box-shadow: 0 1px 4px -1px rgba(30, 64, 175, 0.35);
}

.kb-toast {
  position: fixed;
  top: 18px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 100;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 18px 10px 14px;
  background: rgba(255, 255, 255, 0.96);
  backdrop-filter: blur(40px) saturate(200%);
  border: 1px solid rgba(30, 64, 175, 0.12);
  border-left: 3px solid var(--accent);
  box-shadow: 0 16px 40px -8px rgba(15, 23, 42, 0.18);
  font-family: var(--font-body);
  font-size: 13px;
  color: var(--ink);
  border-radius: 12px;
  max-width: 80vw;
}

.kb-toast-ok {
  border-left-color: #16a34a;
}

.kb-toast-err {
  border-left-color: #dc2626;
}

.kb-toast-mark {
  font-family: var(--font-display);
  font-style: normal;
  font-size: 14px;
  color: #fff;
  font-weight: 700;
  background: var(--aurora);
  width: 24px;
  height: 24px;
  border-radius: 7px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  box-shadow: 0 2px 8px -2px rgba(30, 64, 175, 0.4);
}

.kb-toast-msg {
  letter-spacing: 0.01em;
}

.kb-toast-fade-enter-active,
.kb-toast-fade-leave-active {
  transition: opacity 0.2s, transform 0.2s;
}

.kb-toast-fade-enter-from,
.kb-toast-fade-leave-to {
  opacity: 0;
  transform: translate(-50%, -8px);
}

/* ─── IMAGE LIGHTBOX（案例大图等） ─────────────────────────────── */
.img-lightbox-mask {
  position: fixed;
  inset: 0;
  z-index: 240;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.82);
  cursor: zoom-out;
}

.img-lightbox-img {
  max-width: calc(100vw - 48px);
  max-height: calc(100vh - 96px);
  object-fit: contain;
  border-radius: 8px;
  box-shadow: 0 12px 48px rgba(0, 0, 0, 0.5);
  cursor: default;
}

.img-lightbox-name {
  position: fixed;
  bottom: 24px;
  left: 50%;
  transform: translateX(-50%);
  max-width: calc(100vw - 120px);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.72);
}

.img-lightbox-close {
  position: fixed;
  top: 20px;
  right: 24px;
  width: 36px;
  height: 36px;
  border: none;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.12);
  color: rgba(255, 255, 255, 0.85);
  font-size: 16px;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}

.img-lightbox-close:hover { background: rgba(0, 0, 0, 0.7); color: #fff; }
</style>

<style>
.q-tooltip-popover {
  font-family: 'Plus Jakarta Sans', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif !important;
  scrollbar-width: thin;
  scrollbar-color: rgba(30, 64, 175, 0.18) transparent;
}

.q-tooltip-popover::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}

.q-tooltip-popover::-webkit-scrollbar-track {
  background: transparent;
}

.q-tooltip-popover::-webkit-scrollbar-thumb {
  background: rgba(30, 64, 175, 0.18);
  border-radius: 3px;
}

.q-tooltip-popover::-webkit-scrollbar-thumb:hover {
  background: rgba(30, 64, 175, 0.32);
}

/* Artifact 加载占位符样式见文件末尾全局 style 块 */
</style>

<style>
/* ─────────────────────────────────────────────────────────────
   Artifact 分类型加载骨架（v-html 注入 + artifact-list 模板共用，必须全局）
   ──────────────────────────────────────────────────────────── */

/* 闪光基元 */
.als-bone {
  background: #ebebf0;
  border-radius: 4px;
  position: relative;
  overflow: hidden;
}
.als-bone::after {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(105deg, transparent 20%, rgba(255,255,255,.65) 50%, transparent 80%);
  animation: als-sweep 1.8s ease-in-out infinite;
}
.als-bone:nth-child(2)::after { animation-delay: .1s; }
.als-bone:nth-child(3)::after { animation-delay: .2s; }
.als-bone:nth-child(4)::after { animation-delay: .3s; }
.als-bone:nth-child(5)::after { animation-delay: .15s; }
.als-bone:nth-child(6)::after { animation-delay: .25s; }
@keyframes als-sweep {
  0%   { transform: translateX(-130%) skewX(-8deg); }
  100% { transform: translateX(130%)  skewX(-8deg); }
}

/* ── chart：无边框透明背景（inline 卡片去掉了 border/bg） ── */
.als-chart { padding: 4px 0 8px; margin: 4px 0; }
.als-chart .als-bars { display: flex; align-items: flex-end; gap: 7px; height: 88px; }
.als-chart .als-bar  { flex: 1; border-radius: 3px 3px 1px 1px; }
.als-chart .b1 { height: 52%; } .als-chart .b2 { height: 78%; }
.als-chart .b3 { height: 43%; } .als-chart .b4 { height: 88%; }
.als-chart .b5 { height: 62%; } .als-chart .b6 { height: 35%; }
.als-chart .als-axis { height: 1px; background: #e2e8f0; margin: 5px 0 0; }

/* ── html：圆角边框（与 .artifact-html inline 样式对齐） ── */
.als-html {
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  overflow: hidden;
  background: #fff;
  margin: 4px 0;
}
/* page 内容区：.als-html 和 .als-html-body 两种上下文复用 */
.als-html .als-page,
.als-html-body .als-page    { padding: 12px 14px; display: flex; flex-direction: column; gap: 9px; background: #fafafa; }
.als-html .als-nav,
.als-html-body .als-nav     { height: 18px; width: 100%; border-radius: 5px; }
.als-html .als-grid,
.als-html-body .als-grid    { display: grid; grid-template-columns: 1.5fr 1fr; gap: 9px; }
.als-html .als-col,
.als-html-body .als-col     { display: flex; flex-direction: column; gap: 6px; }
.als-html .als-line,
.als-html-body .als-line    { height: 7px; border-radius: 3px; }
.als-html .lf, .als-html-body .lf { width: 100%; }
.als-html .l8, .als-html-body .l8 { width: 85%; }
.als-html .l6, .als-html-body .l6 { width: 65%; }
.als-html .als-side,
.als-html-body .als-side    { border-radius: 6px; min-height: 65px; }
.als-html .als-foot,
.als-html-body .als-foot    { height: 13px; width: 34%; border-radius: 4px; margin: 0 auto; }

/* ── file：compact 行（与 .artifact-file-inline 对齐） ── */
.als-file {
  display: flex; align-items: center; gap: 6px;
  padding: 4px 8px 4px 5px;
  border: 1px solid #e2e8f0;
  border-radius: 3px;
  background: transparent;
  margin: 4px 0;
}
.als-file .als-f-ext  { width: 28px; height: 18px; border-radius: 2px; flex-shrink: 0; }
.als-file .als-f-name { height: 9px; flex: 1; max-width: 140px; border-radius: 3px; }
.als-file .als-f-act  { height: 10px; width: 40px; border-radius: 2px; margin-left: auto; flex-shrink: 0; }

@media (prefers-reduced-motion: reduce) {
  .als-bone::after { animation: none; opacity: 0; }
}

/* ─── 标准编号链接 ─────────────────────────────────────────── */
.std-no-link {
  border-bottom: 1px dashed #999;
  cursor: default;
  transition: color 0.2s, border-color 0.2s, background 0.2s;
}
.std-no-active {
  color: #2563eb;
  border-bottom: 1px solid #2563eb;
  cursor: pointer;
}
.std-no-active:hover {
  background: #eff6ff;
  border-radius: 2px;
}

/* ════════════════════════════════════════════════════════════════
   Kimi 主题（2026-08 新增，设计参照 www.kimi.com 实测截图）
   准则：中性灰白双色底 / 墨黑主导 / 全页唯一浅蓝 / 无玻璃无光晕 /
         阴影极弱靠底色分层 / 首屏 wordmark + 大输入框主角
   ════════════════════════════════════════════════════════════════ */

/* ── 变量层 ── */
:root[data-qa-theme='ink'] .qa-shell {
  --paper: #fbfbfc;
  --paper-deep: #f1f1f3;
  --paper-soft: #ffffff;
  --surface: #ffffff;
  --surface-strong: #ffffff;
  --surface-deep: #ffffff;
  --highlight: #ffffff;

  --ink: #17181a;
  --ink-2: #494a50;
  --ink-3: #7e7f86;
  --ink-4: #b9bac0;

  --rule: rgba(31, 32, 36, 0.07);
  --rule-soft: rgba(31, 32, 36, 0.05);
  --border: rgba(31, 32, 36, 0.08);
  --border-strong: rgba(31, 32, 36, 0.15);
  --border-glow: rgba(31, 32, 36, 0.2);

  /* 墨黑主导：accent/aurora 全部塌成黑灰阶 */
  --accent: #17181a;
  --accent-soft: rgba(23, 24, 26, 0.05);
  --accent-deep: #000000;
  --gold: #494a50;
  --c-blue: #17181a;
  --c-blue-2: #2e2e33;
  --c-sky: #494a50;
  --c-cyan: #494a50;
  --c-violet: #494a50;
  --c-mint: #494a50;
  --aurora: #131316;

  --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.04);
  --shadow-md: 0 1px 2px rgba(0, 0, 0, 0.03), 0 10px 30px -14px rgba(0, 0, 0, 0.1);
  --shadow-lg: 0 2px 8px rgba(0, 0, 0, 0.05), 0 20px 50px -20px rgba(0, 0, 0, 0.18);
  --shadow-glow: 0 10px 30px -12px rgba(0, 0, 0, 0.25);

  --font-display: -apple-system, 'PingFang SC', 'HarmonyOS Sans SC', 'Microsoft YaHei', system-ui, sans-serif;
  --font-body: -apple-system, 'PingFang SC', 'HarmonyOS Sans SC', 'Microsoft YaHei', system-ui, sans-serif;

  /* kimi 专属：全页唯一彩色 + 灰带 */
  --kimi-blue: #3d7bf6;
  --kimi-blue-bg: #e7effd;
  --kimi-fill: #efeff1;

  /* ─── 语义令牌覆盖 ─── */
  --grad-brand: #131316;
  --on-primary: #ffffff;
  --newchat-bg: #ffffff;
  --newchat-ink: #17181a;
  --card-bg: #ffffff;
  --card-border: rgba(31, 32, 36, 0.07);
  --card-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
  --input-shadow: 0 1px 2px rgba(0, 0, 0, 0.03), 0 10px 30px -14px rgba(0, 0, 0, 0.1);
  --r-newchat: 14px;
  --side-bg: #f7f7f8;
  --side-border: rgba(31, 32, 36, 0.07);
  --side-blur: none;
  --side-shadow: none;
  --head-rule: transparent;
  --fill-hover: rgba(23, 24, 26, 0.045);
  --fill-2: #efeff1;
  --line-hair: rgba(31, 32, 36, 0.07);
  --blue: #3d7bf6;
  --blue-bg: #e7effd;
  --send-off: #d6d6da;
  --kbd-bg: #f1f1f3;
  --kbd-ink: #7e7f86;
  --active-bg: #efeff1;
  --active-shadow: none;
  --active-ink: #17181a;
  --bubble-user: #f0f1f3;
  --ambient: none;
  --mark-shadow: none;
  --popup-bg: #ffffff;
  --popup-blur: none;
  --popup-border: rgba(31, 32, 36, 0.08);
  --popup-shadow: 0 2px 8px rgba(0, 0, 0, 0.05), 0 16px 48px -12px rgba(0, 0, 0, 0.18);
}

/* ── 去氛围层：光球 / 点阵全关 ── */
:root[data-qa-theme='ink'] .qa-grain::before,
:root[data-qa-theme='ink'] .qa-grain::after,
:root[data-qa-theme='ink'] .qa-shell::before,
:root[data-qa-theme='ink'] .qa-shell::after {
  display: none;
}

/* ── 顶栏近乎隐形 ── */
:root[data-qa-theme='ink'] .qa-topbar {
  background: transparent;
  border-bottom-color: rgba(31, 32, 36, 0.07);
}

/* ── 简报面板中性化 ── */
:root[data-qa-theme='ink'] .qa-brief {
  background: #ffffff;
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
  border-color: rgba(31, 32, 36, 0.08);
  box-shadow:
    0 1px 2px rgba(0, 0, 0, 0.03),
    0 12px 32px -16px rgba(0, 0, 0, 0.12);
}

:root[data-qa-theme='ink'] .brief-close-btn {
  background: #ffffff;
  border-color: rgba(31, 32, 36, 0.1);
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
}

:root[data-qa-theme='ink'] .brief-close-btn:hover {
  background: #f7f7f8;
  border-color: rgba(31, 32, 36, 0.18);
  box-shadow: 0 2px 8px -2px rgba(0, 0, 0, 0.1);
}

:root[data-qa-theme='ink'] .answer-export,
:root[data-qa-theme='ink'] .answer-truncate {
  background: #ffffff;
  border-color: rgba(31, 32, 36, 0.08);
  box-shadow: none;
}

:root[data-qa-theme='ink'] .qa-jumpdown {
  background: #ffffff;
  border-color: rgba(31, 32, 36, 0.12);
  color: #17181a;
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
}

:root[data-qa-theme='ink'] .qa-track-line {
  background: rgba(31, 32, 36, 0.12);
}

/* ── 过程时间线在 ink 主题下去玻璃化（与旧 tool-trace 一致，走 token 自然降饱和） ── */
:root[data-qa-theme='ink'] .assistant-response .ptl-box {
  background: var(--surface);
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
  border-color: rgba(31, 32, 36, 0.08);
  box-shadow: var(--shadow-sm);
}

/* ── Kimi 首屏上半：浅蓝 pill + wordmark ── */
/* 样张结构：wordmark 贴住输入框；整组（wordmark+输入框+pill排）垂直居中 */
.kimi-welcome {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 0 32px;
}

/* welcome 态滚动机制：整列由 .qa-main 滚动；输入框上方留一段 vh 边距（不强制居中）。
   案例区（WelcomeShowcase）紧随其后，下滑带出、scrollIntoView 可达。
   ⚠️ 勿改成「qa-feed flex 列 + 子项 margin-top:auto 居中」：内容超高时 auto 边距会把
   顶部内容顶到视口上方，滚都滚不到（曾致输入框被顶出屏幕、滚动失效） */
.kimi-welcome-on .kimi-welcome {
  margin-top: 15vh; /* 输入框上方留白；想更靠上/靠下调这个值即可 */
}

/* welcome 态 .qa-feed 不再自成滚动容器（滚动统一在 .qa-main），避免嵌套滚动打架 */
.kimi-welcome-on .qa-feed {
  overflow: visible;
  height: auto;
}

.kimi-welcome-on .qa-stage {
  flex: 0 0 auto;
}

.kimi-welcome-on .qa-main {
  overflow-y: auto;
  scrollbar-gutter: stable; /* 首屏内容超高滚动条出现时页面不横向抖动 */
}

/* 滚动时顶栏 sticky 托底。选择器叠 .qa-shell 提特异性：要盖过 QATopBar scoped 的
   position:relative 与 ink 主题的 background:transparent（同为 0-3-0，靠后置顺序取胜） */
.kimi-welcome-on.qa-shell .qa-topbar {
  position: sticky;
  top: 0;
  z-index: 100;
  background: var(--paper);
}

/* scrollbar-gutter 预留的滚动条槽会把 sticky 顶栏挤窄，顶栏右侧露出一截槽位。
   用伪元素把顶栏的背景与底线向右延伸盖住槽位（宽度给足，超出部分被
   .qa-main 的 overflow-x: clip 裁掉，不影响内容布局） */
.kimi-welcome-on .qa-main {
  overflow-x: clip;
}

.kimi-welcome-on.qa-shell .qa-topbar::after {
  content: '';
  position: absolute;
  /* 顶栏自带 1px 透明边框：向外各扩 1px 对齐到 border box 外沿（总高 56px），
     否则伪元素只齐 padding box，底线比顶栏高 1px */
  top: -1px;
  bottom: -1px;
  left: 100%;
  width: 40px;
  background: var(--paper);
  border-bottom: 1px solid var(--rule);
}

.kimi-brief-pill {
  display: flex;
  align-items: center;
  gap: 7px;
  height: 38px;
  padding: 0 16px;
  border: none;
  border-radius: 999px;
  background: var(--blue-bg);
  color: var(--blue);
  font-family: var(--font-body);
  font-size: 13.5px;
  font-weight: 500;
  cursor: pointer;
  transition: filter 0.15s ease;
}

.kimi-brief-pill:hover {
  filter: brightness(0.97);
}

.kimi-brief-pill.is-loading {
  opacity: 0.7;
}

.kimi-wordmark {
  margin: 0 0 34px;
  text-align: center;
  user-select: none;
}

/* 玻璃主题下 --grad-brand 为极光渐变 → 渐变字；kimi 下为实色 → 墨黑字 */
.kimi-wordmark-main {
  font-size: 46px;
  font-weight: 800;
  letter-spacing: 0.06em;
  line-height: 1.15;
  background: var(--grad-brand);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}

.kimi-wordmark-sub {
  margin-top: 10px;
  font-size: 12px;
  letter-spacing: 0.3em;
  color: var(--ink-4);
}

/* 首屏下半（探索区）已拆为独立组件 WelcomeShowcase.vue，样式随迁。
   此规则作用于子组件根节点（父 scoped 可达子根），保持输入框叠在灰带之上 */
.kimi-welcome-on .composer-frame {
  position: relative;
  z-index: 1;
}

/* ── 手机端首屏：wordmark 缩排（灰带/pill/案例区移动端样式随 WelcomeShowcase 组件迁移） ── */
@media (max-width: 640px) {
  .kimi-welcome {
    padding: 0 16px;
  }

  /* 特异性需盖过 .kimi-welcome-on .kimi-welcome */
  .kimi-welcome-on .kimi-welcome {
    margin-top: 8vh;
  }

  .kimi-wordmark {
    margin: 0 0 20px;
  }

  .kimi-wordmark-main {
    font-size: 44px;
    letter-spacing: 0.04em;
  }
}
</style>
