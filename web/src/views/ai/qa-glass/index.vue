<script setup lang="ts">
import {computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch} from 'vue';
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
import {
  type AgentArtifact,
  type AgentMessage as ApiMessage,
  type AgentSession as ApiSession,
  type AgentSkill,
  type AgentToolStep,
  type Profession,
  type QuickAction,
  type QuickActionExample,
  type QuickActionGroup,
  fetchAgentMessages,
  fetchAgentSessions,
  fetchAgentSkills,
  fetchTruncateAgentSession,
  fetchCreateAgentSession,
  fetchDeleteAgentSession,
  fetchDistillSkillStream,
  fetchKbSedimentSessionStream,
  fetchQAChatStream,
  fetchQAStop,
  fetchQuickActions,
  fetchOnboarding,
  fetchCompleteOnboarding,
  fetchUpdateMyActions,
  fetchUpdateMyProfession,
  fetchUpdateAgentSession,
  fetchUploadFile,
  fetchDailyBriefStream,
  fetchForkQuickActionExample,
  fetchStandardBatchVerify,
  type DailyBriefEvent,
  type QAEvent
} from '@/service/api';
import StdDetailDrawer from '../standard-base-info/modules/std-detail-drawer.vue';
import {exportConversationAsImage} from './modules/export-conversation';

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
type ToolStep = {
  id: number;
  type: 'tool_call' | 'tool_result';
  tool: string;
  displayName: string;
  summary: string;
  params?: Array<{ key: string; value: string }>;
  resultText?: string;
  expanded?: boolean;
};


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
  thinking?: string;
  thinkingHtml?: string;
  toolSteps?: ToolStep[];
  loading?: boolean;
  currentTool?: string;
  error?: string | null;
  /** 用户主动停止（非异常，中性提示） */
  stopped?: boolean;
  chunks?: Record<string, string>;
  artifacts?: AgentArtifact[];
  attachments?: MessageAttachment[];
};

// ───── Skills (from backend) ─────────────────────────────────────────────
const skills = ref<AgentSkill[]>([]);

async function reloadSkills() {
  const {data, error} = await fetchAgentSkills();
  if (!error && data) skills.value = data;
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
    showKbToast('当前会话还没生成，再聊几句吧', 'err');
    return;
  }
  if (messages.value.length < 2) {
    showKbToast('对话太短，没什么可沉淀的', 'err');
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
            showKbToast(result?.summary || '本次对话没什么可记的', 'info');
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

/** 嵌入抽屉时，通过 prefill prop 预填输入框（仅响应变化，初始值由 onMounted 处理） */
watch(() => props.prefill, async (val) => {
  if (val) {
    await startNewSession();
    inputText.value = val;
    nextTick(() => composerRef.value?.focus());
  }
});

const sidebarOpen = ref(!props.embedded);
const composerExpanded = ref(false);
const scrollEl = ref<HTMLElement | null>(null);
const composerRef = ref<{
  focus: () => void;
  setCaretPos: (pos: number) => void;
  resetHeight: () => void;
  openSkillPanel: () => void;
  readonly selectionStart: number | null;
} | null>(null);

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
  if (current === null && questionList.value.length) {
    current = questionList.value[0].id;
  }
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
      window.$message?.error?.('创建会话失败，无法上传文件');
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
  const list = skills.value;
  if (!q) return list;
  return list.filter(
    s =>
      s.skillKey.toLowerCase().includes(q) ||
      s.name.toLowerCase().includes(q) ||
      (s.description ?? '').toLowerCase().includes(q)
  );
});


// abort/msgId 改为按 sessionKey 隔离，已迁移到 activeChatAborts / sessionMsgIdCounter

const currentSession = computed(() => sessions.value.find(s => s.sessionKey === currentSessionKey.value));

const groupedSessions = computed(() => {
  const now = new Date();
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const yesterdayStart = todayStart - 86400000;
  const groups = {
    starred: [] as ApiSession[],
    today: [] as ApiSession[],
    yesterday: [] as ApiSession[],
    earlier: [] as ApiSession[]
  };
  for (const s of [...sessions.value].sort((a, b) => b.updatedAt - a.updatedAt)) {
    // 画板（workflow）会话归属工作流页面，不在问答侧边栏展示
    if (s.source === 'workflow') continue;
    if (s.isStarred) { groups.starred.push(s); continue; }
    if (s.updatedAt >= todayStart) groups.today.push(s);
    else if (s.updatedAt >= yesterdayStart) groups.yesterday.push(s);
    else groups.earlier.push(s);
  }
  return groups;
});

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
  currentSessionKey.value = '';
  syncSessionToUrl();
  closeSidebarIfMobile();
}

function argsToParams(args: Record<string, unknown>): Array<{ key: string; value: string }> {
  return Object.entries(args).map(([k, v]) => ({
    key: k,
    value: typeof v === 'string' ? v : prettyVal(v)
  }));
}

type ResultNode =
  | { kind: 'text'; value: string }
  | { kind: 'kv'; pairs: Array<{ key: string; value: string }> }
  | { kind: 'list'; items: Array<{ label: string; line: string }> };

// 完整递归展开，无 JSON 符号，多行 OK（用在 kv 值列）
function prettyVal(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  if (Array.isArray(v)) {
    if (!v.length) return '(空)';
    if (v.every(x => typeof x !== 'object' || x === null)) {
      return v.map(x => prettyVal(x)).join(', ');
    }
    return v.map((item, i) => `${i + 1}. ${prettyLine(item)}`).join('\n');
  }
  if (typeof v === 'object') {
    const entries = Object.entries(v as Record<string, unknown>);
    if (!entries.length) return '(空)';
    return entries.map(([k, val]) => {
      const str = prettyVal(val);
      return str.includes('\n')
        ? `${k}:\n  ${str.split('\n').join('\n  ')}`
        : `${k}: ${str}`;
    }).join('\n');
  }
  return String(v);
}

// 紧凑单行摘要（用在 list 每行）
function prettyLine(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'string') return v.length > 80 ? v.slice(0, 80) + '…' : v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  if (Array.isArray(v)) return v.length ? v.map(x => prettyLine(x)).join(', ') : '(空)';
  if (typeof v === 'object') {
    return Object.entries(v as Record<string, unknown>)
      .map(([k, val]) => `${k}: ${prettyLine(val)}`)
      .join('  ');
  }
  return String(v);
}

function parseResultContent(text: string): ResultNode {
  if (!text?.trim()) return { kind: 'text', value: '(无返回内容)' };
  try {
    const parsed = JSON.parse(text);
    if (typeof parsed === 'string') return { kind: 'text', value: parsed };
    if (Array.isArray(parsed)) {
      if (!parsed.length) return { kind: 'text', value: '(空列表)' };
      return {
        kind: 'list',
        items: parsed.slice(0, 30).map((item, i) => ({
          label: `${i + 1}`,
          line: prettyLine(item)
        }))
      };
    }
    if (typeof parsed === 'object') {
      return {
        kind: 'kv',
        pairs: Object.entries(parsed as Record<string, unknown>).map(([k, v]) => ({
          key: k,
          value: prettyVal(v)
        }))
      };
    }
    return { kind: 'text', value: String(parsed) };
  } catch {
    return { kind: 'text', value: text };
  }
}

function argsSummary(args: Record<string, unknown>): string {
  const entries = Object.entries(args);
  if (!entries.length) return '(无参数)';
  const first = entries.find(([, v]) => typeof v === 'string') || entries[0];
  const val = prettyLine(first[1]);
  return val.length > 60 ? val.slice(0, 60) + '…' : val;
}

function resultSummary(text: string): string {
  if (!text?.trim()) return '(无返回内容)';
  try {
    const parsed = JSON.parse(text);
    const s = prettyLine(parsed);
    return s.length > 60 ? s.slice(0, 60) + '…' : s;
  } catch {
    return text.length > 60 ? text.slice(0, 60) + '…' : text;
  }
}

function toolStepsToUi(steps: AgentToolStep[] | null | undefined): ToolStep[] {
  if (!steps) return [];
  return steps.map(s => ({
    id: s.id,
    type: s.type,
    tool: s.tool,
    displayName: s.tool_display || s.tool,
    summary:
      s.type === 'tool_call'
        ? argsSummary(s.args || {})
        : resultSummary(s.content ?? ''),
    params: s.type === 'tool_call' ? argsToParams(s.args || {}) : undefined,
    resultText: s.type === 'tool_result' ? (s.content ?? '') : undefined
  }));
}

