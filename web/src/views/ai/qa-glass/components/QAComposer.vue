<script setup lang="ts">
import { ref, nextTick, watch, computed } from 'vue';
import { NImage, NSwitch } from 'naive-ui';
import type { AgentConnector, AgentSkill, WorkflowListItem } from '@/service/api/ai';
import { fetchBatchAgentConnectorPrefs } from '@/service/api';
import {extractExt} from '@/utils/attachment';
import { useAuthStore } from '@/store/modules/auth';
import { brand } from '@/constants/brand';
import SvgIcon from '@/components/custom/svg-icon.vue';
import SkillPanel from './skill/SkillPanel.vue';
import ConnectorStack from './ConnectorStack.vue';
import ExpertChip from './ExpertChip.vue';
import { skillIconHtml, customIconHtml } from './skill/skill-icon';

const authStore = useAuthStore();

/** 「专家」体系称谓随品牌变体：standard=助理 / generic=专家（+ 菜单条目用） */
const expertLabel = brand.expertLabel;

/** 管理员判定（R_SUPER 或 R_ADMIN）：持续精造等管理入口仅管理员可见 */
const isAdmin = computed(() => (authStore.userInfo?.roles || []).some(r => r === 'R_SUPER' || r === 'R_ADMIN'));

/** 是否当前用户创建的技能：@ 弹层不展示来源/作者徽章，只给本人技能打「我的」标签 */
function isMySkill(sk: AgentSkill) {
  const uid = authStore.userInfo?.userId;
  return sk.userId != null && uid != null && uid !== '' && String(sk.userId) === String(uid);
}

interface AttachedFile {
  id: string;
  name: string;
  path: string;
  size: number;
  uploading: boolean;
  progress: number;
  error: string;
  previewUrl?: string;
}

const props = defineProps<{
  modelValue: string;
  running: boolean;
  attachedFiles: AttachedFile[];
  /** 对话模式偏好（模式 × 思考强度 + 元数据）：null=未加载，切换钮显示默认 */
  chatMode?: Api.AI.ChatModePref | null;
  isMobile: boolean;
  filteredSkills: AgentSkill[];
  skillPopupOpen: boolean;
  skillActiveIndex: number;
  currentSessionKey: string | null;
  /** mini 形态（嵌入工作流画布迷你栏）：去掉工具条 / 页脚 / 放大按钮，压缩输入区高度；外壳玻璃容器由宿主提供 */
  mini?: boolean;
  /** 首屏形态：@技能弹层改为向下展开（输入框位置靠上，上方空间不够） */
  popupDown?: boolean;
  /** 我的连接器（已添加且生效，含禁用态）：圆片堆只渲染启用态，弹层可启停 */
  activeConnectors?: AgentConnector[];
  /** 当前会话驻留专家（null=通用会话） */
  sessionExpert?: {key: string; name: string; icon?: string | null; welcome?: string | null} | null;
  /** 已添加且启用的专家（无驻留专家时的召唤候选） */
  expertCandidates?: Array<{key: string; name: string; icon?: string | null}>;
  /** 伴侣面板入口是否可用（嵌入模式关闭） */
  boardEnabled?: boolean;
  /** 当前挂着的板（null=未挂：显示两个入口钮；非空：显示板 chip） */
  attachedBoard?: {workflowKey: string; title: string; boardType: 'board' | 'html'} | null;
  boardPickerOpen?: boolean;
  boardPickerMode?: 'board' | 'html';
  boardList?: WorkflowListItem[];
  boardListLoading?: boolean;
  /** 持续精造模式是否点亮（仅管理员可见入口；开关式，点亮后持续生效） */
  sustainedArmed?: boolean;
}>();

const emit = defineEmits<{
  'update:modelValue': [val: string];
  'update:skillActiveIndex': [val: number];
  /** 对话模式弹层：选择模式 / 思考强度（父组件负责 PUT 偏好并回显） */
  selectChatMode: [mode: string];
  selectThinkingLevel: [level: string];
  send: [];
  stop: [];
  fileSelect: [files: File[]];
  removeAttachment: [id: string];
  previewAttachment: [file: AttachedFile];
  insertSkill: [skill: AgentSkill];
  input: [];
  keydown: [event: KeyboardEvent];
  paste: [event: ClipboardEvent];
  closeSkillPopup: [];
  /** 技能商店面板内数据有变更（添加/启停/上下架/删除/上传/编辑）：透传给父级刷新 @ 调用列表 */
  skillPanelChange: [];
  /** 专家面板「召唤」：添加该专家并新建绑定会话 */
  expertChat: [expertKey: string];
  /** 输入框专家位：移除驻留专家 / 无专家时从候选召唤绑定当前会话 */
  removeExpert: [];
  summonExpert: [expertKey: string];
  /** 伴侣面板：打开选板弹层（mode=板型）/ 关闭 / 挂板 / 建板 / 解除挂板 */
  openBoardPicker: [mode: 'board' | 'html'];
  closeBoardPicker: [];
  attachBoard: [wk: string];
  createBoard: [type: 'board' | 'html'];
  detachBoard: [];
  /** 到流程编排专页打开当前挂板（深链 wk+sid） */
  expandBoard: [];
  /** 持续精造模式开关切换（仅管理员） */
  toggleSustained: [];
}>();

/** 选板弹层：按当前弹层板型过滤（fetchListWorkflows 无板型参数，前端过滤） */
const filteredPickerBoards = computed(() => (props.boardList || []).filter(b => (b.boardType || 'board') === (props.boardPickerMode || 'board')));

/** 两个维度互斥：连接器维度只见 kind≠dataset，数据集维度只见 kind=dataset（底层同一套 MCP 连接器） */
const plainActiveConnectors = computed(() => (props.activeConnectors || []).filter(c => c.kind !== 'dataset'));
const activeDatasets = computed(() => (props.activeConnectors || []).filter(c => c.kind === 'dataset'));
/** 有启用中的连接器/数据集才算「点亮」→ 圆片堆外露功能条；全禁用/未添加时入口只在 + 菜单级联里 */
const hasEnabledConnectors = computed(() => plainActiveConnectors.value.some(c => c.userEnabled));
const hasEnabledDatasets = computed(() => activeDatasets.value.some(c => c.userEnabled));

// local state
const dragOver = ref(false);
const fileInputEl = ref<HTMLInputElement | null>(null);
const composerInputEl = ref<HTMLTextAreaElement | null>(null);

/* 首屏向下弹：弹层锚到 @ 光标处而不是整个输入框下缘。
   textarea 取不到行内坐标，用镜像 div 复刻其排版，量出光标 offset，再减 scrollTop 换回可视坐标 */
const popupStyle = ref<Record<string, string>>({});

const POPUP_MIRROR_PROPS = [
  'box-sizing', 'font-family', 'font-size', 'font-style', 'font-weight',
  'letter-spacing', 'line-height', 'padding', 'text-align', 'text-indent',
  'text-transform', 'white-space', 'word-break', 'word-spacing', 'width'
];

function updatePopupPos() {
  if (!props.popupDown || !props.skillPopupOpen) {
    popupStyle.value = {};
    return;
  }
  const el = composerInputEl.value;
  if (!el) return;
  const pos = el.selectionStart ?? 0;
  const cs = window.getComputedStyle(el);
  const mirror = document.createElement('div');
  for (const p of POPUP_MIRROR_PROPS) mirror.style.setProperty(p, cs.getPropertyValue(p));
  mirror.style.position = 'absolute';
  mirror.style.visibility = 'hidden';
  mirror.style.top = '0';
  mirror.style.left = '-9999px';
  mirror.style.overflow = 'hidden';
  mirror.textContent = el.value.substring(0, pos);
  const marker = document.createElement('span');
  marker.textContent = '\u200b'; // 零宽字符占位，量 offset 用
  mirror.appendChild(marker);
  document.body.appendChild(mirror);
  const caretLeft = marker.offsetLeft;
  const caretTop = marker.offsetTop;
  document.body.removeChild(mirror);

  const lineH = parseFloat(cs.lineHeight) || 20;
  // 左缘钳制：贴 @ 但不让弹层被挤得过窄（至少留 320px，不够就向左让）
  const maxLeft = Math.max(0, el.clientWidth - 320);
  const left = Math.max(-4, Math.min(caretLeft - 2, maxLeft));
  popupStyle.value = {
    left: `${left}px`,
    right: '-4px',
    bottom: 'auto',
    top: `${caretTop + lineH - el.scrollTop + 6}px`
  };
}

watch([() => props.skillPopupOpen, () => props.modelValue, () => props.popupDown], () => {
  nextTick(updatePopupPos);
});

let dragCounter = 0;

// utility functions (copied locally — no external import needed)
function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function fileExt(name: string): string {
  const ext = extractExt(name);
  return ext.length > 4 ? ext.slice(0, 4) : ext || '?';
}