function apiMsgToUi(m: ApiMessage): Message {
  let content = m.content || '';
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
  const msg: Message = {
    id: m.id,
    serverId: m.id,
    role: m.role,
    content,
    thinking: m.thinking || '',
    toolSteps: toolStepsToUi(m.toolSteps),
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
  if (msg.thinking) msg.thinkingHtml = marked.parse(msg.thinking) as string;
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

  currentSessionKey.value = key;
  syncSessionToUrl();
  closeSidebarIfMobile();

  // 本地已有缓存（含正在跑流的会话），直接复用，不再拉 DB 覆盖
  if (sessionMessages[key] && sessionMessages[key].length > 0) {
    scrollFeedToBottom(false);
    return;
  }

  const {data, error} = await fetchAgentMessages(key);
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
      }
      // 仍在 streaming：保持 loading=true，不做任何修改
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
  await fetchDeleteAgentSession(key).catch(() => {
  });
  sessions.value = sessions.value.filter(s => s.sessionKey !== key);
  if (currentSessionKey.value === key) {
    // 画板会话不在本页展示，兜底切换时同样跳过 workflow 来源
    const next = sessions.value.find(s => s.source !== 'workflow');
    if (next) await loadSession(next.sessionKey);
    else {
      // 最后一个删完也不自动建，保持草稿态
      currentSessionKey.value = '';
    }
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
  return content.replace(/^\[artifact:-?\d+\]\s*$/gm, '').replace(/\n{3,}/g, '\n\n').trim();
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
type ContentSegment = { type: 'html'; html: string; markdown?: string } | { type: 'artifact'; id: number };

/**
 * 解析 content 中的 [artifact:ID] 占位符，切分成文本段 + artifact 段
 * 如果没有任何标记，返回 null（fallback 到原 v-html + 末尾 ArtifactList）
 *
 * 容错策略：当 ID 不匹配时，按出现顺序映射到实际 artifacts（AI 有时会写 [artifact:1] 但实际 ID 是数据库自增值）
 */
function buildContentSegments(
  content: string,
  artifacts: AgentArtifact[] | undefined,
  loading = false
): ContentSegment[] | null {
  if (!artifacts?.length || !content) return null;

  const MARKER_RE = /^\[artifact:(-?\d+)\]$/gm;
  const artifactIds = new Set(artifacts.map(a => a.id));
  const hasMarkers = MARKER_RE.test(content);
  MARKER_RE.lastIndex = 0;
  if (!hasMarkers) return null;

  // 收集所有标记的 ID（按出现顺序）
  const markerIds: number[] = [];
  let match: RegExpExecArray | null;
  while ((match = MARKER_RE.exec(content)) !== null) {
    markerIds.push(Number(match[1]));
  }
  MARKER_RE.lastIndex = 0;

  // 检查是否所有标记 ID 都能在 artifacts 中找到
  const allMatched = markerIds.every(id => artifactIds.has(id));

  // 如果 ID 都不匹配，且标记数量 == artifacts 数量，则按顺序映射
  const needMapping = !allMatched && markerIds.length === artifacts.length;
  const idMap = new Map<number, number>();
  if (needMapping) {
    markerIds.forEach((markerId, idx) => {
      idMap.set(markerId, artifacts[idx].id);
    });
  }

  const segments: ContentSegment[] = [];
  let lastIndex = 0;

  while ((match = MARKER_RE.exec(content)) !== null) {
    const rawId = Number(match[1]);
    const actualId = needMapping ? (idMap.get(rawId) ?? rawId) : rawId;

    // 只处理真实存在的 artifact ID
    if (!artifactIds.has(actualId)) continue;

    // 前面的文本段
    const before = content.slice(lastIndex, match.index).trim();
    if (before) {
      const beforeText = loading ? stripArtifactMarkers(before, true) : before;
      segments.push({ type: 'html', html: marked.parse(beforeText) as string, markdown: loading ? beforeText : undefined });
    }

    // artifact 段
    segments.push({ type: 'artifact', id: actualId });
    lastIndex = MARKER_RE.lastIndex;
  }

  // 剩余文本段
  const after = content.slice(lastIndex).trim();
  if (after) {
    const afterText = loading ? stripArtifactMarkers(after, true) : after;
    segments.push({ type: 'html', html: marked.parse(afterText) as string, markdown: loading ? afterText : undefined });
  }

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
  // 找紧挨着它之前的那条用户消息做标题
  const idx = messages.value.findIndex(m => m.id === msg.id);
  let titleHint = '';
  for (let i = idx - 1; i >= 0; i--) {
    if (messages.value[i].role === 'user') {
      titleHint = messages.value[i].content.slice(0, 40);
      break;
    }
  }
  const title = titleHint || '回复导出';
  const md = buildMarkdown(title, msg.content, msg.artifacts);
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
    thinking: '',
    toolSteps: [],
    loading: true,
    currentTool: '',
    chunks: {}
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
            // 仅当用户没切走时同步 currentSessionKey
            if (!currentSessionKey.value) {
              currentSessionKey.value = realKey;
            }
            reloadSessions();
          }
          if (event.assistantMessageId) {
            msg.serverId = event.assistantMessageId;
          }
        } else if (event.type === 'tool_call') {
          const toolName = event.tool || 'unknown';
          msg.toolSteps!.push({
            id: event.step,
            type: 'tool_call',
            tool: toolName,
            displayName: event.tool_display || toolName,
            summary: argsSummary(event.args || {}),
            params: argsToParams(event.args || {})
          });
          msg.currentTool = event.tool_display || event.tool;
        } else if (event.type === 'tool_result') {
          const c = event.content ?? '';
          const toolName = event.tool || 'unknown';
          msg.toolSteps!.push({
            id: event.step,
            type: 'tool_result',
            tool: toolName,
            displayName: event.tool_display || toolName,
            summary: resultSummary(c),
            resultText: c
          });
          msg.currentTool = '';
        } else if (event.type === 'answer_chunk') {
          const c = event.content ?? '';
          if (!c) return;
          if (!msg.chunks) msg.chunks = {};
          msg.chunks[event.msg_id] = (msg.chunks[event.msg_id] || '') + c;
          appendChunkSmooth(msg, c);
          msg.currentTool = '';
        } else if (event.type === 'reclassify') {
          // 重分类：把同一 msg_id 的累计内容从正文里 rollback，转入 thinking
          const stash = msg.chunks?.[event.msg_id] || '';
          if (stash && msg.content?.endsWith(stash)) {
            msg.content = msg.content.slice(0, msg.content.length - stash.length);
            msg.contentHtml = msg.content ? (marked.parse(stripArtifactMarkers(msg.content, true)) as string) : '';
          }
          if (msg.chunks) msg.chunks[event.msg_id] = undefined as unknown as string;
          const raw = event.content ?? '';
          if (raw) {
            const t = raw.trim();
            if (t) {
              msg.thinking = msg.thinking ? `${msg.thinking}\n\n${t}` : t;
            }
            msg.thinkingHtml = marked.parse(msg.thinking) as string;
          }
        } else if (event.type === 'answer') {
          msg.content = (msg.content || '') + event.content;
          extractAndRefreshInline(msg);
          msg.contentHtml = marked.parse(stripArtifactMarkers(msg.content, true)) as string;
          msg.currentTool = '';
        } else if (event.type === 'done') {
          msg.loading = false;
          msg.currentTool = '';
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
        } else if (event.type === 'error') {
          msg.error = event.message;
          msg.loading = false;
          msg.currentTool = '';
        } else if (event.type === 'quota_exceeded') {
          msg.error = event.message || '积分余额不足，请联系管理员';
          msg.loading = false;
          msg.currentTool = '';
        }
        followScrollIfNeeded();
      },
      abortController.signal,
      filePaths.length > 0 ? filePaths : undefined,
      props.workflowKey,
      undefined,
      props.selectedNodeIds
    );
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

function autoResizeComposer() {
  // QAComposer 内部通过 rows 属性控制高度，手机端额外动态调整由 QAComposer 自身处理
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
  // 覆盖「会话中创建技能」「其他端变更」等面板事件冒泡覆盖不到的路径
  if (!wasOpen) void reloadSkills();
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
    const list = filteredSkills.value;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (list.length) skillActiveIndex.value = (skillActiveIndex.value + 1) % list.length;
      return;
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (list.length) skillActiveIndex.value = (skillActiveIndex.value - 1 + list.length) % list.length;
      return;
    }
    if (e.key === 'Enter' || e.key === 'Tab') {
      if (list.length) {
        e.preventDefault();
        insertSkill(list[skillActiveIndex.value]);
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
  nextTick(autoResizeComposer);
  await sendSingle(text || '[附件]');
}

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
const isOnboarded = ref(false);
const needOnboarding = ref(false);
const myActionIds = ref<number[]>([]); // 订阅功能（按订阅顺序）
const myProfessionId = ref<number | null>(null);
const showAllActions = ref(false); // 橱窗「查看全部」开关
const onboardingProfessions = ref<Profession[]>([]);
const onboardingGroups = ref<QuickActionGroup[]>([]);
const showOnboarding = ref(false); // 首次强制引导
const showMySettings = ref(false); // 功能设置（改职业 / 改订阅）

/** 当前职业（引导时选定，随 onboardingProfessions 一并拉取） */
const myProfession = computed<Profession | null>(() => {
  if (myProfessionId.value === null) return null;
  return onboardingProfessions.value.find(p => p.id === myProfessionId.value) ?? null;
});

/** 书桌模式：已引导、正看「我的功能」、职业有效——卷首语 / 印章 / 水印整体换声，从「宣传册」变「你的书桌」 */
const deskMode = computed(() => isOnboarded.value && !showAllActions.value && myProfession.value !== null);

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
      // 默认选中首个类型分组
      if (!activeShowcaseCat.value && showcaseGroups.value.length) {
        activeShowcaseCat.value = showcaseGroups.value[0].cat;
      }
    }
  } finally {
    showcaseLoading.value = false;
  }
}

/** 拉取新手引导数据：是否需要引导 + 职业清单 + 当前订阅回显 */
async function loadOnboarding() {
  const { data, error } = await fetchOnboarding();
  if (error || !data) return;
  onboardingProfessions.value = data.professions;
  onboardingGroups.value = data.groups;
  needOnboarding.value = data.needOnboarding;
  isOnboarded.value = !data.needOnboarding;
  myActionIds.value = data.current?.actionIds ?? [];
  myProfessionId.value = data.current?.professionId ?? null;
  // 无职业或无功能可勾选时，强制引导会是选无可选、关不掉的死胡同 → 不弹，改由橱窗「空白章节」承接
  if (data.needOnboarding && data.professions.length > 0 && data.actions.length > 0) showOnboarding.value = true;
}

/** 引导 / 设置确认后刷新订阅状态与橱窗（复用 onboarding 数据，无需重拉全量） */
async function refreshSubscription() {
  const { data, error } = await fetchOnboarding();
  if (error || !data) return;
  onboardingProfessions.value = data.professions;
  isOnboarded.value = !data.needOnboarding;
  needOnboarding.value = false;
  myActionIds.value = data.current?.actionIds ?? [];
  myProfessionId.value = data.current?.professionId ?? null;
}

async function handleOnboardingConfirm(payload: {professionId: number; actionIds: number[]; professionChanged: boolean}) {
  const { error } = await fetchCompleteOnboarding({professionId: payload.professionId, actionIds: payload.actionIds});
  if (error) {
    window.$message?.error?.('保存失败，请重试');
    return;
  }
  showOnboarding.value = false;
  await refreshSubscription();
  window.$message?.success?.('欢迎！已为你备好常用功能');
}

async function handleSettingsConfirm(payload: {professionId: number; actionIds: number[]; professionChanged: boolean}) {
  // 换职业先写职业（服务端会重置为推荐），再用实际勾选项覆盖订阅
  if (payload.professionChanged) {
    const { error } = await fetchUpdateMyProfession({professionId: payload.professionId});
    if (error) {
      window.$message?.error?.('保存失败，请重试');
      return;
    }
  }
  const { error } = await fetchUpdateMyActions({actionIds: payload.actionIds});
  if (error) {
    window.$message?.error?.('保存失败，请重试');
    return;
  }
  showMySettings.value = false;
  await refreshSubscription();
  window.$message?.success?.('功能设置已更新');
}

function toggleShowAllActions() {
  showAllActions.value = !showAllActions.value;
  // 切回「我的」时若当前章节已不在订阅里，回落到首个可见章节
  if (!showAllActions.value && activeShowcaseGroup.value && !showcaseGroups.value.some(g => g.cat === activeShowcaseCat.value)) {
    activeShowcaseCat.value = showcaseGroups.value[0]?.cat ?? '';
  }
}


/**
 * 章节来自后端 groups（类型顺序 + 类型内排序均已在后端算好）。
 * 未挂任何类型的功能按全局序收进「更多能力」章。
 */
const showcaseGroups = computed(() => {
  const actionById = new Map(showcaseActions.value.map(a => [a.id, a]));
  const groups: Array<{cat: string; actions: QuickAction[]}> = [];
  const grouped = new Set<number>();
  for (const g of showcaseGroupDefs.value) {
    const members = g.actionIds.map(id => actionById.get(id)).filter((a): a is QuickAction => Boolean(a));
    if (!members.length) continue;
    groups.push({cat: g.name, actions: members});
    members.forEach(a => grouped.add(a.id));
  }
  const rest = showcaseActions.value.filter(a => !grouped.has(a.id));
  if (rest.length) groups.push({cat: '更多能力', actions: rest});
  return groups;
});

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
const chapterTabEls = ref<(HTMLElement | null)[]>([]);
const chapterCursor = reactive({ left: 0, width: 0 });

function setChapterTabRef(el: Element | null, i: number) {
  chapterTabEls.value[i] = el as HTMLElement | null;
}

function measureChapterCursor() {
  const idx = showcaseGroups.value.findIndex(g => g.cat === (activeShowcaseGroup.value?.cat ?? ''));
  const el = idx >= 0 ? chapterTabEls.value[idx] : null;
  if (el) {
    chapterCursor.left = el.offsetLeft;
    chapterCursor.width = el.offsetWidth;
  }
}

watch([activeShowcaseCat, showcaseGroups], () => {
  nextTick(measureChapterCursor);
});