function fileExtGroup(name: string): string {
  const ext = extractExt(name);
  if (['pdf'].includes(ext)) return 'pdf';
  if (['doc', 'docx', 'rtf', 'odt'].includes(ext)) return 'doc';
  if (['xls', 'xlsx', 'csv', 'tsv'].includes(ext)) return 'sheet';
  if (['ppt', 'pptx'].includes(ext)) return 'ppt';
  if (['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'bmp'].includes(ext)) return 'img';
  if (['zip', 'rar', '7z', 'tar', 'gz'].includes(ext)) return 'zip';
  if (['mp4', 'avi', 'mov', 'mkv', 'mp3', 'wav', 'flac'].includes(ext)) return 'media';
  if (['py', 'js', 'ts', 'java', 'go', 'rs', 'c', 'cpp', 'h'].includes(ext)) return 'code';
  if (['txt', 'md', 'json', 'xml', 'yaml', 'yml', 'toml'].includes(ext)) return 'text';
  return 'other';
}

// 附件预览辅助：与对话/父组件保持同一套可预览判定（md / office / video），其余文件不可预览
function isImg(name: string): boolean {
  return fileExtGroup(name) === 'img';
}

function isMarkdown(name: string): boolean {
  return ['md', 'markdown', 'mdx'].includes(extractExt(name));
}

function isOffice(name: string): boolean {
  return ['docx', 'xlsx', 'xls', 'pdf', 'pptx'].includes(extractExt(name));
}

function isVideo(name: string): boolean {
  return ['mp4', 'webm', 'mov', 'ogg', 'mkv', 'avi'].includes(extractExt(name));
}

function canPreviewFile(af: AttachedFile): boolean {
  if (af.uploading || af.error || !af.path) return false;
  return isMarkdown(af.name) || isOffice(af.name) || isVideo(af.name);
}

function previewFile(af: AttachedFile) {
  if (canPreviewFile(af)) emit('previewAttachment', af);
}

// drag handlers
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

function handleDrop(e: DragEvent) {
  e.preventDefault();
  dragCounter = 0;
  dragOver.value = false;
  if (e.dataTransfer?.files?.length) {
    emit('fileSelect', Array.from(e.dataTransfer.files));
  }
}

// file input handler
function triggerFileInput() {
  fileInputEl.value?.click();
}

function handleFileChange(e: Event) {
  const input = e.target as HTMLInputElement;
  if (input.files?.length) {
    emit('fileSelect', Array.from(input.files));
  }
  input.value = '';
}

// textarea input/keydown/paste — delegate to parent for skill popup logic
function handleInput(e: Event) {
  emit('update:modelValue', (e.target as HTMLTextAreaElement).value);
  emit('input');
  autoGrow();
}

/**
 * 输入框自动拉高：先复位到 auto 再按 scrollHeight 撑高，超过 CSS max-height（约 56vh）
 * 由浏览器截断并出现纵向滚动条。取代原「大框切换」按钮——高度纯随内容，无需手动放大。
 */
function autoGrow() {
  const el = composerInputEl.value;
  if (!el) return;
  el.style.height = 'auto';
  el.style.height = `${el.scrollHeight}px`;
  el.style.overflowY = el.scrollHeight > el.clientHeight ? 'auto' : 'hidden';
}

// 内容被父组件清空（发送后）或外部改写时，复位/重算高度
watch(
  () => props.modelValue,
  v => {
    nextTick(() => {
      if (!v) {
        const el = composerInputEl.value;
        if (el) {
          el.style.height = '';
          el.style.overflowY = '';
        }
      } else {
        autoGrow();
      }
    });
  }
);

function handleKeydown(e: KeyboardEvent) {
  emit('keydown', e);
}

function handlePaste(e: ClipboardEvent) {
  emit('paste', e);
}

function handleCloseSkillPopup() {
  setTimeout(() => emit('closeSkillPopup'), 120);
}

function handleInsertSkill(sk: AgentSkill) {
  emit('insertSkill', sk);
}

// ─────── 技能商店面板（技能库 / 连接器 双 tab） ─────────────────────────────
const skillPanelOpen = ref(false);
const skillPanelTab = ref<'expert' | 'skill' | 'connector' | 'dataset'>('skill');

function openSkillPanel(tab: 'expert' | 'skill' | 'connector' | 'dataset' = 'skill') {
  skillPanelTab.value = tab;
  skillPanelOpen.value = true;
}

/** 在光标处插入文本（自动补空格），插入后聚焦并定位光标 */
function insertAtCursor(text: string) {
  const currentValue = props.modelValue;
  const cursorPos = composerInputEl.value?.selectionStart ?? currentValue.length;
  const before = currentValue.slice(0, cursorPos);
  const needSpace = before.length > 0 && !before.endsWith(' ') && !before.endsWith('\n');
  const toInsert = (needSpace ? ' ' : '') + text;
  const after = currentValue.slice(cursorPos);
  emit('update:modelValue', before + toInsert + after);
  nextTick(() => {
    composerInputEl.value?.focus();
    const newPos = before.length + toInsert.length;
    composerInputEl.value?.setSelectionRange(newPos, newPos);
  });
}

// 技能面板「使用」/「AI 编辑」回调：插入 @key（AI 编辑为 编辑 @key）
function onPanelUse(skillKey: string) {
  insertAtCursor(`@${skillKey} `);
}

// 技能面板「到会话中创建/寻找」回调：填入起手文案并聚焦（抽屉由面板自行关闭）
function onPanelFill(text: string) {
  insertAtCursor(text);
}

// ─────── + 菜单：未点亮入口一律收进纵向弹层；点亮的控制件（板/精造/专家）外露 ───────
const plusOpen = ref(false);
/** 级联二级：专家/连接器条目悬停或点击时向菜单右侧展开子面板；关菜单或移到其它一级条目时复位 */
const plusSub = ref<'' | 'expert' | 'connector' | 'dataset'>('');

watch(plusOpen, v => {
  if (!v) plusSub.value = '';
});

function togglePlus() {
  plusOpen.value = !plusOpen.value;
}

/** + 菜单条目分发：先关菜单再触发对应动作 */
function plusPick(kind: 'attach' | 'board' | 'html' | 'sustained') {
  plusOpen.value = false;
  plusSub.value = '';
  if (kind === 'attach') triggerFileInput();
  else if (kind === 'board') emit('openBoardPicker', 'board');
  else if (kind === 'html') emit('openBoardPicker', 'html');
  else if (kind === 'sustained') emit('toggleSustained');
}

/** 级联二级开合：悬停展开；点击切换（触屏无 hover） */
function togglePlusSub(key: 'expert' | 'connector' | 'dataset') {
  plusSub.value = plusSub.value === key ? '' : key;
}

/** 二级专家候选召唤：关闭整个菜单（驻留后专家 chip 自行外露） */
function pickSubExpert(key: string) {
  plusOpen.value = false;
  plusSub.value = '';
  emit('summonExpert', key);
}

/** 二级连接器/数据集启停开关（与 ConnectorStack::toggle 同款实现）：菜单保持打开可连续拨多个 */
async function toggleSubConnector(c: AgentConnector) {
  const { data, error } = await fetchBatchAgentConnectorPrefs([c.connectorKey], { isEnabled: !c.userEnabled });
  if (!error && data) {
    c.userEnabled = !c.userEnabled;
    emit('skillPanelChange');
  } else {
    window.$message?.error('切换失败');
  }
}

/** 二级底部入口：去技能面板对应 tab（先关菜单） */
function plusSubGoShop(tab: 'expert' | 'connector' | 'dataset') {
  plusOpen.value = false;
  plusSub.value = '';
  openSkillPanel(tab);
}

// ─────── 对话模式弹层（模式 × 思考强度，所有用户可见；替代原放大钮位置） ───────
const modePopupOpen = ref(false);

/** 强度档位一句话说明（档位 = wire 值 none/low/medium/high/max，白名单由模型块 reasoning_levels 驱动） */
const LEVEL_NOTES: Record<string, string> = {
  none: '不深度思考，响应最快',
  low: '轻度思考',
  medium: '适度思考',
  high: '充分思考，攻克难题',
  max: '极限推理，不惜耗时'
};

const currentModeLabel = computed(() => {
  const cm = props.chatMode;
  if (!cm) return '均衡';
  return cm.modes.find(m => m.key === cm.mode)?.label || '均衡';
});

/** 当前模式有效块的思考强度档位白名单（配置序 = 滑块序；空 = 该模型不支持强度调节，整节隐藏） */
const currentLevels = computed(() => {
  const cm = props.chatMode;
  if (!cm) return [] as { key: string; label: string }[];
  return cm.modes.find(m => m.key === cm.mode)?.levels || [];
});

/** 滑块位置（currentLevels 索引）：拖拽全程只动本地态，松手才提交一次 */
const levelIndex = ref(0);
const lvDragging = ref(false);
const lvRailEl = ref<HTMLElement | null>(null);
/** 最近一次已上抛的档位：PUT 回包前 props 未更新，防指针事件重复触发双发 */
let lastCommittedLevel: string | null = null;

function syncLevelIndex() {
  const idx = currentLevels.value.findIndex(lv => lv.key === props.chatMode?.thinkingLevel);
  levelIndex.value = idx >= 0 ? idx : 0;
  lastCommittedLevel = null;
}

watch(() => props.chatMode, syncLevelIndex, { immediate: true, deep: true });

/** 轨道行程内缩量 = 钮半径（样张几何：posPx(i) = PAD + i*(W-2PAD)/(N-1)，拉满钮不溢出轨端） */
const LV_PAD = 15;

/** 0~1 行程比例（档位 <2 恒拉满） */
const lvFrac = computed(() => {
  const n = currentLevels.value.length;
  return n < 2 ? 1 : levelIndex.value / (n - 1);
});

/** calc 实现 PAD 内缩几何（随轨宽自适应，无需监听 resize）：fill 宽 = 2PAD + f*(W-2PAD) */
const lvFillW = computed(() => `calc(${2 * LV_PAD}px + ${lvFrac.value} * (100% - ${2 * LV_PAD}px))`);
const lvKnobLeft = computed(() => `calc(${LV_PAD}px + ${lvFrac.value} * (100% - ${2 * LV_PAD}px))`);

function lvDotPos(i: number) {
  const n = currentLevels.value.length;
  const f = n < 2 ? 1 : i / (n - 1);
  return `calc(${LV_PAD}px + ${f} * (100% - ${2 * LV_PAD}px))`;
}

/** 最高档：星夜带 + 烟花态 */
const lvAtMax = computed(() => currentLevels.value.length > 0 && levelIndex.value >= currentLevels.value.length - 1);

const lvRand = (a: number, b: number) => a + Math.random() * (b - a);

/** S2 星夜带：20 颗全参数随机星（挂载时生成一次，弹层每次打开一致；拉满时随 fill 亮起） */
const LV_STARS: Record<string, string>[] = Array.from({ length: 20 }, () => {
  const size = lvRand(1, 2.5).toFixed(2);
  return {
    left: `${lvRand(2, 97).toFixed(2)}%`,
    top: `${lvRand(18, 82).toFixed(2)}%`,
    width: `${size}px`,
    height: `${size}px`,
    '--sc': Math.random() < 0.7 ? 'rgba(255,255,255,0.95)' : 'rgba(233,213,255,0.9)',
    '--td': `${lvRand(1.1, 3).toFixed(2)}s`,
    '--tl': `-${lvRand(0, 3).toFixed(2)}s`,
    '--dd': `${lvRand(3, 7).toFixed(2)}s`,
    '--dx': `${lvRand(-5, 5).toFixed(1)}px`,
    '--dy': `${lvRand(-3, 3).toFixed(1)}px`
  };
});

const FW_COLORS = ['#a78bfa', '#c084fc', '#8b5cf6', '#e9d5ff', '#ffffff'];
const burstParts = ref<Record<string, string>[]>([]);
const burstKey = ref(0);
let burstTimer: ReturnType<typeof setTimeout> | null = null;

/** 进入拉满瞬间：钮右侧绽开一小朵紫色烟花（12 粒右偏扇形 ±72° + 扩散细环，800ms 自清理） */
function fireBurst() {
  if (burstTimer !== null) clearTimeout(burstTimer);
  burstKey.value += 1; // 换 key 强制重建节点，动画从头播
  burstParts.value = Array.from({ length: 12 }, (_, i) => {
    const ang = lvRand(-1.25, 1.25);
    const dist = lvRand(9, 17);
    const size = lvRand(2, 3.2).toFixed(1);
    const c = FW_COLORS[i % FW_COLORS.length];
    return {
      width: `${size}px`,
      height: `${size}px`,
      background: c,
      'box-shadow': `0 0 6px ${c}`,
      '--fx': `${(Math.cos(ang) * dist).toFixed(1)}px`,
      '--fy': `${(Math.sin(ang) * dist).toFixed(1)}px`,
      'animation-delay': `${lvRand(0, 0.08).toFixed(2)}s`
    };
  });
  burstTimer = setTimeout(() => {
    burstParts.value = [];
    burstTimer = null;
  }, 800);
}

watch(lvAtMax, atMax => {
  if (atMax) {
    fireBurst();
  } else if (burstTimer !== null) {
    clearTimeout(burstTimer);
    burstTimer = null;
    burstParts.value = [];
  }
});

function commitLevel() {
  const key = currentLevels.value[levelIndex.value]?.key;
  if (!key || key === props.chatMode?.thinkingLevel || key === lastCommittedLevel) return;
  lastCommittedLevel = key;
  emit('selectThinkingLevel', key);
}

/** 指针位置 → 档位：按 PAD 内缩行程取最近档（越过两点中点即跳到下一点） */
function levelIndexFromX(clientX: number) {
  const rail = lvRailEl.value;
  const n = currentLevels.value.length;
  if (!rail || n < 2) return Math.max(0, n - 1);
  const r = rail.getBoundingClientRect();
  const x = Math.min(r.width - LV_PAD, Math.max(LV_PAD, clientX - r.left));
  const ratio = (x - LV_PAD) / Math.max(1, r.width - 2 * LV_PAD);
  return Math.min(n - 1, Math.max(0, Math.round(ratio * (n - 1))));
}

function onLvMove(e: PointerEvent) {
  if (!lvDragging.value) return;
  levelIndex.value = levelIndexFromX(e.clientX);
}

function onLvUp() {
  if (!lvDragging.value) return;
  lvDragging.value = false;
  window.removeEventListener('pointermove', onLvMove);
  window.removeEventListener('pointerup', onLvUp);
  commitLevel();
}

/** 轨道按下即开始拖拽：拖动中零请求，松手统一提交一次 */
function onLvDown(e: PointerEvent) {
  if (!currentLevels.value.length) return;
  e.preventDefault();
  lvDragging.value = true;
  levelIndex.value = levelIndexFromX(e.clientX);
  window.addEventListener('pointermove', onLvMove);
  window.addEventListener('pointerup', onLvUp);
}

function abortLvDrag() {
  lvDragging.value = false;
  window.removeEventListener('pointermove', onLvMove);
  window.removeEventListener('pointerup', onLvUp);
}

/** 键盘可达性（轨道聚焦后）：方向键逐档、Home/End 跳两端，每步提交一次 */
function onLvKey(e: KeyboardEvent) {
  const n = currentLevels.value.length;
  if (n < 2) return;
  let idx = levelIndex.value;
  if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') idx -= 1;
  else if (e.key === 'ArrowRight' || e.key === 'ArrowUp') idx += 1;
  else if (e.key === 'Home') idx = 0;
  else if (e.key === 'End') idx = n - 1;
  else return;
  e.preventDefault();
  levelIndex.value = Math.min(n - 1, Math.max(0, idx));
  commitLevel();
}

/** 关弹层：拖拽未松手就关闭时按当前位置兜底提交（dedup 保证不双发） */
function closeModePopup() {
  abortLvDrag();
  commitLevel();
  modePopupOpen.value = false;
}

function toggleModePopup() {
  if (modePopupOpen.value) {
    closeModePopup();
    return;
  }
  syncLevelIndex();
  modePopupOpen.value = true;
}

/** 选中不关弹层（点 backdrop 空白处才关）：用户切完模式还能接着调强度滑块；
    档位由后端按新块白名单平滑迁移（父组件重拉 GET），拖拽中的位置不提交避免竞态 */
function pickMode(key: string) {
  abortLvDrag();
  lastCommittedLevel = null;
  if (key !== props.chatMode?.mode) emit('selectChatMode', key);
}

// expose focus so parent can focus the textarea
defineExpose({
  focus() {
    composerInputEl.value?.focus();
  },
  // 技能面板入口已移到侧栏（QASidebar「技能商店」），由父组件代为打开
  openSkillPanel,
  setCaretPos(pos: number) {
    composerInputEl.value?.focus();
    composerInputEl.value?.setSelectionRange(pos, pos);
  },
  resetHeight() {
    // 清 inline 高度后按当前内容重算：checkResponsive 在桌面↔手机切换时调用，
    // 长内容切换后立即得到正确高度（只清不重算会塌回 min-height）
    const el = composerInputEl.value;
    if (el) {
      el.style.height = '';
      el.style.overflowY = '';
    }
    nextTick(autoGrow);
  },
  get selectionStart() {
    return composerInputEl.value?.selectionStart ?? null;
  }
});
</script>

<template>
  <footer
    :class="{ 'is-mini': mini }"
    class="qa-composer"
  >
    <!-- 附件条（独立于输入框之上）：图片回显缩略图（点击放大），文件可预览则整块可点 -->
    <div v-if="attachedFiles.length" class="attached-bar">
      <template v-for="af in attachedFiles" :key="af.id">
        <!-- 图片：本地 object URL 即时回显，n-image 点击放大（与对话同组件） -->
        <div v-if="isImg(af.name)" class="attached-item attached-item--img">
          <div class="af-thumb">
            <NImage
              :src="af.previewUrl || ''"
              :alt="af.name"
              object-fit="cover"
              :img-props="{ class: 'af-thumb-img', loading: 'lazy' }"
            />
            <div v-if="af.uploading" class="af-thumb-mask">
              <span class="af-thumb-spin" />
              <span class="af-thumb-pct">{{ Math.round(af.progress) }}%</span>
            </div>
            <div v-else-if="af.error" class="af-thumb-mask af-thumb-mask--err" :title="af.error">
              <span class="af-thumb-err">!</span>
            </div>
          </div>
          <button class="af-thumb-remove" :aria-label="`移除 ${af.name}`" :title="`移除 ${af.name}`" @click.stop="emit('removeAttachment', af.id)"></button>
        </div>

        <!-- 文件：可预览时主体为按钮（点击走父组件同一套预览），右上角移除 -->
        <div v-else class="attached-item af-file" :class="[{ 'has-error': af.error }]">
          <button
            v-if="canPreviewFile(af)"
            type="button"
            class="af-file-main"
            :disabled="af.uploading"
            :title="`预览 ${af.name}`"
            @click="previewFile(af)"
          >
            <span :class="'af-ext-' + fileExtGroup(af.name)" class="af-ext">{{ fileExt(af.name) }}</span>
            <span :title="af.name" class="af-name">{{ af.name }}</span>
            <span v-if="af.uploading" class="af-progress-bar">
              <span :style="{ width: af.progress + '%' }" class="af-progress-fill" />
            </span>
            <span v-else-if="af.error" :title="af.error" class="af-error">失败</span>
            <span v-else class="af-preview-hint">预览</span>
          </button>
          <span v-else class="af-file-main">
            <span :class="'af-ext-' + fileExtGroup(af.name)" class="af-ext">{{ fileExt(af.name) }}</span>
            <span :title="af.name" class="af-name">{{ af.name }}</span>
            <span v-if="af.uploading" class="af-progress-bar">
              <span :style="{ width: af.progress + '%' }" class="af-progress-fill" />
            </span>
            <span v-else-if="af.error" :title="af.error" class="af-error">失败</span>
            <span v-else class="af-size">{{ formatFileSize(af.size) }}</span>
          </span>
          <button class="af-remove" :title="`移除 ${af.name}`" @click="emit('removeAttachment', af.id)">×</button>
        </div>
      </template>
    </div>

    <div
      :class="{ 'is-running': running, 'is-dragover': dragOver }"
      class="composer-frame"
      @dragleave="handleDragLeave"
      @dragover="handleDragOver"
      @drop="handleDrop"
    >
      <div v-if="dragOver" class="drag-overlay">
        <span class="drag-icon">↥</span>
        <span class="drag-text">松开上传文件</span>
      </div>
      <div class="composer-input-row">
        <span class="composer-prompt">›</span>
        <div class="composer-input-wrap">
          <textarea
            ref="composerInputEl"
            :value="modelValue"
            :rows="isMobile ? 1 : mini ? 2 : 4"
            class="composer-input"
            :enterkeyhint="isMobile ? 'enter' : undefined"
            :placeholder="running
              ? '可继续输入下一个问题，待当前回复结束后发送…'
              : mini
                ? '告诉 agent 怎么改… Enter 发送，可拖入 / 粘贴附件'
                : (isMobile ? '输入 @ 调用技能' : '输入 @ 调用技能')"
            @input="handleInput"
            @keydown="handleKeydown"
            @paste="handlePaste"
            @blur="handleCloseSkillPopup"
          />

          <!-- @ 调用弹层：@技能名 加载技能（召唤助理走输入框专家位，不在 @ 手势里） -->
          <div v-if="skillPopupOpen && filteredSkills.length" class="skill-popup" :class="{ 'skill-popup--down': popupDown }" :style="popupStyle">
            <div class="skill-popup-head">
              <span class="sp-icon">@</span>
              <span class="sp-label">调&nbsp;用</span>
              <span class="sp-line" />
              <span class="sp-count">{{ filteredSkills.length }}</span>
            </div>
            <ul class="skill-list">
              <li
                v-for="(sk, i) in filteredSkills"
                :key="sk.skillKey"
                :class="{ active: i === skillActiveIndex }"
                class="skill-item"
                @mousedown.prevent="handleInsertSkill(sk)"
                @mouseenter="emit('update:skillActiveIndex', i)"
              >
                <!--
 显示即调用标识（skillKey）：插入与后端匹配都按 key，可见文本只有 @key + 描述，
                   中文名退到 tooltip（name≠key 时提示「中文名｜调用标识 @key」），避免显示与落框不一致
-->
                <!--
 竖排两行：技能名与介绍都可能超长（key 有长名_专属code、描述有几百字的），
                   横排必然互相挤——各占一行独立截断，条目高度恒定，弹层不再变形；全文悬停 title 可见
-->
                <span class="sk-icon" v-html="skillIconHtml(sk.icon)" />
                <span class="sk-body">
                  <span class="sk-line">
                    <span class="sk-name" :title="sk.name !== sk.skillKey ? `${sk.name}｜调用标识 @${sk.skillKey}` : `@${sk.skillKey}`">@{{ sk.skillKey }}</span>
                    <!-- 唯一标签：本人创建的技能显示「我的」（来源/作者徽章已去掉） -->
                    <span v-if="isMySkill(sk)" class="sk-src sk-src--mine" title="我创建的技能">我的</span>
                  </span>
                  <span class="sk-desc" :title="sk.description || ''">{{ sk.description || '' }}</span>
                </span>
              </li>
            </ul>
            <div class="skill-popup-foot">
              <span>@技能名 加载技能</span>
              <span>↑↓ 选择 · Enter 插入 · Esc 关闭</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 功能条：左侧 + 菜单（未点亮入口全收这里）+ 已点亮控制件，右侧字数/放大/发送 -->
      <div class="composer-actions">
        <div class="composer-actions-left">
          <!-- mini：保留原附件钮（嵌入形态不走 + 菜单） -->
          <button v-if="mini" :disabled="running" class="btn-attach" title="上传附件" @click="triggerFileInput">
            <svg class="attach-clip" width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M14.29 7.37l-6.13 6.13a4 4 0 0 1-5.66-5.66l5.71-5.71A2.67 2.67 0 1 1 12 5.89l-5.73 5.71a1.33 1.33 0 0 1-1.89-1.89l5.66-5.65" />
            </svg>
          </button>

          <!-- + 菜单：未点亮入口收进纵向弹层；点亮的才外露（板 chip / 精造 / 专家 / 连接器） -->
          <div v-else class="plus-wrap">
            <button class="btn-plus" :class="{ open: plusOpen }" title="添加：附件 / 挂板 / 专家 / 连接器等" @click="togglePlus">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true">
                <path d="M8 3v10M3 8h10" />
              </svg>
            </button>
            <template v-if="plusOpen">
              <div class="plus-backdrop" @click="plusOpen = false" />
              <div class="plus-menu">
                <button class="plus-item" @mouseenter="plusSub = ''" @click="plusPick('attach')">
                  <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <path d="M14.29 7.37l-6.13 6.13a4 4 0 0 1-5.66-5.66l5.71-5.71A2.67 2.67 0 1 1 12 5.89l-5.73 5.71a1.33 1.33 0 0 1-1.89-1.89l5.66-5.65" />
                  </svg>
                  <span>附件上传</span>
                </button>
                <!-- 分割线：附件 / 板与模式 / 专家与连接器 三类；中段条目全被门控隐藏时（嵌入+非管理员/手机端）不画 -->
                <div v-if="(boardEnabled || isAdmin) && !isMobile" class="plus-divider" />
                <button v-if="boardEnabled && !isMobile" class="plus-item" @mouseenter="plusSub = ''" @click="plusPick('board')">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <path d="M12 3l7 4v10l-7 4-7-4V7z" />
                    <path d="M12 12l7-4M12 12v9M12 12L5 8" />
                  </svg>
                  <span>流程编排</span>
                </button>
                <button v-if="boardEnabled && !isMobile" class="plus-item" @mouseenter="plusSub = ''" @click="plusPick('html')">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <rect x="3" y="4" width="18" height="14" rx="2" />
                    <path d="M3 8h18M7 21h10M12 18v3" />
                  </svg>
                  <span>应用制作</span>
                </button>
                <button v-if="isAdmin && !isMobile" class="plus-item" :class="{ active: sustainedArmed }" @mouseenter="plusSub = ''" @click="plusPick('sustained')">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <path d="M21 12a9 9 0 1 1-2.64-6.36" />
                    <path d="M21 3v6h-6" />
                    <path d="M12 8v4l2.5 2.5" />
                  </svg>
                  <span>持续精造</span>
                </button>
                <div class="plus-divider" />
                <!-- 级联条目：悬停/点击向菜单右侧唤出二级子面板（专家=召唤候选，连接器/数据集=启停开关） -->
                <button class="plus-item plus-item--cascade" :class="{ sub: plusSub === 'expert' }" @mouseenter="plusSub = 'expert'" @click="togglePlusSub('expert')">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <circle cx="12" cy="8" r="4" />
                    <path d="M4 21c0-4 3.6-6.5 8-6.5s8 2.5 8 6.5" />
                  </svg>
                  <span>{{ expertLabel }}</span>
                  <span class="plus-caret" aria-hidden="true">›</span>
                </button>
                <button class="plus-item plus-item--cascade" :class="{ sub: plusSub === 'connector' }" @mouseenter="plusSub = 'connector'" @click="togglePlusSub('connector')">
                  <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <path d="M6 2v3M10 2v3" />
                    <path d="M4 5h8v2.5a4 4 0 0 1-4 4 4 4 0 0 1-4-4V5z" />
                    <path d="M8 11.5V14" />
                  </svg>
                  <span>连接器</span>
                  <span class="plus-caret" aria-hidden="true">›</span>
                </button>
                <button class="plus-item plus-item--cascade" :class="{ sub: plusSub === 'dataset' }" @mouseenter="plusSub = 'dataset'" @click="togglePlusSub('dataset')">
                  <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <ellipse cx="8" cy="4" rx="5.5" ry="2.1" />
                    <path d="M2.5 4v8c0 1.2 2.5 2.1 5.5 2.1s5.5-.9 5.5-2.1V4" />
                    <path d="M2.5 8c0 1.2 2.5 2.1 5.5 2.1s5.5-.9 5.5-2.1" />
                  </svg>
                  <span>数据集</span>
                  <span class="plus-caret" aria-hidden="true">›</span>
                </button>

                <!-- 级联二级：专家（召唤候选，点了绑定当前任务） -->
                <div v-if="plusSub === 'expert'" class="plus-sub">
                  <div class="plus-sub-card">
                    <div class="plus-sub-head">召唤{{ expertLabel }}<span>绑定当前任务</span></div>
                    <div class="plus-sub-list">
                      <button v-for="c in expertCandidates || []" :key="c.key" class="plus-sub-item" @click="pickSubExpert(c.key)">
                        <span class="plus-sub-icon">
                          <SvgIcon :icon="c.icon || 'mdi:account-tie-outline'" />
                        </span>
                        <span class="plus-sub-name" :title="c.name">{{ c.name }}</span>
                      </button>
                      <div v-if="!(expertCandidates || []).length" class="plus-sub-empty">还没有添加{{ expertLabel }}，点击下方按钮添加</div>
                    </div>
                    <button class="plus-sub-foot" @click="plusSubGoShop('expert')">召唤更多{{ expertLabel }}</button>
                  </div>
                </div>

                <!-- 级联二级：连接器（启停开关，启用后任务中自动加载其工具；kind=dataset 归数据集维度不在此列） -->
                <div v-if="plusSub === 'connector'" class="plus-sub">
                  <div class="plus-sub-card">
                    <div class="plus-sub-head">连接器<span>启用后任务中自动加载其工具</span></div>
                    <div class="plus-sub-list">
                      <div v-for="c in plainActiveConnectors" :key="c.connectorKey" class="plus-sub-item plus-sub-item--switch">
                        <span class="plus-sub-icon">
                          <span v-if="customIconHtml(c.icon)" v-html="customIconHtml(c.icon)" />
                          <svg v-else width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                            <path d="M6 2v3M10 2v3" />
                            <path d="M4 5h8v2.5a4 4 0 0 1-4 4 4 4 0 0 1-4-4V5z" />
                            <path d="M8 11.5V14" />
                          </svg>
                        </span>
                        <span class="plus-sub-name" :title="c.name">{{ c.name }}</span>
                        <NSwitch :value="c.userEnabled" size="small" @update:value="() => toggleSubConnector(c)" />
                      </div>
                      <div v-if="!plainActiveConnectors.length" class="plus-sub-empty">还没有添加连接器，点击下方按钮添加</div>
                    </div>
                    <button class="plus-sub-foot" @click="plusSubGoShop('connector')">{{ plainActiveConnectors.length ? '管理连接器' : '添加连接器' }}</button>
                  </div>
                </div>

                <!-- 级联二级：数据集（kind=dataset 的 MCP 连接器，独立维度；启停与连接器同款） -->
                <div v-if="plusSub === 'dataset'" class="plus-sub">
                  <div class="plus-sub-card">
                    <div class="plus-sub-head">数据集<span>启用后为任务注入相关知识数据</span></div>
                    <div class="plus-sub-list">
                      <div v-for="c in activeDatasets" :key="c.connectorKey" class="plus-sub-item plus-sub-item--switch">
                        <span class="plus-sub-icon">
                          <span v-if="customIconHtml(c.icon)" v-html="customIconHtml(c.icon)" />
                          <svg v-else width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                            <ellipse cx="8" cy="4" rx="5.5" ry="2.1" />
                            <path d="M2.5 4v8c0 1.2 2.5 2.1 5.5 2.1s5.5-.9 5.5-2.1V4" />
                            <path d="M2.5 8c0 1.2 2.5 2.1 5.5 2.1s5.5-.9 5.5-2.1" />
                          </svg>
                        </span>
                        <span class="plus-sub-name" :title="c.name">{{ c.name }}</span>
                        <NSwitch :value="c.userEnabled" size="small" @update:value="() => toggleSubConnector(c)" />
                      </div>
                      <div v-if="!activeDatasets.length" class="plus-sub-empty">还没有添加数据集，点击下方按钮添加</div>
                    </div>
                    <button class="plus-sub-foot" @click="plusSubGoShop('dataset')">{{ activeDatasets.length ? '管理数据集' : '添加数据集' }}</button>
                  </div>
                </div>
              </div>
            </template>

            <!-- 选板弹层：锚在 + 处（经 + 菜单或板 chip 打开）；结构/令牌照 skill-popup -->
            <template v-if="boardPickerOpen">
              <div class="board-popup-backdrop" @click="emit('closeBoardPicker')" />
              <div class="board-popup">
                <div class="board-popup-head">
                  <span class="bp-icon">{{ boardPickerMode === 'html' ? '▦' : '◫' }}</span>
                  <span>{{ boardPickerMode === 'html' ? '应用制作' : '流程编排' }}</span>
                  <span class="bp-line" />
                  <span class="bp-hint">挂一块板到任务</span>
                </div>
                <button class="board-popup-new" @click="emit('createBoard', boardPickerMode || 'board')">
                  ＋ 新建{{ boardPickerMode === 'html' ? '应用制作' : '流程编排' }}
                </button>
                <div class="board-popup-list">
                  <div v-if="boardListLoading" class="board-popup-empty">加载中…</div>
                  <template v-else>
                    <button
                      v-for="b in filteredPickerBoards"
                      :key="b.workflowKey"
                      class="board-popup-item"
                      :class="{cur: attachedBoard && b.workflowKey === attachedBoard.workflowKey}"
                      :title="b.title"
                      @click="emit('attachBoard', b.workflowKey)"
                    >
                      <span class="bpi-name">{{ b.title || '未命名' }}</span>
                      <span class="bpi-meta">{{ b.version || 0 }} 次编辑</span>
                    </button>
                    <div v-if="!filteredPickerBoards.length" class="board-popup-empty">暂无该型板，点上方新建</div>
                  </template>
                </div>
                <button v-if="attachedBoard" class="board-popup-detach" @click="emit('detachBoard')">解除当前挂板</button>
                <button v-if="attachedBoard" class="board-popup-expand" @click="emit('expandBoard')">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 4h6v6" /><path d="M20 4l-7 7" /><path d="M10 20H4v-6" /><path d="M4 20l7-7" /></svg>
                  到专页打开（完整画布 + 任务库）
                </button>
              </div>
            </template>
          </div>

          <input ref="fileInputEl" hidden multiple type="file" @change="handleFileChange" />

          <!-- 已点亮控制件：挂上/点亮/驻留后才外露，与 + 菜单并存 -->
          <span v-if="!mini && boardEnabled && attachedBoard" class="board-chip-slot">
            <button
              class="btn-board chip"
              :class="{'chip-html': attachedBoard.boardType === 'html'}"
              :title="`已挂板：${attachedBoard.title || (attachedBoard.boardType === 'html' ? '应用制作' : '流程编排')}（点击换板）`"
              @click="emit('openBoardPicker', attachedBoard.boardType)"
            >
              <svg v-if="attachedBoard.boardType === 'html'" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
                <rect x="3" y="4" width="18" height="14" rx="2" />
                <path d="M3 8h18M7 21h10M12 18v3" />
              </svg>
              <svg v-else width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
                <path d="M12 3l7 4v10l-7 4-7-4V7z" />
                <path d="M12 12l7-4M12 12v9M12 12L5 8" />
              </svg>
              <span class="bb-label">{{ attachedBoard.title || (attachedBoard.boardType === 'html' ? '应用制作' : '流程编排') }}</span>
            </button>
            <!-- 叉在图标位（悬停 chip 才盖住图标显形），与专家 chip 同款交互；仅点叉才解除挂板 -->
            <button class="btn-board-x" title="解除挂板（收起面板）" @click="emit('detachBoard')">
              <svg width="9" height="9" viewBox="0 0 16 16" fill="none" stroke="currentColor" aria-hidden="true">
                <path d="M4 4l8 8M12 4l-8 8" stroke-width="1.8" stroke-linecap="round" />
              </svg>
            </button>
          </span>

          <!-- 持续精造点亮态（仅管理员）：入口收进 + 菜单，点亮后外露，点击关闭 -->
          <button
            v-if="isAdmin && !mini && !isMobile && sustainedArmed"
            class="btn-board btn-sustained active"
            title="持续精造已开启：复杂创作任务将多轮自我精进直到拿得出手（点击关闭）"
            @click="emit('toggleSustained')"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M21 12a9 9 0 1 1-2.64-6.36" />
              <path d="M21 3v6h-6" />
              <path d="M12 8v4l2.5 2.5" />
            </svg>
            <span>持续精造</span>
          </button>

          <!-- 会话驻留专家：驻留时才外露 chip（图标+全名，仅点叉移除）；召唤入口在 + 菜单 -->
          <ExpertChip
            v-if="!mini && sessionExpert"
            :expert="sessionExpert"
            :candidates="expertCandidates || []"
            @remove="emit('removeExpert')"
            @summon="k => emit('summonExpert', k)"
            @open-shop="openSkillPanel('expert')"
          />

          <!-- 在用连接器：有启用中的才外露圆片堆（kind≠dataset）；全禁用/未添加时入口只在 + 菜单级联里 -->
          <ConnectorStack
            v-if="!mini && hasEnabledConnectors"
            :connectors="plainActiveConnectors"
            @changed="emit('skillPanelChange')"
            @manage="openSkillPanel('connector')"
          />

          <!-- 在用数据集：kind=dataset 独立维度，同款圆片堆；管理入口直达面板数据集 tab -->
          <ConnectorStack
            v-if="!mini && hasEnabledDatasets"
            variant="dataset"
            :connectors="activeDatasets"
            @changed="emit('skillPanelChange')"
            @manage="openSkillPanel('dataset')"
          />
        </div>
        <div class="composer-side">
          <span v-if="modelValue.length > 0" class="char-count">{{ modelValue.length }}</span>
          <!-- 对话模式切换（原放大钮位置；所有用户可见，mini 嵌入形态不显示） -->
          <div v-if="!mini" class="mode-wrap">
            <button
              class="btn-mode"
              :class="{ open: modePopupOpen }"
              :title="`对话模式：${currentModeLabel}（点击切换模式与思考强度）`"
              @click="toggleModePopup"
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M13 2L3 14h7l-1 8 10-12h-7z" />
              </svg>
              <span>{{ currentModeLabel }}</span>
            </button>
            <template v-if="modePopupOpen">
              <div class="mode-popup-backdrop" @click="closeModePopup" />
              <div
                class="mode-popup"
                :class="{ 'mode-popup--down': popupDown }"
              >
                <div class="mode-popup-group">
                  <button
                    v-for="m in chatMode?.modes || []"
                    :key="m.key"
                    class="mode-item"
                    :class="{ cur: chatMode?.mode === m.key }"
                    @click="pickMode(m.key)"
                  >
                    <span class="mi-name">{{ m.label }}</span>
                    <!-- 模型名仅超管可见（后端脱敏），普通用户展示模式说明（DB 驱动 note） -->
                    <span class="mi-meta" :title="m.blockLabel || m.note || ''">{{ m.blockLabel || m.note || '' }}</span>
                    <span v-if="chatMode?.mode === m.key" class="mi-check">
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                        <path d="M4.5 12.5l5 5 10-11" />
                      </svg>
                    </span>
                  </button>
                </div>
                <!--
                  思考强度节：档位白名单由模型块驱动（当前模式有效块），未配置的模型整节隐藏。
                  自绘粗轨滑块（样张规格：A 骨架轨24/钮30 + 轨内淡刻度点 + 拉满星夜带/烟花）：
                  拖拽全程只动本地态、过中点跳档，松手才提交一次 PUT
                -->
                <template v-if="currentLevels.length">
                  <div class="mode-popup-sep" />
                  <div class="lv-section" :class="{ 'lv-max': lvAtMax }">
                    <div class="lv-head">
                      <span class="lv-title">思考强度</span>
                      <span class="lv-cur">{{ currentLevels[levelIndex]?.label || '' }}</span>
                    </div>
                    <div class="lv-zone">
                      <div
                        ref="lvRailEl"
                        class="lv-rail"
                        :class="{ 'is-drag': lvDragging }"
                        role="slider"
                        :aria-valuemin="0"
                        :aria-valuemax="currentLevels.length - 1"
                        :aria-valuenow="levelIndex"
                        :aria-valuetext="currentLevels[levelIndex]?.label || ''"
                        tabindex="0"
                        @pointerdown="onLvDown"
                        @keydown="onLvKey"
                      >
                        <div class="lv-fill" :style="{ width: lvFillW }">
                          <div class="lv-stars">
                            <i v-for="(s, si) in LV_STARS" :key="si" class="lv-star" :style="s" />
                          </div>
                        </div>
                        <div class="lv-dots">
                          <span
                            v-for="(lv, i) in currentLevels"
                            :key="lv.key"
                            class="lv-dot"
                            :class="{ on: i <= levelIndex }"
                            :style="{ left: lvDotPos(i) }"
                          />
                        </div>
                        <div class="lv-handle" :style="{ left: lvKnobLeft }" />
                      </div>
                      <!-- 拉满烟花：进入最高档瞬间在钮右侧绽开一次（12 粒扇形星屑 + 扩散细环，自清理） -->
                      <div v-if="burstParts.length" :key="burstKey" class="lv-burst">
                        <i class="lv-fw-ring" />
                        <i v-for="(p, pi) in burstParts" :key="pi" class="lv-fw-p" :style="p" />
                      </div>
                    </div>
                    <div class="lv-note">{{ LEVEL_NOTES[currentLevels[levelIndex]?.key || ''] || '' }}</div>
                  </div>
                </template>
              </div>
            </template>
          </div>
          <button v-if="running" class="btn-stop" @click="emit('stop')">停止</button>
          <button
            v-else
            :disabled="!modelValue.trim() && !attachedFiles.some(f => f.path && !f.error)"
            class="btn-send"
            @click="emit('send')"
          >
            <span>发送</span>
            <span class="btn-arrow">→</span>
          </button>
        </div>
      </div>
    </div>

    <div v-if="!mini" class="composer-foot">
      AI · 生成结果 · 请核对关键数据
      <BeianInfo />
    </div>

    <!-- 技能商店面板（右侧宽抽屉：商店本体，右上角入口进「我的技能/上架管理」子页面，自带 Teleport） -->
    <SkillPanel v-model:show="skillPanelOpen" :initial-tab="skillPanelTab" @use="onPanelUse" @fill="onPanelFill" @expert-chat="k => emit('expertChat', k)" @change="emit('skillPanelChange')" />
  </footer>
</template>

<style scoped>
.qa-composer {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  border-top: none;
  padding: 8px 32px 16px;
  /* 镂空背景：整条去掉底色与毛玻璃，页面背景直接透出 */
  background: transparent;
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
}

/* 输入框高度纯随内容自动拉高（autoGrow），上限约 56vh（原「大框」规模），
   超过上限由 overflow-y 内滚——放大/收起按钮已删除 */

.attached-bar,
.composer-foot {
  flex-shrink: 0;
}

/* 纵向 flex 列里 auto 外边距会关掉 stretch、子项收缩成内容宽——
   显式给满宽，max-width + margin:auto 继续负责 820 居中 */
.attached-bar,
.composer-frame,
.composer-foot {
  width: 100%;
}

.composer-frame {
  position: relative;
  /* 纵向布局：上面输入区，下面功能条（左附件/挂板入口，右字数/放大/发送） */
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-width: 880px;
  margin: 0 auto;
  border: 1px solid var(--border, var(--card-border));
  background: var(--card-bg);
  padding: 12px 16px 10px;
  border-radius: 22px;
  /* 只过渡颜色类属性：尺寸/弹性在大小框切换时必须瞬时完成，
     all 会让 flex-grow 参与插值，产生"闪现后缓慢铺开"的撕裂感 */
  transition: border-color 0.2s ease, background-color 0.2s ease, box-shadow 0.2s ease;
  box-shadow: var(--input-shadow, var(--card-shadow));
}

/* 输入行：提示符 + 输入框 */
.composer-input-row {
  display: flex;
  align-items: flex-start;
  width: 100%;
}

.composer-input-row .composer-input-wrap {
  flex: 1;
  min-width: 0;
}

/* 功能条：左附件/挂板，右字数/放大/发送 */
.composer-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
}

.composer-actions-left {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}

/* 功能条按钮统一语言：图标+文字胶囊，与顶栏安静灰阶同源 */
.composer-actions-left .btn-attach,
.composer-actions-left .btn-board {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 30px;
  padding: 0 10px;
  border: none;
  border-radius: 999px;
  background: transparent;
  color: var(--ink-3);
  font-family: var(--font-body);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0.005em;
  cursor: pointer;
  white-space: nowrap;
  flex-shrink: 0;
  transition: background 0.15s ease, color 0.15s ease;
}

.composer-actions-left .btn-attach:hover:not(:disabled),
.composer-actions-left .btn-board:hover {
  background: var(--fill-hover);
  color: var(--ink);
}

.composer-actions-left .btn-attach:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

/* 附件钮是纯图标（无文字）：收成 30px 圆钮，和板钮同高一族 */
.composer-actions-left .btn-attach {
  width: 30px;
  padding: 0;
  justify-content: center;
}

.composer-frame:focus-within {
  border-color: var(--border-strong);
  background: var(--paper-soft, #fff);
  box-shadow: var(--shadow-md);
}

.composer-frame.is-running {
  border-color: var(--border-strong);
  background: var(--fill-hover);
}

.composer-frame.is-dragover {
  border-color: var(--border-strong);
  background: var(--fill-hover);
  border-radius: 22px;
}

/* 样张无 › 前缀 */
.composer-prompt {
  display: none;
}

.composer-input {
  flex: 1;
  /* 不要右下角 resize 斜杠（拖手柄视觉噪音；高度由 autoGrow 随内容自动拉高） */
  resize: none;
  border: none;
  outline: none;
  background: transparent;
  font-family: var(--font-body);
  font-size: 15px;
  line-height: 1.6;
  color: var(--ink);
  /* 整体加高：默认 4 行起步；上限约 56vh（原大框规模），超过内滚 */
  min-height: 96px;
  max-height: 56vh;
  overflow-y: hidden;
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

/* 圆形发送钮（Kimi 语言）：玻璃 = 极光实底圆 / kimi = 墨黑圆；空态 --send-off */
.btn-send {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  padding: 0;
  border: none;
  cursor: pointer;
  border-radius: 50%;
  transition: all 0.2s ease;
  background: var(--grad-brand);
  color: #fff;
  box-shadow: none;
}

.btn-send > span:not(.btn-arrow) {
  display: none;
}

.btn-send .btn-arrow {
  transform: rotate(-90deg);
}

.btn-send:hover:not(:disabled) {
  filter: brightness(1.06);
}

.btn-send:hover:not(:disabled) .btn-arrow {
  transform: rotate(-90deg);
}

.btn-send:disabled {
  background: var(--send-off);
  color: #fff;
  box-shadow: none;
  cursor: not-allowed;
}

.btn-arrow {
  font-family: var(--font-body);
  font-size: 16px;
  font-weight: 700;
  transition: transform 0.2s;
}

.btn-stop {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  border: 1px solid var(--card-border);
  cursor: pointer;
  font-family: var(--font-body);
  font-size: 12.5px;
  font-weight: 600;
  letter-spacing: 0.005em;
  border-radius: 999px;
  transition: all 0.16s ease;
  text-transform: none;
  background: var(--card-bg);
  color: var(--ink-2);
  box-shadow: none;
}

.btn-stop:hover {
  background: var(--grad-brand);
  color: var(--on-primary);
  border-color: transparent;
  box-shadow: none;
}

/* 对话模式切换钮（原放大钮位置）：功能条灰阶胶囊同族语言。
   ⚠️ 新 class btn-mode，绝不加进任何手机端隐藏/圆角改写列表——所有用户可见是硬要求 */
.mode-wrap {
  position: relative;
  flex-shrink: 0;
}

.btn-mode {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 30px;
  padding: 0 11px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 999px;
  color: var(--ink-3);
  font-family: var(--font-body);
  font-size: 12px;
  font-weight: 400;
  letter-spacing: 0.005em;
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.15s ease, color 0.15s ease;
  box-shadow: none;
}

.btn-mode:hover,
.btn-mode.open {
  background: var(--fill-hover);
  color: var(--ink);
}

/* 模式弹层：照 board-popup 的向上弹定位与令牌；首屏（popupDown）翻转向下 */
.mode-popup-backdrop {
  position: fixed;
  inset: 0;
  z-index: 60;
}

.mode-popup {
  position: absolute;
  right: -8px;
  bottom: calc(100% + 12px);
  z-index: 61;
  width: 300px;
  display: flex;
  flex-direction: column;
  /* 无标题头，条目组直接顶格：上下留白由弹层自身 padding 承担 */
  padding: 8px 0 10px;
  background: var(--popup-bg, rgba(255, 255, 255, 0.92));
  backdrop-filter: var(--popup-blur, blur(28px) saturate(180%));
  -webkit-backdrop-filter: var(--popup-blur, blur(28px) saturate(180%));
  border: 1px solid var(--popup-border, rgba(30, 64, 175, 0.12));
  border-radius: 14px;
  box-shadow: var(--popup-shadow, 0 18px 48px -16px rgba(15, 23, 42, 0.28));
  animation: popupRise 0.18s cubic-bezier(0.16, 1, 0.3, 1);
}

.mode-popup--down {
  bottom: auto;
  top: calc(100% + 12px);
}

.mode-popup-sep {
  height: 1px;
  margin: 9px 12px 5px;
  background: var(--line-hair);
}

.mode-popup-group {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 0 10px;
}

.mode-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 13px;
  border: none;
  border-radius: 11px;
  background: transparent;
  cursor: pointer;
  text-align: left;
  transition: background 0.14s ease;
}

.mode-item:hover {
  background: var(--fill-hover);
}

.mode-item.cur {
  background: rgba(30, 64, 175, 0.05);
}

.mi-name {
  flex-shrink: 0;
  font-family: var(--font-body);
  font-size: 13.5px;
  font-weight: 400;
  color: var(--ink-2);
}

.mode-item.cur .mi-name {
  color: #3b62c9;
}

.mi-meta {
  flex: 1;
  min-width: 0;
  font-family: var(--font-body);
  font-size: 11.5px;
  font-weight: 400;
  color: var(--ink-4);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mi-check {
  display: inline-flex;
  align-items: center;
  flex-shrink: 0;
  color: #7c9ae8;
}

/* ── 思考强度节（样张规格：A 骨架轨24/钮30 + 轨内 4px 淡刻度点 + S2 星夜带 + 拉满烟花） ──
   几何：行程按钮半径 PAD=15px 两端内缩（lvDotPos/lvFillW/lvKnobLeft 用 calc 实现）；
   拖拽中保留过渡但换 0.18s 微回弹曲线——过中点跳档是「滑过去」而非生硬瞬移 */
.lv-section {
  --lv-track-bg: rgba(30, 64, 175, 0.12);
  --lv-fill-bg: #2563eb;
  --lv-dot-on: rgba(255, 255, 255, 0.6);
  --lv-dot-off: rgba(30, 64, 175, 0.24);
  --lv-knob-shadow: 0 2px 10px rgba(15, 23, 42, 0.25);
  padding: 2px 22px 10px;
}

/* ink 主题降级（样张 body.ink 令牌组） */
:root[data-qa-theme='ink'] .lv-section {
  --lv-track-bg: rgba(255, 255, 255, 0.12);
  --lv-fill-bg: #3b82f6;
  --lv-dot-off: rgba(255, 255, 255, 0.18);
  --lv-knob-shadow: 0 2px 10px rgba(0, 0, 0, 0.6);
}

.lv-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 14px;
}

.lv-title {
  font-family: var(--font-body);
  font-size: 13.5px;
  font-weight: 400;
  color: var(--ink-2);
}

.lv-cur {
  font-family: var(--font-body);
  font-size: 11.5px;
  font-weight: 600;
  color: var(--accent, #1e40af);
}

/* 烟花锚定层：与轨同宽，burst 越过轨右缘 9px（弹层 padding 内，弹层无 overflow 裁切） */
.lv-zone {
  position: relative;
}

/* 粗轨道：24px 圆角胶囊抱 30px 白钮，整轨可按可拖；悬停抓手、拖动握紧 */
.lv-rail {
  position: relative;
  height: 24px;
  border-radius: 999px;
  background: var(--lv-track-bg);
  cursor: grab;
  touch-action: none;
  outline: none;
}

.lv-rail:focus-visible {
  box-shadow: 0 0 0 3px rgba(30, 64, 175, 0.2);
}

.lv-rail.is-drag {
  cursor: grabbing;
}

.lv-fill {
  position: absolute;
  inset: 0 auto 0 0;
  border-radius: 999px;
  background: var(--lv-fill-bg);
  transition: width 0.22s cubic-bezier(0.16, 1, 0.3, 1);
}

.lv-rail.is-drag .lv-fill {
  transition: width 0.18s cubic-bezier(0.34, 1.4, 0.64, 1);
}

.lv-handle {
  position: absolute;
  top: 50%;
  width: 30px;
  height: 30px;
  transform: translate(-50%, -50%);
  border-radius: 50%;
  background: #fff;
  box-shadow: var(--lv-knob-shadow);
  pointer-events: none;
  transition:
    left 0.22s cubic-bezier(0.16, 1, 0.3, 1),
    box-shadow 0.3s ease;
}

.lv-rail.is-drag .lv-handle {
  transition:
    left 0.18s cubic-bezier(0.34, 1.4, 0.64, 1),
    box-shadow 0.3s ease;
}

/* 轨内淡刻度点：已过档亮 / 未到档淡；拉满整组淡出 */
.lv-dots {
  position: absolute;
  inset: 0;
  pointer-events: none;
  transition: opacity 0.25s ease;
}

.lv-dot {
  position: absolute;
  top: 50%;
  width: 4px;
  height: 4px;
  transform: translate(-50%, -50%);
  border-radius: 50%;
  background: var(--lv-dot-off);
  transition: background 0.2s ease;
}

.lv-dot.on {
  background: var(--lv-dot-on);
}

.lv-max .lv-dots {
  opacity: 0;
}

/* S2 星夜带（仅拉满）：深紫渐变 + 双层辉光；钮加紫色光环 */
.lv-max .lv-fill {
  background: linear-gradient(90deg, #1e1b4b 0%, #5b21b6 60%, #8b5cf6 100%);
  box-shadow:
    0 0 18px rgba(139, 92, 246, 0.5),
    0 0 46px rgba(30, 27, 75, 0.55);
  overflow: hidden;
}

.lv-max .lv-handle {
  box-shadow:
    0 0 0 4px rgba(196, 181, 253, 0.5),
    0 0 24px rgba(139, 92, 246, 0.85),
    var(--lv-knob-shadow);
}

/* 20 颗随机星：外层游移（drift）+ ::after 明灭（twinkle），参数全走内联 CSS 变量 */
.lv-stars {
  position: absolute;
  inset: 0;
  pointer-events: none;
  opacity: 0;
  transition: opacity 0.3s ease;
}

.lv-max .lv-stars {
  opacity: 1;
}

.lv-star {
  position: absolute;
  animation: lv-star-drift var(--dd) ease-in-out infinite alternate;
}

.lv-star::after {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: 50%;
  background: var(--sc);
  box-shadow: 0 0 3px var(--sc);
  animation: lv-star-twinkle var(--td) ease-in-out infinite;
  animation-delay: var(--tl);
}

@keyframes lv-star-drift {
  from {
    transform: translate(0, 0);
  }
  to {
    transform: translate(var(--dx), var(--dy));
  }
}

@keyframes lv-star-twinkle {
  0%,
  100% {
    opacity: 0.12;
  }
  50% {
    opacity: 1;
  }
}

/* 拉满烟花：零尺寸锚点在钮右侧（轨右缘外 9px），粒子右偏扇形绽开 + 细环扩散，一次性 */
.lv-burst {
  position: absolute;
  right: -9px;
  top: 50%;
  width: 0;
  height: 0;
  pointer-events: none;
  z-index: 5;
}

.lv-fw-p {
  position: absolute;
  top: 0;
  left: 0;
  border-radius: 50%;
  animation: lv-fw-p 0.62s cubic-bezier(0.16, 1, 0.3, 1) forwards;
}

.lv-fw-ring {
  position: absolute;
  top: 0;
  left: 0;
  width: 26px;
  height: 26px;
  border-radius: 50%;
  border: 1.5px solid rgba(196, 181, 253, 0.9);
  animation: lv-fw-ring 0.55s ease-out forwards;
}

@keyframes lv-fw-p {
  from {
    transform: translate(-50%, -50%) scale(1);
    opacity: 1;
  }
  to {
    transform: translate(calc(-50% + var(--fx)), calc(-50% + var(--fy))) scale(0.2);
    opacity: 0;
  }
}

@keyframes lv-fw-ring {
  from {
    transform: translate(-50%, -50%) scale(0.25);
    opacity: 0.9;
  }
  to {
    transform: translate(-50%, -50%) scale(1.15);
    opacity: 0;
  }
}

.lv-note {
  margin-top: 12px;
  font-family: var(--font-body);
  font-size: 11px;
  color: var(--ink-4);
  text-align: center;
}

.composer-foot {
  position: relative;
  max-width: 880px;
  margin: 9px auto 0;
  text-align: center;
  font-family: var(--font-body);
  font-size: 11px;
  color: var(--ink-4);
  letter-spacing: 0.02em;
  text-transform: none;
  font-weight: 400;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-wrap: wrap;
  gap: 0 0.4em;
}

/* ─── input wrapper (for popup anchoring) ─────────────── */
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
  background: var(--popup-bg);
  backdrop-filter: var(--popup-blur);
  -webkit-backdrop-filter: var(--popup-blur);
  border: 1px solid var(--popup-border);
  border-radius: 16px;
  box-shadow: var(--popup-shadow);
  max-height: 320px;
  display: flex;
  flex-direction: column;
  animation: popupRise 0.22s cubic-bezier(0.22, 1, 0.36, 1);
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

/* 首屏形态：向下展开（输入框靠上，上方空间不够）——动画方向同步反过来 */
.skill-popup--down {
  bottom: auto;
  top: calc(100% + 10px);
  animation-name: popupDrop;
}

@keyframes popupDrop {
  from {
    opacity: 0;
    transform: translateY(-8px);
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
  border-bottom: 1px solid rgba(30, 64, 175, 0.08);
  background: rgba(30, 64, 175, 0.04);
  border-radius: 16px 16px 0 0;
}

.sp-icon {
  font-family: var(--font-body);
  font-size: 15px;
  color: #1e40af;
  font-weight: 700;
}

.sp-label {
  font-family: var(--font-mono);
  font-size: 9.5px;
  font-weight: 700;
  letter-spacing: 0.28em;
  color: var(--ink-2);
  text-transform: uppercase;
}

.sp-line {
  flex: 1;
  height: 1px;
  background: linear-gradient(to right, rgba(30, 64, 175, 0.1), transparent);
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
  background: rgba(30, 64, 175, 0.1);
  border-radius: 4px;
}

.skill-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  cursor: pointer;
  border-left: none;
  border-radius: 10px;
  margin: 0 4px;
  transition: background 0.12s;
}

.skill-item:last-child {
  border-bottom: none;
}

.skill-item.active,
.skill-item:hover {
  background: rgba(30, 64, 175, 0.06);
  border-left-color: transparent;
}

/* 条目图标瓦片：v-html（svg 源码/图片 dataURI，兜底四芒星） */
.sk-icon {
  flex-shrink: 0;
  width: 26px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  background: var(--fill-hover);
  color: var(--accent, #1e40af);
  overflow: hidden;
}
.sk-icon :deep(svg) {
  width: 15px;
  height: 15px;
  display: block;
}
.sk-icon :deep(img) {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

/* 图标右侧文本区：名字行 + 描述行竖排，独立截断 */
.sk-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

/* 名字行：key + 徽章。key 可缩可截断，徽章 flex-shrink:0 永不被挤掉 */
.sk-line {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.sk-name {
  font-family: var(--font-body);
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
  letter-spacing: -0.005em;
  flex: 0 1 auto;
  min-width: 0;
  /* key 可能超长（长技能名_专属code）：整行截断省略，悬停有完整 title */
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.skill-item.active .sk-name,
.skill-item:hover .sk-name {
  color: #1e40af;
  font-style: normal;
}

/* 「我的」标签：本人创建的技能专用，浅色胶囊克制提示（来源/作者徽章已去掉） */
.sk-src {
  flex-shrink: 0;
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.03em;
  padding: 1px 7px;
  border-radius: 20px;
}
.sk-src--mine {
  color: var(--accent, #1e40af);
  background: color-mix(in srgb, var(--accent, #1e40af) 10%, transparent);
}

/* @ 弹层分组小标题（专家 / 技能） */
.skill-group-label {
  padding: 7px 14px 3px;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.12em;
  color: var(--ink-4);
  list-style: none;
}

.sk-desc {
  font-family: var(--font-body);
  font-size: 12px;
  color: var(--ink-3);
  line-height: 1.5;
  /* 介绍限一行：技能描述普遍很长（有的几百字），不限行每条都被撑成段落，
     弹层条目高度失控、把列表挤变形；完整描述悬停 title 可见 */
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.skill-popup-foot {
  display: flex;
  gap: 18px;
  padding: 7px 14px;
  border-top: 1px solid rgba(30, 64, 175, 0.08);
  background: rgba(30, 64, 175, 0.04);
  border-radius: 0 0 16px 16px;
  font-family: var(--font-mono);
  font-size: 9px;
  color: var(--ink-3);
  letter-spacing: 0.14em;
  font-weight: 500;
  text-transform: uppercase;
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

/* ─── 附件按钮：样式并入功能条胶囊统一语言（.composer-actions-left .btn-attach） ── */
.attach-clip {
  display: inline-flex;
  align-items: center;
  flex-shrink: 0;
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
  transition: border-color 0.15s;
}

.attached-item:hover {
  border-color: rgba(30, 64, 175, 0.2);
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
  border-radius: 5px;
  font-size: 9px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.02em;
  color: #fff;
  background: #94a3b8;
  flex-shrink: 0;
}

.af-ext-pdf { background: #dc2626; }
.af-ext-doc { background: #2563eb; }
.af-ext-sheet { background: #16a34a; }
.af-ext-ppt { background: #ea580c; }
.af-ext-img { background: #7c3aed; }
.af-ext-zip { background: #854d0e; }
.af-ext-media { background: #0891b2; }
.af-ext-code { background: #475569; }
.af-ext-text { background: #64748b; }
.af-ext-other { background: #94a3b8; }

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
  background: rgba(30, 64, 175, 0.1);
  border-radius: 4px;
  overflow: hidden;
  flex-shrink: 0;
}

.af-progress-fill {
  display: block;
  height: 100%;
  background: #1e40af;
  border-radius: 4px;
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

/* ─── 附件：图片缩略图（即时回显，点击放大）──────────────────────────── */
.attached-item--img {
  position: relative; /* 关键：作为删除按钮的定位锚点，否则 absolute 按钮会飞到外层 relative 容器角上 */
  display: inline-block;
  /* 一圈透明内边距 = 悬停安全桥：按钮定位在此区域内，鼠标移向它时不会脱离 :hover */
  padding: 4px;
  border: none;
  background: transparent;
  box-shadow: none;
  max-width: none;
  vertical-align: top;
}

.attached-item--img:hover {
  border-color: transparent;
}

.af-thumb {
  position: relative;
  width: 56px;
  height: 56px;
  border-radius: 10px;
  overflow: hidden;
  border: 1px solid rgba(30, 64, 175, 0.14);
  background: #fff;
  line-height: 0;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.6), 0 1px 3px rgba(15, 23, 42, 0.08);
}

.af-thumb :deep(.n-image),
.af-thumb :deep(.n-image img),
.af-thumb-img {
  width: 100%;
  height: 100%;
  display: block;
  object-fit: cover;
  cursor: pointer;
  transition: transform 0.18s ease;
}

.attached-item--img:hover .af-thumb-img {
  transform: scale(1.05);
}

/* 覆盖 naive-ui n-image 内置的 zoom-in 光标，悬停图片显示手指 */
.af-thumb :deep(.n-image),
.af-thumb :deep(.n-image img) {
  cursor: pointer !important;
}

/* 上传中 / 失败遮罩 */
.af-thumb-mask {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 3px;
  background: rgba(15, 23, 42, 0.46);
  color: #fff;
  pointer-events: none;
}

.af-thumb-mask--err {
  background: rgba(220, 38, 38, 0.55);
}

.af-thumb-spin {
  width: 16px;
  height: 16px;
  border: 2px solid rgba(255, 255, 255, 0.4);
  border-top-color: #fff;
  border-radius: 50%;
  animation: af-spin 0.8s linear infinite;
}

@keyframes af-spin {
  to { transform: rotate(360deg); }
}

.af-thumb-pct {
  font-family: var(--font-mono);
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.02em;
}

.af-thumb-err {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #fff;
  color: #dc2626;
  font-weight: 800;
  font-size: 12px;
  line-height: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* 角标移除按钮：默认隐藏，悬停浮现；定位在容器内边距"安全桥"内，鼠标移向它时不脱离 :hover */
.af-thumb-remove {
  position: absolute;
  top: 0;
  right: 0;
  z-index: 3;
  width: 18px;
  height: 18px;
  padding: 0;
  border-radius: 50%;
  border: 1px solid rgba(255, 255, 255, 0.92);
  background: rgba(15, 23, 42, 0.72);
  cursor: pointer;
  opacity: 0;
  transform: scale(0.8);
  transition: opacity 0.15s, background 0.15s, transform 0.15s;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.3);
}

/* 用 CSS 画叉：两条线经 transform 几何居中，避免文字 × 字形偏上的视觉偏差 */
.af-thumb-remove::before,
.af-thumb-remove::after {
  content: '';
  position: absolute;
  top: 50%;
  left: 50%;
  width: 9px;
  height: 1.6px;
  border-radius: 1px;
  background: #fff;
}

.af-thumb-remove::before {
  transform: translate(-50%, -50%) rotate(45deg);
}

.af-thumb-remove::after {
  transform: translate(-50%, -50%) rotate(-45deg);
}

.attached-item--img:hover .af-thumb-remove {
  opacity: 1;
  transform: scale(1);
}

.af-thumb-remove:hover {
  background: #dc2626;
}

/* ─── 附件：文件主体（可预览时为按钮）────────────────────────────────── */
.af-file {
  padding-right: 4px;
}

.af-file-main {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  flex: 1;
  min-width: 0;
  padding: 0;
  margin: 0;
  border: none;
  background: none;
  font: inherit;
  color: inherit;
  text-align: left;
}

button.af-file-main {
  cursor: pointer;
}

button.af-file-main:disabled {
  cursor: default;
}

.af-file--preview {
  cursor: pointer;
}

.af-file--preview:hover {
  border-color: rgba(30, 64, 175, 0.28);
  background: rgba(30, 64, 175, 0.06);
}

.af-preview-hint {
  font-family: var(--font-body);
  font-size: 9.5px;
  font-weight: 700;
  letter-spacing: 0.02em;
  color: #1e40af;
  padding: 1px 7px;
  border: 1px solid rgba(30, 64, 175, 0.2);
  border-radius: 999px;
  margin-left: auto;
  flex-shrink: 0;
  opacity: 0.8;
  transition: background 0.15s, color 0.15s, border-color 0.15s, opacity 0.15s;
}

.af-file--preview:hover .af-preview-hint {
  background: #1e40af;
  color: #fff;
  border-color: transparent;
  opacity: 1;
}

/* ─── mini 形态（工作流画布迷你栏嵌入）──────────────────────────── */
/* 外层玻璃容器与内边距由宿主提供：这里只剥掉外壳，留下附件条 + 输入框 */
.qa-composer.is-mini {
  padding: 0;
  background: transparent;
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
}

.qa-composer.is-mini .composer-frame {
  max-width: none;
  margin: 0;
}

.qa-composer.is-mini .attached-bar {
  max-width: none;
  margin: 0 0 6px;
}

.qa-composer.is-mini .composer-input {
  min-height: 40px;
  max-height: 120px;
}

/* ─── 响应式：960px 以下（手机端）─────────────────────────────── */
@media (max-width: 960px) {
  .qa-composer {
    padding: 10px 12px max(12px, env(safe-area-inset-bottom));
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

  .btn-attach,
  .btn-plus {
    width: 36px;
    height: 36px;
    border-radius: 12px;
    flex-shrink: 0;
  }

  /* 级联子面板收窄：菜单右缘已占 ~170px，避免窄屏溢出 */
  .plus-sub-card {
    width: min(224px, 48vw);
  }

  .composer-input-wrap {
    flex: 1 1 auto;
    min-width: 0;
    order: 0;
  }

  .composer-input {
    font-size: 16px;
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

  .attached-bar {
    margin: 0 0 6px;
  }

  .attached-item {
    max-width: 100%;
    border-radius: 12px;
  }

  .af-name {
    max-width: 110px;
  }

  .af-remove,
  .af-thumb-remove {
    opacity: 1;
  }

  .af-thumb {
    width: 52px;
    height: 52px;
  }

  .btn-attach,
  .btn-plus,
  .skill-popup,
  .skill-item {
    border-radius: 12px;
  }

  .btn-send, .btn-stop {
    border-radius: 999px;
  }

  .skill-popup {
    max-height: 50vh;
    left: -10px;
    right: -10px;
  }

  .skill-list {
    max-height: calc(50vh - 80px);
  }

  .skill-item {
    gap: 8px;
  }

  .sk-icon {
    width: 22px;
    height: 22px;
    border-radius: 7px;
  }

  .sk-name {
    font-size: 14px;
  }

  .sk-desc {
    font-size: 11.5px;
  }

  .skill-popup-foot {
    gap: 12px;
    font-size: 8.5px;
  }
}

@media (max-width: 480px) {
  .composer-foot {
    font-size: 8px;
    letter-spacing: 0.18em;
  }
}

/* 首屏（双主题）：输入框 hero 化（更大，成为视觉主角，样张同款） */
.kimi-welcome-on .composer-frame {
  border-radius: 24px;
}

.kimi-welcome-on .composer-input {
  min-height: 96px;
}

/* 首屏灰带叠层下，隐藏输入框底部 caption（样张无） */
.kimi-welcome-on .composer-foot {
  display: none;
}

/* ─── + 菜单（未点亮入口折叠）+ 选板弹层 ─── */
.plus-wrap {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
}

/* + 钮：与功能条胶囊同族（30px 圆钮）；打开时 + 旋成 ×，原位表示收起 */
.btn-plus {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  padding: 0;
  border: none;
  border-radius: 999px;
  background: transparent;
  color: var(--ink-3);
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}

.btn-plus:hover,
.btn-plus.open {
  background: var(--fill-hover);
  color: var(--ink);
}

.btn-plus svg {
  transition: transform 0.18s ease;
}

.btn-plus.open svg {
  transform: rotate(45deg);
}

.plus-backdrop {
  position: fixed;
  inset: 0;
  z-index: 60;
}

/* 纵向菜单：令牌与选板弹层同源（popup-* 变量 + popupRise 动画） */
.plus-menu {
  position: absolute;
  left: -8px;
  bottom: calc(100% + 12px);
  z-index: 61;
  width: 172px;
  padding: 6px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  background: var(--popup-bg, rgba(255, 255, 255, 0.92));
  backdrop-filter: var(--popup-blur, blur(28px) saturate(180%));
  -webkit-backdrop-filter: var(--popup-blur, blur(28px) saturate(180%));
  border: 1px solid var(--popup-border, rgba(30, 64, 175, 0.12));
  border-radius: 14px;
  box-shadow: var(--popup-shadow, 0 18px 48px -16px rgba(15, 23, 42, 0.28));
  animation: popupRise 0.18s cubic-bezier(0.16, 1, 0.3, 1);
}

.plus-item {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 7px 9px;
  border: none;
  border-radius: 9px;
  background: transparent;
  color: var(--ink-2);
  font-family: var(--font-body);
  font-size: 12.5px;
  font-weight: 500;
  letter-spacing: 0.005em;
  cursor: pointer;
  text-align: left;
  white-space: nowrap;
  transition: background 0.12s ease, color 0.12s ease;
}

.plus-item:hover {
  background: var(--fill-hover);
  color: var(--ink);
}

.plus-item svg {
  flex-shrink: 0;
  color: var(--ink-3);
  transition: color 0.12s ease;
}

.plus-item:hover svg {
  color: inherit;
}

/* 持续精造已点亮：菜单内同步着色标示（仍可从菜单点击关闭） */
.plus-item.active,
.plus-item.active svg {
  color: #1d4ed8;
}

/* 菜单分组线：附件 / 板与模式 / 专家与连接器 三类（令牌同选板弹层的 line-hair） */
.plus-divider {
  height: 1px;
  margin: 4px 7px;
  background: var(--line-hair, rgba(30, 64, 175, 0.08));
  flex-shrink: 0;
}

/* ─── + 菜单级联二级（专家召唤候选 / 连接器启停） ─── */
/* 级联条目：右端箭头指示；展开时条目高亮 */
.plus-caret {
  margin-left: auto;
  font-size: 14px;
  line-height: 1;
  color: var(--ink-4);
  transition: color 0.12s ease, transform 0.12s ease;
}

.plus-item.sub {
  background: var(--fill-hover);
  color: var(--ink);
}

.plus-item.sub .plus-caret {
  color: inherit;
  transform: translateX(1px);
}

/* 子面板自菜单右侧展开、底缘对齐（专家/连接器条目在菜单底部）；
   padding-left 充当悬停桥：鼠标横越菜单与面板间隙不掉落 */
.plus-sub {
  position: absolute;
  left: 100%;
  bottom: -6px;
  z-index: 62;
  padding-left: 8px;
  animation: plusSubIn 0.16s cubic-bezier(0.16, 1, 0.3, 1);
}

@keyframes plusSubIn {
  from {
    opacity: 0;
    transform: translateX(-6px);
  }
  to {
    opacity: 1;
    transform: translateX(0);
  }
}

.plus-sub-card {
  width: 224px;
  max-height: 320px;
  display: flex;
  flex-direction: column;
  background: var(--popup-bg, rgba(255, 255, 255, 0.92));
  backdrop-filter: var(--popup-blur, blur(28px) saturate(180%));
  -webkit-backdrop-filter: var(--popup-blur, blur(28px) saturate(180%));
  border: 1px solid var(--popup-border, rgba(30, 64, 175, 0.12));
  border-radius: 14px;
  box-shadow: var(--popup-shadow, 0 18px 48px -16px rgba(15, 23, 42, 0.28));
  overflow: hidden;
}

.plus-sub-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 10px 13px 8px;
  border-bottom: 1px solid var(--line-hair, rgba(30, 64, 175, 0.08));
  font-family: var(--font-body);
  font-size: 12.5px;
  font-weight: 700;
  color: var(--ink);
}

.plus-sub-head span {
  font-size: 10.5px;
  font-weight: 400;
  color: var(--ink-4);
}

.plus-sub-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  scrollbar-gutter: stable;
  padding: 4px 6px;
}

.plus-sub-item {
  display: flex;
  align-items: center;
  gap: 9px;
  width: 100%;
  padding: 7px 8px;
  border: none;
  border-radius: 9px;
  background: transparent;
  cursor: pointer;
  font-family: var(--font-body);
  text-align: left;
  transition: background 0.12s ease;
}

.plus-sub-item:hover {
  background: var(--fill-hover);
}

.plus-sub-icon {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 7px;
  background: var(--fill-hover);
  color: var(--accent, #1e40af);
  overflow: hidden;
}

.plus-sub-icon :deep(svg) {
  width: 13px;
  height: 13px;
}

.plus-sub-icon :deep(img) {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.plus-sub-name {
  flex: 1;
  min-width: 0;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ink-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 连接器行是开关行不是按钮：整行不带手型，点击只在开关上 */
.plus-sub-item--switch {
  cursor: default;
}

.plus-sub-empty {
  padding: 14px 10px;
  font-family: var(--font-body);
  font-size: 11.5px;
  line-height: 1.7;
  color: var(--ink-4);
  text-align: center;
}

.plus-sub-foot {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 8px;
  border: none;
  border-top: 1px solid var(--line-hair, rgba(30, 64, 175, 0.08));
  background: transparent;
  color: var(--ink-3);
  font-family: var(--font-body);
  font-size: 12px;
  cursor: pointer;
  transition: background 0.12s ease, color 0.12s ease;
}

.plus-sub-foot:hover {
  background: var(--fill-hover);
  color: var(--accent, #1e40af);
}

/* 已挂板 chip：板名 + 换板箭头（紧凑，不抢输入区视觉）；基础样式并入功能条胶囊统一语言 */
.btn-board.chip {
  width: auto;
  gap: 5px;
  padding: 0 8px;
  height: 30px;
  max-width: 180px;
  background: var(--fill-2);
  border: 1px solid var(--line-hair);
  color: var(--ink-2);
  font-size: 12px;
  font-weight: 500;
}

.btn-board.chip:hover {
  color: var(--ink);
  border-color: var(--line-soft, rgba(30, 64, 175, 0.16));
}

.btn-board.chip-html {
  color: #1d4ed8;
}

/* 持续精造点亮态：轻着色标示模式开启（与挂板 chip 同源语言，不抢输入区视觉） */
.composer-actions-left .btn-sustained.active {
  background: var(--fill-2);
  border: 1px solid var(--line-hair);
  color: #1d4ed8;
}

.composer-actions-left .btn-sustained.active:hover {
  background: var(--fill-hover);
  border-color: var(--line-soft, rgba(30, 64, 175, 0.16));
  color: #1e40af;
}

.btn-board.chip .bb-label {
  max-width: 100px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 挂板 chip 容器：叉绝对定位叠在图标位（与专家 chip 同款交互） */
.board-chip-slot {
  position: relative;
  display: inline-flex;
  align-items: center;
}

.btn-board-x {
  position: absolute;
  left: 8px; /* 对齐 chip 的 padding-left，正好盖住板图标 */
  top: 50%;
  transform: translateY(-50%);
  width: 15px;
  height: 15px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: var(--surface-1, rgba(241, 245, 249, 1));
  color: var(--ink-3);
  cursor: pointer;
  opacity: 0;
  pointer-events: none; /* 隐藏时不可点，避免误触解除 */
  transition: opacity 0.15s, color 0.12s;
}

.board-chip-slot:hover .btn-board-x,
.btn-board-x:focus-visible {
  opacity: 1;
  pointer-events: auto;
}

.btn-board-x:hover {
  color: #dc2626;
}

/* 触屏没有 hover：保持弱可见，保证可点 */
@media (hover: none) {
  .btn-board-x {
    opacity: 0.55;
    pointer-events: auto;
  }
}

/* 选板弹层：照 skill-popup 的向上弹定位与令牌 */
.board-popup {
  position: absolute;
  left: -8px;
  bottom: calc(100% + 12px);
  z-index: 61;
  width: 280px;
  max-height: 380px;
  display: flex;
  flex-direction: column;
  background: var(--popup-bg, rgba(255, 255, 255, 0.92));
  backdrop-filter: var(--popup-blur, blur(28px) saturate(180%));
  -webkit-backdrop-filter: var(--popup-blur, blur(28px) saturate(180%));
  border: 1px solid var(--popup-border, rgba(30, 64, 175, 0.12));
  border-radius: 14px;
  box-shadow: var(--popup-shadow, 0 18px 48px -16px rgba(15, 23, 42, 0.28));
  animation: popupRise 0.18s cubic-bezier(0.16, 1, 0.3, 1);
  overflow: hidden;
}

.board-popup-backdrop {
  position: fixed;
  inset: 0;
  z-index: 60;
}

.board-popup-head {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 10px 13px 8px;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ink);
}

.board-popup-head .bp-icon {
  color: var(--ink-3);
}

.board-popup-head .bp-line {
  flex: 1;
  height: 1px;
  background: var(--line-hair);
}

.board-popup-head .bp-hint {
  font-size: 11px;
  font-weight: 400;
  color: var(--ink-4);
}

.board-popup-new {
  margin: 0 8px 6px;
  padding: 8px 10px;
  border: 1px dashed var(--line-soft, rgba(30, 64, 175, 0.24));
  border-radius: 9px;
  background: transparent;
  color: var(--ink-2);
  font-size: 12.5px;
  cursor: pointer;
  text-align: left;
  transition: all 0.15s ease;
}

.board-popup-new:hover {
  border-color: var(--grad-brand, #2563eb);
  color: var(--ink);
  background: var(--fill-hover);
}

.board-popup-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 2px 6px 6px;
}

.board-popup-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 9px;
  border: none;
  border-radius: 8px;
  background: transparent;
  cursor: pointer;
  text-align: left;
  transition: background 0.12s ease;
}

.board-popup-item:hover {
  background: var(--fill-hover);
}

.board-popup-item.cur .bpi-name {
  color: var(--accent, #2563eb);
  font-weight: 600;
}

.bpi-name {
  flex: 1;
  min-width: 0;
  font-size: 12.5px;
  color: var(--ink-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.bpi-meta {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--ink-4);
}

.board-popup-empty {
  padding: 16px 10px;
  text-align: center;
  font-size: 12px;
  color: var(--ink-4);
}

.board-popup-detach {
  padding: 8px;
  border: none;
  border-top: 1px solid var(--line-hair);
  background: transparent;
  color: var(--ink-3);
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.board-popup-detach:hover {
  color: var(--ink);
  background: var(--fill-hover);
}

.board-popup-expand {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 8px;
  border: none;
  border-top: 1px solid var(--line-hair);
  background: transparent;
  color: var(--ink-3);
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.board-popup-expand:hover {
  color: var(--ink);
  background: var(--fill-hover);
}

</style>