// 切回浏览模式（卷首重新可见）后重测章节游标——书桌模式下卷首隐藏，游标测量无效
watch(deskMode, v => {
  if (!v) nextTick(measureChapterCursor);
});

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
      {session_key: currentSessionKey.value, is_public: false},
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
    window.$message?.error?.('未找到可导出的对话内容');
    return;
  }
  exportingImage.value = true;
  try {
    await exportConversationAsImage({
      node,
      title: currentSession.value?.title || '新对话',
      brandName: brand.qaSidebarTitle,
      assistantName: brand.assistantName,
      exchangeCount: messages.value.filter(m => m.role === 'user').length
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
  const narrow = window.innerWidth < 960;
  isMobile.value = narrow;
  if (narrow) {
    // 窄屏默认收起；用户主动点击汉堡才展开
    sidebarOpen.value = false;
    // 复位 textarea 高度（避免桌面切到手机后残留撑开高度）
    nextTick(autoResizeComposer);
  } else {
    sidebarOpen.value = true;
    // 切回桌面时清掉行内高度，让 rows 属性接管
    composerRef.value?.resetHeight();
  }
}

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

function goToWorkbench() {
  router.push({name: 'ai_home'});
}
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

// 把当前会话状态同步到 URL：sid=会话key 或 sid=new（草稿），并清掉首页一次性参数
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
// 关闭 = 隐藏右上角徽章入口 + 不做任何自动生成
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

onMounted(async () => {
  await Promise.all([reloadSessions(), reloadSkills(), loadShowcaseActions(), loadOnboarding()]);

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
    // 无路由参数：新建空白会话，简报默认折叠，后台预生成，点右上角徽章展开
    await startNewSession();
    triggerDailyBriefIfNeeded(false);
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
  document.removeEventListener('click', handleStdNoClick);
  document.removeEventListener('click', handleCodeCopyClick);
  document.removeEventListener('click', handleTruncateOutsideClick);
  if (!props.embedded) document.removeEventListener('keydown', handleGlobalKeydown);
  for (const key of Object.keys(activeChatAborts)) {
    activeChatAborts[key]?.abort();
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
  <div :class="{ 'sidebar-collapsed': !sidebarOpen, 'is-embedded': embedded }" class="qa-shell">
    <div aria-hidden="true" class="qa-grain"/>

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
      :groupedSessions="groupedSessions"
      :currentSessionKey="currentSessionKey"
      :sessions="sessions"
      :renamingKey="renamingKey"
      :renamingTitle="renamingTitle"
      :runningSessions="runningSessions"
      :briefEnabled="briefEnabled"
      @newSession="() => { startNewSession(); triggerDailyBriefIfNeeded(false); }"
      @openTasks="taskDrawerOpen = true"
      @openWorkflow="handleOpenWorkflow"
      @openSkill="() => composerRef?.openSkillPanel()"
      @createSkill="handleCreateSkill"
      @openSearch="searchModalShow = true"
      @openProfile="handleOpenProfile"
      @update:briefEnabled="setBriefEnabled"
      @loadSession="loadSession"
      @startRename="startRename"
      @commitRename="commitRename"
      @cancelRename="cancelRename"
      @toggleStar="toggleStar"
      @deleteSession="deleteSession"
      @update:renamingTitle="renamingTitle = $event"
    />

    <SessionSearchModal v-model:show="searchModalShow" @select="handleSearchSelect" />

    <!-- 编辑个人资料 -->
    <ProfileModal v-model:show="profileModalOpen" />

    <!-- 「创建技能」首次说明弹窗（仅用户点「知道了/现在就创建」才算知晓，×/遮罩关闭下次仍展示） -->
    <SkillIntroModal :show="skillIntroShow" @dismiss="dismissSkillIntro" @ack="ackSkillIntro" @start="startSkillFromIntro" />

    <!-- 首次强制引导：选职业 → 勾选功能 -->
    <OnboardingModal
      v-model:show="showOnboarding"
      mode="onboarding"
      :professions="onboardingProfessions"
      :actions="allShowcaseActions"
      :groups="onboardingGroups.length ? onboardingGroups : allShowcaseGroupDefs"
      @confirm="handleOnboardingConfirm"
    />
    <!-- 功能设置：改职业 / 改订阅 -->
    <OnboardingModal
      v-model:show="showMySettings"
      mode="settings"
      :professions="onboardingProfessions"
      :actions="allShowcaseActions"
      :groups="onboardingGroups.length ? onboardingGroups : allShowcaseGroupDefs"
      :initial-profession-id="myProfessionId"
      :initial-action-ids="myActionIds"
      @confirm="handleSettingsConfirm"
    />

    <!-- ─── Main column ────────────────────────────────────────────── -->
    <main class="qa-main">
      <QATopBar
        v-if="!hideTopbar"
        :currentSessionTitle="currentSession?.title || '新对话'"
        :messagesLength="messages.length"
        :sedimentingKb="sedimentingKb"
        :distilling="distilling"
        :hasSessionKey="!!currentSessionKey"
        :sedimentMenuOpen="sedimentMenuOpen"
        :exportingImage="exportingImage"
        :streaming="running"
        @toggleSidebar="!embedded && (sidebarOpen = !sidebarOpen)"
        @goToWorkbench="goToWorkbench"
        @goToNian="$router.push('/ai/nian')"
        @sedimentSession="handleSedimentSession"
        @distill="handleDistill"
        @toggleSedimentMenu="sedimentMenuOpen = !sedimentMenuOpen"
        @closeSedimentMenu="closeSedimentMenu"
        @pickSediment="onPickSediment"
        @exportImage="handleExportImage"
      />

      <div class="qa-stage">
        <!-- 简报入口徽章（右上角，brief 隐藏且总开关启用时显示） -->
        <Transition name="brief-btn-fade">
        <button
          v-if="briefEnabled && !briefState.visible"
          class="brief-badge-btn"
          :class="{ 'is-loading': briefState.loading }"
          :title="briefState.loading ? '简报生成中…' : '查看今日简报'"
          @click="openBrief()"
        >
          <svg class="brief-badge-star" viewBox="0 0 14 14" fill="currentColor" width="11" height="11">
            <path d="M7 0l1.5 4L13 7l-4.5 1L7 14 5.5 8 0 7l4.5-1L7 0z" />
          </svg>
          <span class="brief-badge-text">今日简报</span>
        </button>
        </Transition>

        <!-- 每日简报面板 -->
        <Transition name="brief-fade">
        <section v-if="briefState.visible" class="qa-brief">
          <!-- 加载中：今日简报生成中卡片 -->
          <div v-if="briefState.loading && !briefState.topHtml && !briefState.middleHtml" class="dlp-card">
            <div class="dlp-topbar">
              <div class="dlp-logo">
                <div class="dlp-logo-icon">
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
                    <rect x="2" y="1" width="12" height="14" rx="2" stroke="white" stroke-width="1.4"/>
                    <line x1="5" y1="5" x2="11" y2="5" stroke="white" stroke-width="1.4" stroke-linecap="round"/>
                    <line x1="5" y1="8" x2="11" y2="8" stroke="white" stroke-width="1.4" stroke-linecap="round"/>
                    <line x1="5" y1="11" x2="8.5" y2="11" stroke="white" stroke-width="1.4" stroke-linecap="round"/>
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
                <div class="dlp-tw-txt" ref="dlpTwEl"></div>
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
              <HtmlRender :html="briefState.topHtml"/>
            </div>
            <!-- 中：html 内容 -->
            <div v-if="briefState.middleHtml" class="brief-section brief-section--middle">
              <HtmlRender :html="briefState.middleHtml"/>
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
          @click="briefState.visible = false"
          title="收起简报"
        >×</button>

        <section v-if="!briefState.visible" ref="scrollEl" class="qa-feed" @scroll.passive="handleFeedScroll">

        <!-- ─── 能力图鉴 CODEX：简报收起 + 会话为空时的首屏 ─── -->
        <div
          v-if="messages.length === 0"
          class="qa-showcase"
          @mousemove="onCodexStageMove"
          @mouseleave="onCodexStageLeave"
        >
          <!-- 环境层：点阵 + 当前章节号水印 -->
          <div class="codex-ambient" aria-hidden="true">
            <span class="codex-dots" />
            <Transition name="codex-ghost" mode="out-in">
              <!-- 书桌模式：章节号水印换成职业印记——这一页是「你的」 -->
              <span v-if="deskMode && myProfession" key="desk" class="codex-ghostno codex-ghostno--glyph">
                <SvgIcon :icon="myProfession.icon || 'mdi:account-outline'" />
              </span>
              <span v-else :key="activeChapterNo" class="codex-ghostno">{{ activeChapterNo }}</span>
            </Transition>
          </div>

          <!-- 卷首 + 章节索引：翻进条目页（看案例）时整块折起，把高度让给案例列表 -->
          <div class="codex-masthead" :class="{ 'is-collapsed': activeCodexFeature }">
            <div class="codex-masthead-in">
              <!-- 卷首：下面那排功能卡的「头」——与卡片同族（圆角 / 图标牌 / 同一组色相），
                   但存在感压到最低：一抹淡彩 + 发丝边框，不抢卡片的戏。
                   书桌模式只换文案（职业名），样式与「全部功能」完全一致 -->
              <header class="codex-front">
                <div class="codex-front-left">
                  <div class="codex-mark">
                    <SvgIcon v-if="deskMode && myProfession" :icon="myProfession.icon || 'mdi:account-outline'" />
                    <template v-else>№</template>
                  </div>
                  <h1 class="codex-title">{{ deskMode && myProfession ? `${myProfession.name}的工作台` : '我能帮你做什么' }}</h1>
                </div>
                <div class="codex-front-right">
                  <!-- 图鉴统计：功能/常用数 · 案例总数 + 收录日期，版权页式小字 -->
                  <div class="codex-meta">
                    <span>{{ deskMode ? `${showcaseActions.length} 项常用 · ${codexPlateTotal} 个案例` : `${showcaseGroups.length} 类场景 · ${showcaseActions.length} 项功能 · ${codexPlateTotal} 个案例` }}</span>
                    <span class="codex-meta-hint">{{ codexDateStr }}</span>
                  </div>
                  <!-- 已引导用户：切换「我的功能 / 全部功能」+ 功能设置入口 -->
                  <div v-if="isOnboarded" class="codex-myctl">
                    <button type="button" class="codex-myctl-btn" :class="{active: showAllActions}" @click="toggleShowAllActions">
                      <SvgIcon :icon="showAllActions ? 'mdi:star-circle-outline' : 'mdi:view-grid-outline'" />
                      <span>{{ showAllActions ? '返回我的功能' : '查看全部功能' }}</span>
                    </button>
                    <button type="button" class="codex-myctl-btn" @click="showMySettings = true">
                      <SvgIcon icon="mdi:tune-variant" />
                      <span>功能设置</span>
                    </button>
                  </div>
                </div>
              </header>

              <!-- 章节索引 + 极光游标（书桌模式没有章节） -->
              <nav v-if="!deskMode && showcaseGroups.length > 1" class="codex-chapters">
                <span class="codex-cursor" :style="{ left: `${chapterCursor.left}px`, width: `${chapterCursor.width}px` }" aria-hidden="true" />
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
                      <!-- 功能图标回显（管理页录入的 iconify 名） -->
                      <span class="codex-card-ico" aria-hidden="true">
                        <SvgIcon :icon="action.icon || 'mdi:lightning-bolt'" />
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
                      >试一试 <i>↗</i></button>
                      <span v-if="action.examples.length" class="codex-card-cases">
                        <b>{{ action.examples.length }}</b> 个案例 <i>→</i>
                      </span>
                      <span v-else class="codex-card-cases codex-card-cases--none">案例征集中</span>
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
                    >试一试 <i>↗</i></button>
                  </div>

                  <div class="codex-entry-head">
                    <span class="codex-entry-no" aria-hidden="true">{{ activeCodexFeatureNo }}</span>
                    <span class="codex-entry-ico" aria-hidden="true">
                      <SvgIcon :icon="activeCodexFeature.icon || 'mdi:lightning-bolt'" />
                    </span>
                    <h2 class="codex-entry-name">{{ activeCodexFeature.name }}</h2>
                    <span v-if="activeCodexFeature.skillKey" class="codex-callno">@{{ activeCodexFeature.skillKey }}</span>
                  </div>
                  <p v-if="activeCodexFeature.description" class="codex-entry-desc">{{ activeCodexFeature.description }}</p>

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
                </div>
              </Transition>
            </section>

            <!-- 空白章节：无功能可看时的「待收录」跨页——幽灵卡预演目录形状，
                 把空白变成有意为之的留白，而不是看起来像 bug 的空屏 -->
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
                    <button type="button" class="codex-blank-btn codex-blank-btn--solid" @click="showMySettings = true">
                      <SvgIcon icon="mdi:tune-variant" /><span>重新挑选</span>
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
        <div v-if="messages.length > 0" class="conversation">
          <article
            v-for="msg in messages"
            :key="msg.id"
            :class="`exchange-${msg.role}`"
            class="exchange"
          >
            <!-- USER -->
            <template v-if="msg.role === 'user'">
              <div :data-msg-id="msg.id" class="user-question">
                <span class="q-mark">Q.</span>
                <div class="q-body">
                  <n-tooltip
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
                  </n-tooltip>
                  <!-- 截断按钮：放在 q-body 末尾，hover user-question 时显示 -->
                  <template v-if="msg.serverId">
                    <button
                      v-if="truncateConfirmMsgId !== msg.serverId"
                      class="answer-truncate q-truncate"
                      :disabled="truncatingMsgId === msg.serverId"
                      title="从此处截断（删除此问题及之后的全部对话）"
                      @click.stop="truncateConfirmMsgId = msg.serverId!"
                    >
                      <span class="ae-icon">✂</span>
                      <span>截断</span>
                    </button>
                    <button
                      v-else
                      class="answer-truncate answer-truncate--confirm q-truncate"
                      :disabled="truncatingMsgId === msg.serverId"
                      title="确认删除此问题及之后的全部对话"
                      @click.stop="truncateFromMessage(msg.serverId!)"
                    >
                      <span class="ae-icon">⚠</span>
                      <span>{{ truncatingMsgId === msg.serverId ? '删除中…' : '确认截断' }}</span>
                    </button>
                  </template>
                  <div v-if="msg.attachments && msg.attachments.length" class="q-attachments">
                    <n-image-group v-if="msg.attachments.some(a => a.isImage)">
                      <div v-for="att in msg.attachments.filter(a => a.isImage)" :key="att.path" class="q-att-item">
                        <div class="q-att-img-wrap">
                          <n-image
                            :src="buildAttachmentUrl(att.path)"
                            :alt="att.name"
                            object-fit="cover"
                            :img-props="{ class: 'q-att-img', loading: 'lazy' }"
                          />
                        </div>
                      </div>
                    </n-image-group>
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
              <!-- Thinking — prominent standalone block -->
              <aside v-if="msg.thinking" class="thinking">
                <div class="thinking-rule"/>
                <div class="thinking-inner">
                  <div class="thinking-head">
                    <span class="thinking-icon">↯</span>
                    <span class="thinking-label">思&nbsp;考&nbsp;过&nbsp;程</span>
                    <span class="thinking-line"/>
                  </div>
                  <!-- eslint-disable-next-line vue/no-v-html -->
                  <div class="thinking-body" v-html="msg.thinkingHtml || msg.thinking"/>
                </div>
              </aside>

              <!-- Tool trace — collapsed by default -->
              <div v-if="msg.toolSteps && msg.toolSteps.length" class="tool-trace">
                <details>
                  <summary>
                    <span class="trace-icon">⌬</span>
                    <span class="trace-text">执行痕迹</span>
                    <span class="trace-step-badge">{{ msg.toolSteps.length }} 步</span>
                    <span class="trace-toggle">展开<i>↓</i></span>
                  </summary>
                  <ol class="trace-list">
                    <li
                      v-for="step in msg.toolSteps"
                      :key="step.id"
                      :class="`trace-${step.type}`"
                      class="trace-item"
                    >
                      <span class="trace-node"/>
                      <div class="trace-body">
                        <div class="trace-head-row" @click="step.expanded = !step.expanded">
                          <span :class="`tag-${step.type}`" class="trace-tag">
                            {{ step.type === 'tool_call' ? '调用' : '返回' }}
                          </span>
                          <span class="trace-tool">{{ step.displayName }}</span>
                          <span class="trace-seq">{{ String(step.id).padStart(2, '0') }}</span>
                          <span class="trace-arrow">{{ step.expanded ? '▴' : '▾' }}</span>
                        </div>
                        <div v-if="!step.expanded" class="trace-summary-line">{{ step.summary }}</div>
                        <div v-else class="trace-detail">
                          <template v-if="step.type === 'tool_call' && step.params?.length">
                            <div v-for="p in step.params" :key="p.key" class="trace-param-row">
                              <span class="trace-param-key">{{ p.key }}</span>
                              <span class="trace-param-val">{{ p.value }}</span>
                            </div>
                          </template>
                          <template v-else-if="step.type === 'tool_result'">
                            <template v-if="parseResultContent(step.resultText || '').kind === 'kv'">
                              <div
                                v-for="p in (parseResultContent(step.resultText || '') as any).pairs"
                                :key="p.key"
                                class="trace-param-row trace-result-row"
                              >
                                <span class="trace-param-key trace-result-key">{{ p.key }}</span>
                                <span class="trace-param-val">{{ p.value }}</span>
                              </div>
                            </template>
                            <template v-else-if="parseResultContent(step.resultText || '').kind === 'list'">
                              <div
                                v-for="item in (parseResultContent(step.resultText || '') as any).items"
                                :key="item.label"
                                class="trace-list-item"
                              >
                                <span class="trace-list-seq">{{ item.label }}</span>
                                <span class="trace-list-line">{{ item.line }}</span>
                              </div>
                            </template>
                            <div v-else class="trace-result-text">
                              {{ (parseResultContent(step.resultText || '') as any).value }}
                            </div>
                          </template>
                          <div v-else class="trace-result-text">(无内容)</div>
                        </div>
                      </div>
                    </li>
                  </ol>
                </details>
              </div>

              <!-- Loading：打字机已在输出且无工具调用时，不再呈现"思考中" -->
              <div v-if="msg.loading && (msg.currentTool || !msg.content)" class="loading-line">
                <span class="orbit-dual">
                  <span class="star"/><span class="star"/>
                  <span class="inner-ring"><span class="inner-star"/></span>
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
                <div class="answer-mark">A.</div>
                <!-- inline 模式：分段内容包在 flex:1 的 wrapper 里 -->
                <template v-if="msg.contentSegments?.length">
                  <div class="answer-body-segments">
                    <template v-for="(seg, si) in msg.contentSegments" :key="si">
                      <!-- eslint-disable-next-line vue/no-v-html -->
                      <div v-if="seg.type === 'html'" class="answer-body" :class="{ 'is-last-segment': si === msg.contentSegments.length - 1 }" v-html="seg.html"/>
                      <ArtifactList
                        v-else-if="seg.type === 'artifact' && getArtifactById(msg, seg.id).length"
                        :artifacts="getArtifactById(msg, seg.id)"
                        :inline="true"
                      />
                    </template>
                  </div>
                </template>
                <!-- 普通模式：单个 answer-body，与原来完全一致 -->
                <div v-else class="answer-body" v-html="msg.contentHtml"/>
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
      <aside
        v-if="questionList.length"
        :class="{ 'is-hover': trackHover }"
        class="qa-track"
        @mouseenter="trackHover = true"
        @mouseleave="trackHover = false"
      >
        <div class="qa-track-rail">
          <span class="qa-track-line"/>
          <button
            v-for="q in questionList"
            :key="q.id"
            :class="{ active: q.id === activeQuestionId }"
            :title="q.text"
            class="qa-track-tick"
            type="button"
            @click="jumpToQuestion(q.id)"
          >
            <span class="qa-track-dot"/>
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
        v-model:composerExpanded="composerExpanded"
        v-model:skillActiveIndex="skillActiveIndex"
        :running="running"
        :attachedFiles="attachedFiles"
        :isMobile="isMobile"
        :filteredSkills="filteredSkills"
        :skillPopupOpen="skillPopupOpen"
        :currentSessionKey="currentSessionKey || null"
        @send="handleSend"
        @stop="handleStop"
        @fileSelect="uploadFiles"
        @removeAttachment="removeAttachment"
        @previewAttachment="handleAttachmentPreview"
        @insertSkill="insertSkill"
        @input="() => handleInput()"
        @keydown="handleKeydown"
        @paste="handlePaste"
        @closeSkillPopup="closeSkillPopup"
        @skillPanelChange="reloadSkills"
      />
    </main>

    <!-- Distill result modal -->
    <div v-if="distillResult || distillError" class="distill-mask" @click.self="closeDistillResult">
      <div class="distill-modal">
        <header class="dm-head">
          <span class="dm-tag">{{ distillResult ? 'SKILL·SAVED' : 'DISTILL·FAILED' }}</span>
          <span class="dm-line"/>
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
    <TaskDrawer v-model:show="taskDrawerOpen" @loadSession="loadSession" @fill="onTaskDrawerFill" />

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

  /* ─── 阴影系统 ─── */
  --shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.04), 0 4px 16px -8px rgba(30, 64, 175, 0.12);
  --shadow-md: 0 1px 2px rgba(15, 23, 42, 0.05), 0 12px 32px -12px rgba(30, 64, 175, 0.18);
  --shadow-lg: 0 1px 2px rgba(15, 23, 42, 0.05), 0 24px 64px -20px rgba(30, 64, 175, 0.28);
  --shadow-glow: 0 8px 32px -10px rgba(30, 64, 175, 0.45);

  /* ─── 字体 ─── */
  --font-display: 'Plus Jakarta Sans', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', system-ui, sans-serif;
  --font-body: 'Plus Jakarta Sans', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', system-ui, sans-serif;
  --font-mono: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace;

  position: relative;
  display: grid;
  grid-template-columns: 286px 1fr;
  height: 100%;
  width: 100%;
  background: var(--paper);
  color: var(--ink);
  font-family: var(--font-body);
  overflow: hidden;
  transition: grid-template-columns 0.42s cubic-bezier(0.65, 0, 0.35, 1);
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

.qa-shell.sidebar-collapsed {
  grid-template-columns: 0 1fr;
}

/* Atmospheric aurora orbs */
.qa-grain {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 0;
  overflow: hidden;
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
  opacity: 0.35;
  animation: qa-aurora-1 26s ease-in-out infinite;
}

.qa-grain::after {
  width: 720px;
  height: 720px;
  bottom: -260px;
  left: -200px;
  background: radial-gradient(circle, #0891b2 0%, transparent 65%);
  opacity: 0.32;
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

/* 第三、第四颗 orb（用 ::before/::after on shell） */
.qa-shell::after {
  content: '';
  position: absolute;
  width: 480px;
  height: 480px;
  top: 36%;
  right: 14%;
  border-radius: 50%;
  background: radial-gradient(circle, #0ea5e9 0%, transparent 65%);
  filter: blur(120px);
  pointer-events: none;
  z-index: 0;
  opacity: 0.3;
  animation: qa-aurora-3 28s ease-in-out infinite;
}

@keyframes qa-aurora-3 {
  0%, 100% { transform: translate(0, 0) scale(1); }
  33%      { transform: translate(-50px, 30px) scale(0.96); }
  66%      { transform: translate(40px, -25px) scale(1.04); }
}

.qa-shell::before {
  content: '';
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 0;
  background-image:
    radial-gradient(circle at 1px 1px, rgba(15, 23, 42, 0.05) 1px, transparent 0);
  background-size: 3px 3px;
  opacity: 0.4;
  mix-blend-mode: multiply;
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

/* Topbar */
.qa-topbar {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 14px 32px;
  border-bottom: 1px solid var(--rule);
  flex-shrink: 0;
  background: var(--paper);
}

.topbar-toggle {
  background: none;
  border: 1px solid var(--rule);
  cursor: pointer;
  font-family: var(--font-body);
  font-size: 11px;
  color: var(--ink-2);
  border-radius: 9px;
  transition: all 0.15s;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 8px 10px;
}

.topbar-toggle:hover {
  border-color: var(--ink-2);
  color: var(--accent);
}

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

.hamburger {
  display: inline-block;
  width: 14px;
  height: 1px;
  background: currentColor;
  position: relative;
}

.hamburger::before, .hamburger::after {
  content: '';
  position: absolute;
  left: 0;
  width: 14px;
  height: 1px;
  background: currentColor;
  transition: transform 0.2s;
}

.hamburger::before {
  top: -4px;
}

.hamburger::after {
  top: 4px;
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

/* 简报入口徽章（右上角极光胶囊） */
.brief-badge-btn {
  position: absolute;
  top: 14px;
  right: 20px;
  z-index: 6;
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 30px;
  padding: 0 14px 0 11px;
  border: none;
  border-radius: 999px;
  background: linear-gradient(110deg, #1e40af 0%, #2563eb 35%, #0ea5e9 70%, #0891b2 100%);
  color: #fff;
  cursor: pointer;
  box-shadow:
    0 2px 8px -2px rgba(30, 64, 175, 0.45),
    0 0 0 1px rgba(255, 255, 255, 0.15) inset,
    0 1px 0 rgba(255, 255, 255, 0.25) inset;
  transition: all 0.25s ease;
  animation: brief-badge-glow 3s ease-in-out infinite;
}
.brief-badge-btn:hover {
  transform: translateY(-1px) scale(1.04);
  box-shadow:
    0 6px 20px -4px rgba(30, 64, 175, 0.55),
    0 0 0 1px rgba(255, 255, 255, 0.2) inset,
    0 1px 0 rgba(255, 255, 255, 0.3) inset;
}
.brief-badge-btn:active {
  transform: translateY(0) scale(0.98);
}
.brief-badge-btn.is-loading {
  animation: brief-badge-pulse 1.6s ease-in-out infinite;
}
@keyframes brief-badge-glow {
  0%, 100% { box-shadow: 0 2px 8px -2px rgba(30,64,175,0.45), 0 0 0 1px rgba(255,255,255,0.15) inset, 0 1px 0 rgba(255,255,255,0.25) inset; }
  50% { box-shadow: 0 4px 16px -2px rgba(30,64,175,0.6), 0 0 0 1px rgba(255,255,255,0.2) inset, 0 1px 0 rgba(255,255,255,0.3) inset, 0 0 12px rgba(14,165,233,0.25); }
}
@keyframes brief-badge-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.6; }
}
.brief-badge-star {
  animation: brief-star-spin 6s linear infinite;
  flex-shrink: 0;
}
@keyframes brief-star-spin {
  from { transform: rotate(0); }
  to { transform: rotate(360deg); }
}
.brief-badge-text {
  font-family: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.04em;
  white-space: nowrap;
}

/* 入口按钮出入动效 */
.brief-btn-fade-enter-active {
  transition: opacity 0.35s ease 0.2s, transform 0.35s cubic-bezier(0.16, 1, 0.3, 1) 0.2s;
}
.brief-btn-fade-leave-active {
  transition: opacity 0.15s ease, transform 0.15s ease;
}
.brief-btn-fade-enter-from {
  opacity: 0;
  transform: scale(0.7) translateY(-4px);
}
.brief-btn-fade-leave-to {
  opacity: 0;
  transform: scale(0.85);
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
  transition: width 0.18s cubic-bezier(0.22, 1, 0.36, 1),
              right 0.18s cubic-bezier(0.22, 1, 0.36, 1);
}

.qa-track.is-hover {
  width: 296px;
  right: 16px;
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
  color: var(--ink-3);
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  text-overflow: ellipsis;
  word-break: break-word;
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
  bottom: -4px;
  height: 3px;
  border-radius: 2px;
  background: var(--aurora);
  background-size: 200% 100%;
  animation: codex-holo 5.2s ease-in-out infinite;
  box-shadow: 0 2px 8px -2px rgba(30, 64, 175, 0.5);
  transition: left 0.32s cubic-bezier(0.22, 1, 0.36, 1), width 0.32s cubic-bezier(0.22, 1, 0.36, 1);
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

/* User question */
.user-question {
  display: flex;
  gap: 12px;
  padding-bottom: 20px;
  border-bottom: 1px solid rgba(30, 64, 175, 0.06);
}

.q-mark {
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
  flex: 1;
  min-width: 0;
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

/* ─── THINKING — prominent ──────────────────────────────────────── */
.thinking {
  position: relative;
  display: flex;
  background: rgba(255, 255, 255, 0.42);
  backdrop-filter: blur(20px) saturate(180%);
  -webkit-backdrop-filter: blur(20px) saturate(180%);
  border: 1px solid rgba(30, 64, 175, 0.1);
  border-radius: 14px;
  animation: thinkingFade 0.5s ease-out;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.95),
    inset 0 0 0 1px rgba(255, 255, 255, 0.4),
    0 1px 2px rgba(15, 23, 42, 0.04);
  overflow: hidden;
}

.thinking-rule {
  width: 3px;
  background: linear-gradient(180deg, #1e40af, #0891b2);
  flex-shrink: 0;
  border-radius: 14px 0 0 14px;
}

.thinking-inner {
  flex: 1;
  min-width: 0;
  padding: 16px 24px 20px;
}

.thinking-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}

.thinking-icon {
  font-family: var(--font-display);
  font-size: 16px;
  line-height: 1;
  color: #1e40af;
  font-weight: 700;
  font-style: normal;
}

.thinking-label {
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.06em;
  color: #1e40af;
  text-transform: uppercase;
}

.thinking-line {
  flex: 1;
  height: 1px;
  background: linear-gradient(to right, var(--accent), transparent);
  opacity: 0.4;
}

.thinking-body {
  font-family: var(--font-body);
  font-style: normal;
  font-size: 14px;
  line-height: 1.78;
  color: var(--ink-2);
  word-break: break-word;
  font-weight: 500;
  letter-spacing: 0;
}

.thinking-body :deep(p) {
  margin: 8px 0;
}

.thinking-body :deep(p:first-child) {
  margin-top: 0;
}

.thinking-body :deep(p:last-child) {
  margin-bottom: 0;
}

.thinking-body :deep(h1),
.thinking-body :deep(h2),
.thinking-body :deep(h3),
.thinking-body :deep(h4) {
  font-family: var(--font-display);
  font-style: normal;
  font-weight: 700;
  color: var(--ink);
  margin: 14px 0 6px;
  line-height: 1.35;
  letter-spacing: -0.005em;
}

.thinking-body :deep(h1) {
  font-size: 18px;
}

.thinking-body :deep(h2) {
  font-size: 16.5px;
}

.thinking-body :deep(h3) {
  font-size: 15.5px;
}

.thinking-body :deep(h4) {
  font-family: var(--font-mono), monospace;
  font-style: normal;
  font-size: 11.5px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--ink-2);
}

.thinking-body :deep(strong) {
  font-style: normal;
  font-weight: 600;
  color: var(--ink);
}

.thinking-body :deep(em) {
  color: var(--accent);
}

.thinking-body :deep(ul), .thinking-body :deep(ol) {
  padding-left: 20px;
  margin: 8px 0;
  font-style: italic;
}

.thinking-body :deep(li) {
  margin: 3px 0;
}

.thinking-body :deep(li::marker) {
  color: var(--accent);
}

.thinking-body :deep(code) {
  font-family: var(--font-mono), monospace;
  font-style: normal;
  font-size: 12px;
  background: rgba(30, 64, 175, 0.06);
  color: var(--accent-deep);
  padding: 2px 6px;
  border-radius: 5px;
  border: 1px solid var(--rule-soft);
}

.thinking-body :deep(pre) {
  font-style: normal;
  background: #0f172a;
  color: #e2e8f0;
  padding: 12px 16px;
  border-radius: 10px;
  margin: 10px 0;
  font-size: 12px;
  border: 1px solid var(--rule);
  max-width: 100%;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
}

.thinking-body :deep(pre code) {
  background: transparent;
  color: inherit;
  border: none;
  padding: 0;
  font-size: 12px;
}

.thinking-body :deep(blockquote) {
  margin: 10px 0;
  padding: 4px 14px;
  border-left: 2px solid var(--accent);
  color: var(--ink-2);
  font-style: italic;
}

.thinking-body :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 10px 0;
  font-size: 12.5px;
  font-style: normal;
  font-family: var(--font-body), sans-serif;
}

.thinking-body :deep(th), .thinking-body :deep(td) {
  padding: 6px 10px;
  border-bottom: 1px solid var(--rule-soft);
  text-align: left;
  vertical-align: top;
}

.thinking-body :deep(th) {
  font-family: var(--font-mono), monospace;
  font-size: 10px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--ink-2);
  border-bottom: 1px solid var(--ink-3);
}

.thinking-body :deep(hr) {
  border: none;
  border-top: 1px dashed var(--rule);
  margin: 14px 0;
}

.thinking-body :deep(a) {
  color: var(--accent);
  text-decoration: underline;
  text-underline-offset: 2px;
}

/* ─── TOOL TRACE ────────────────────────────────────────────────── */
.tool-trace {
  margin: 10px 0 6px;
}

.tool-trace details {
  border: 1px solid rgba(30, 64, 175, 0.1);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.42);
  backdrop-filter: blur(20px) saturate(180%);
  overflow: hidden;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.95),
    inset 0 0 0 1px rgba(255, 255, 255, 0.4);
}

.tool-trace summary {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 14px;
  cursor: pointer;
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.06em;
  color: var(--ink-3);
  list-style: none;
  user-select: none;
  transition: all 0.15s;
  background: linear-gradient(90deg, var(--accent-soft) 0%, transparent 70%);
}

.tool-trace summary::-webkit-details-marker {
  display: none;
}

.tool-trace summary:hover {
  color: var(--ink);
  background: linear-gradient(90deg, rgba(30,64,175,0.1) 0%, transparent 70%);
}

.tool-trace details[open] summary {
  border-bottom: 1px solid var(--rule);
  color: var(--ink);
}

.tool-trace details[open] .trace-toggle i {
  transform: rotate(180deg);
}

.trace-icon {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  border: 1.5px solid var(--accent);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 9px;
  color: var(--accent);
  flex-shrink: 0;
  background: var(--accent-soft);
}

.trace-text {
  flex: 1;
}

.trace-step-badge {
  background: var(--accent);
  color: #fff;
  font-size: 9px;
  font-weight: 700;
  padding: 1px 8px;
  border-radius: 99px;
  letter-spacing: 0.08em;
}

.trace-toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--ink-4);
  font-size: 9px;
  letter-spacing: 0.1em;
}

.trace-toggle i {
  font-style: normal;
  transition: transform 0.2s;
  display: inline-block;
}

.trace-list {
  list-style: none;
  padding: 6px 0;
  margin: 0;
  position: relative;
}

.trace-list::before {
  content: '';
  position: absolute;
  left: 32px;
  top: 18px;
  bottom: 18px;
  width: 1px;
  background: repeating-linear-gradient(
    to bottom,
    var(--rule) 0 5px,
    transparent 5px 9px
  );
}

.trace-item {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 7px 14px 7px 18px;
  transition: background 0.1s;
  position: relative;
}

.trace-item:hover {
  background: rgba(30, 64, 175, 0.025);
}

.trace-node {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
  margin-top: 4px;
  position: relative;
  z-index: 1;
}

.trace-tool_call .trace-node {
  background: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}

.trace-tool_result .trace-node {
  background: #2a9d8f;
  box-shadow: 0 0 0 3px rgba(42, 157, 143, 0.12);
}

.trace-body {
  flex: 1;
  min-width: 0;
}

.trace-head-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-family: var(--font-mono);
  margin-bottom: 3px;
  cursor: pointer;
}

.trace-tag {
  font-size: 9px;
  font-weight: 700;
  padding: 1px 7px;
  letter-spacing: 0.1em;
  border-radius: 99px;
  flex-shrink: 0;
}

.tag-tool_call {
  background: var(--accent-soft);
  color: var(--accent);
  border: 1px solid rgba(30, 64, 175, 0.18);
}

.tag-tool_result {
  background: rgba(42, 157, 143, 0.1);
  color: #2a9d8f;
  border: 1px solid rgba(42, 157, 143, 0.2);
}

.trace-tool {
  flex: 1;
  color: var(--ink);
  font-weight: 600;
  font-size: 11.5px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.trace-seq {
  font-size: 9px;
  color: var(--ink-4);
  font-weight: 400;
  flex-shrink: 0;
}

.trace-arrow {
  color: var(--ink-4);
  font-size: 9px;
  flex-shrink: 0;
}

.trace-summary-line {
  font-family: var(--font-body);
  font-size: 11.5px;
  color: var(--ink-3);
  line-height: 1.5;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.trace-detail {
  margin: 6px 0 2px;
  border: 1px solid var(--rule-soft);
  border-left: 2px solid var(--accent);
  border-radius: 10px;
  overflow: hidden;
}

.trace-tool_result .trace-detail {
  border-left-color: #2a9d8f;
}

.trace-param-row {
  display: grid;
  grid-template-columns: minmax(80px, auto) 1fr;
  align-items: baseline;
  gap: 0;
  border-bottom: 1px solid var(--rule-soft);
}

.trace-param-row:last-child {
  border-bottom: none;
}

.trace-param-key {
  padding: 6px 10px;
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 600;
  color: var(--accent);
  letter-spacing: 0.04em;
  background: var(--accent-soft);
  white-space: nowrap;
  border-right: 1px solid var(--rule-soft);
  align-self: stretch;
  display: flex;
  align-items: flex-start;
  padding-top: 8px;
}

.trace-param-val {
  padding: 6px 12px;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--ink-2);
  white-space: pre-wrap;
  word-break: break-all;
  line-height: 1.6;
  max-height: 200px;
  overflow-y: auto;
  background: var(--paper);
}

.trace-result-row .trace-result-key {
  background: rgba(42, 157, 143, 0.08);
  color: #2a9d8f;
}

.trace-list-item {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 5px 12px;
  border-bottom: 1px solid var(--rule-soft);
  background: var(--paper);
}

.trace-list-item:last-child {
  border-bottom: none;
}

.trace-list-seq {
  font-family: var(--font-mono);
  font-size: 9px;
  font-weight: 700;
  color: var(--ink-4);
  min-width: 16px;
  flex-shrink: 0;
}

.trace-list-line {
  font-family: var(--font-body);
  font-size: 11px;
  color: var(--ink-2);
  line-height: 1.5;
  word-break: break-all;
}

.trace-result-text {
  padding: 8px 14px;
  font-family: var(--font-body);
  font-size: 11.5px;
  color: var(--ink-2);
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.65;
  max-height: 200px;
  overflow-y: auto;
  background: var(--paper);
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

.answer-mark {
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
  margin-top: 4px;
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

/* 用户问题上的截断按钮：默认隐藏，hover user-question 时显示 */
.q-truncate {
  opacity: 0;
  margin-top: 6px;
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
.answer-body :deep(.code-block),
.thinking-body :deep(.code-block) {
  position: relative;
  margin: 14px 0;
  max-width: 100%;
  min-width: 0;
}

.answer-body :deep(.code-block) > pre,
.thinking-body :deep(.code-block) > pre {
  margin: 0;
  max-width: 100%;
  overflow: hidden;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
}

.answer-body :deep(.code-block) > pre code,
.thinking-body :deep(.code-block) > pre code {
  display: block;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
}

.answer-body :deep(.code-block-lang),
.thinking-body :deep(.code-block-lang) {
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

.answer-body :deep(.code-copy-btn),
.thinking-body :deep(.code-copy-btn) {
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
.thinking-body :deep(.code-block:hover .code-copy-btn),
.answer-body :deep(.code-copy-btn:focus-visible),
.thinking-body :deep(.code-copy-btn:focus-visible) {
  opacity: 1;
}

.answer-body :deep(.code-copy-btn:hover),
.thinking-body :deep(.code-copy-btn:hover) {
  color: #fff;
  border-color: rgba(226, 232, 240, 0.4);
  background: rgba(15, 23, 42, 0.85);
}

.answer-body :deep(.code-copy-btn.is-done),
.thinking-body :deep(.code-copy-btn.is-done) {
  opacity: 1;
  color: #4ade80;
  border-color: rgba(74, 222, 128, 0.45);
}

.answer-body :deep(.code-copy-btn.is-error),
.thinking-body :deep(.code-copy-btn.is-error) {
  opacity: 1;
  color: #f87171;
  border-color: rgba(248, 113, 113, 0.45);
}

.answer-body :deep(.ccb-icon),
.thinking-body :deep(.ccb-icon) {
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

/* 大框模式：高度定死 56vh，输入框 flex 吃掉剩余空间，页脚钉死底边不抖动 */
.qa-composer.is-expanded {
  height: 56vh;
}

.qa-composer.is-expanded .composer-frame {
  flex: 1;
  min-height: 140px;
  align-items: stretch;
}

/* 纵向 flex 列里 auto 外边距会关掉 stretch——显式满宽，max-width + margin:auto 负责 820 居中 */
.composer-toolbar,
.attached-bar,
.composer-frame,
.composer-foot {
  width: 100%;
}

.qa-composer.is-expanded .composer-input {
  max-height: none;
  height: 100%;
}

/* 大框模式：按钮组保持和小框一样钉在右下角，不随框变高而跳位 */
.qa-composer.is-expanded .composer-side {
  align-self: flex-end;
  padding-bottom: 2px;
}

.qa-composer.is-expanded .composer-prompt {
  align-self: flex-start;
  padding-top: 2px;
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

.btn-expand {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  padding: 0;
  background: rgba(255, 255, 255, 0.62);
  border: 1px solid rgba(30, 64, 175, 0.1);
  color: var(--ink-3);
  cursor: pointer;
  border-radius: 8px;
  transition: all 0.2s ease;
  flex-shrink: 0;
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.95), inset 0 0 0 1px rgba(255,255,255,0.4);
}

.btn-expand:hover {
  border-color: rgba(30, 64, 175, 0.25);
  color: #1e40af;
  background: rgba(255, 255, 255, 0.78);
}

.expand-icon {
  position: relative;
  width: 14px;
  height: 14px;
  display: inline-block;
}

.ex-arrow {
  position: absolute;
  width: 6px;
  height: 6px;
  border: 1.4px solid currentColor;
  border-right: none;
  border-bottom: none;
}

.ex-tl {
  top: 0;
  left: 0;
}

.ex-br {
  bottom: 0;
  right: 0;
  transform: rotate(180deg);
}

.ex-tl-in {
  top: 0;
  right: 0;
  transform: rotate(90deg);
}

.ex-br-in {
  bottom: 0;
  left: 0;
  transform: rotate(-90deg);
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

@keyframes thinkingFade {
  from {
    opacity: 0;
    transform: translateY(-6px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
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
  .qa-topbar .topbar-toggle,
  .qa-topbar .topbar-distill,
  .qa-topbar .topbar-kb,
  .btn-attach,
  .btn-expand,
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
  .thinking-inner,
  .tool-trace summary,
  .trace-detail,
  .trace-item,
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
  .qa-topbar .topbar-toggle {
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

  .q-mark, .answer-mark {
    font-size: 19px;
  }

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

  /* 思考过程 */
  .thinking-inner {
    padding: 12px 14px 14px;
  }

  .thinking-body {
    font-size: 14px;
    line-height: 1.7;
  }

  /* tool trace */
  .tool-trace summary {
    padding: 8px 12px;
  }

  .trace-detail {
    font-size: 11px;
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

  /* 手机端不需要"放大输入框"按钮，textarea 自动撑高 1-7 行 */
  .btn-expand {
    display: none;
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

  /* 窄屏下不需要"展开输入框"模式 */
  .qa-composer.is-expanded {
    max-height: none;
    height: auto;
  }

  .qa-composer.is-expanded .composer-frame {
    flex: none;
    height: auto;
    align-items: flex-end;
  }

  .qa-composer.is-expanded .composer-input {
    max-height: none;
    height: auto;
    min-height: 36px;
  }

  .qa-composer.is-expanded .composer-side {
    flex-direction: row;
    align-items: flex-end;
    align-self: flex-end;
    padding: 0;
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

/* 会话区收窄：48px 侧边距在 440px 浮窗里会吃掉大半内容宽。
   左侧刻意放宽到 34px 作"页边留白"——Q./A. 圆徽像印刷版式的旁注印章
   挂进这条边栏（参见悬挂引号的做法），正文列左缘从而与 thinking /
   tool-trace 等全宽块对齐，列内零空槽；右侧保持 16px 不贴边 */
.qa-shell.is-embedded .conversation {
  padding: 22px 16px 28px 34px;
}

/* 窄窗问答布局：Q./A. 圆徽挂进左侧页边留白（绝对定位出正文列），
   正文铺满整列、零左槽。徽章仍在、版式门面不丢，只是从"占一列"
   变成"旁注印章"。
   ⚠ 别再改回 flex-row + wrap：正文 flex:1（basis 0%）与 actions
   （basis 100%）合计恰好"装得下"一行，flex 不换行，正文塌成 0 宽
   每行一字的竖排 */
.qa-shell.is-embedded .answer {
  display: block;
}

.qa-shell.is-embedded .answer-mark,
.qa-shell.is-embedded .q-mark {
  position: absolute;
  left: -30px; /* 挂进 34px 页边留白：距窗边 ~4px，与正文隔 ~8px */
  margin-top: 0; /* 基类有 margin-top:4px，绝对定位下会叠加到 top 上 */
  /* 迷你印章：缩到 9px + 窄内边距（基类 11px/4px 8px 几乎顶到正文），
     阴影同步收轻，小尺寸下不糊成一团 */
  font-size: 9px;
  padding: 2px 6px;
  letter-spacing: 0.04em;
  box-shadow: 0 1px 5px -2px rgba(30, 64, 175, 0.3);
}

.qa-shell.is-embedded .answer-mark {
  top: 7px; /* 与 15px/1.85 正文首行垂直居中 */
}

.qa-shell.is-embedded .q-mark {
  top: 6px; /* 与 17px/1.5 问题首行垂直居中 */
}

.qa-shell.is-embedded .user-question {
  position: relative;
  gap: 0;
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

.qa-shell.is-embedded :deep(.btn-expand) {
  width: 26px;
  height: 26px;
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

/* 大框模式 56vh 按视口算，浮窗里一口吞掉整个窗口（浮窗本身才 660px）；
   改成按浮窗比例；父链高度不定时 percentage 自动回落 auto，无副作用 */
.qa-shell.is-embedded .qa-composer.is-expanded {
  height: 65%;
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
</style>
