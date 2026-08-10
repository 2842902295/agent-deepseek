<script setup lang="ts">
import { ref, nextTick } from 'vue';
import { NImage } from 'naive-ui';
import type { AgentSkill } from '@/service/api/ai';
import {extractExt} from '@/utils/attachment';
import SkillPanel from './skill/SkillPanel.vue';

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
  composerExpanded: boolean;
  isMobile: boolean;
  filteredSkills: AgentSkill[];
  skillPopupOpen: boolean;
  skillActiveIndex: number;
  currentSessionKey: string | null;
  /** mini 形态（嵌入工作流画布迷你栏）：去掉工具条 / 页脚 / 放大按钮，压缩输入区高度；外壳玻璃容器由宿主提供 */
  mini?: boolean;
}>();

const emit = defineEmits<{
  'update:modelValue': [val: string];
  'update:composerExpanded': [val: boolean];
  'update:skillActiveIndex': [val: number];
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
  /** 技能管理面板内数据有变更（启停/删除/上传/编辑）：透传给父级刷新 @ 调用列表 */
  skillPanelChange: [];
}>();

// local state
const dragOver = ref(false);
const fileInputEl = ref<HTMLInputElement | null>(null);
const composerInputEl = ref<HTMLTextAreaElement | null>(null);

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
}

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

// ─────── 技能管理面板 ───────────────────────────────────────────────────────
const skillPanelOpen = ref(false);

function openSkillPanel() {
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

// expose focus so parent can focus the textarea
defineExpose({
  focus() {
    composerInputEl.value?.focus();
  },
  // 技能面板入口已移到侧栏（QASidebar「技能管理」），由父组件代为打开
  openSkillPanel,
  setCaretPos(pos: number) {
    composerInputEl.value?.focus();
    composerInputEl.value?.setSelectionRange(pos, pos);
  },
  resetHeight() {
    const el = composerInputEl.value;
    if (el) {
      el.style.height = '';
      el.style.overflowY = '';
    }
  },
  get selectionStart() {
    return composerInputEl.value?.selectionStart ?? null;
  }
});
</script>

<template>
  <footer
    :class="{ 'is-expanded': composerExpanded, 'is-mini': mini }"
    class="qa-composer"
  >
    <!-- 附件条（独立于输入框之上）：图片回显缩略图（点击放大），文件可预览则整块可点 -->
    <div v-if="attachedFiles.length" class="attached-bar">
      <template v-for="af in attachedFiles" :key="af.id">
        <!-- 图片：本地 object URL 即时回显，n-image 点击放大（与对话同组件） -->
        <div v-if="isImg(af.name)" class="attached-item attached-item--img">
          <div class="af-thumb">
            <n-image
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
        <div v-else :class="['attached-item', 'af-file', { 'has-error': af.error }]">
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
      <button :disabled="running" class="btn-attach" title="上传附件" @click="triggerFileInput">
        <svg class="attach-clip" width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M14.29 7.37l-6.13 6.13a4 4 0 0 1-5.66-5.66l5.71-5.71A2.67 2.67 0 1 1 12 5.89l-5.73 5.71a1.33 1.33 0 0 1-1.89-1.89l5.66-5.65" />
        </svg>
      </button>
      <input ref="fileInputEl" hidden multiple type="file" @change="handleFileChange" />

      <span class="composer-prompt">›</span>
      <div class="composer-input-wrap">
        <textarea
          ref="composerInputEl"
          :value="modelValue"
          :rows="isMobile ? 1 : mini ? 2 : (composerExpanded ? 12 : 3)"
          class="composer-input"
          :enterkeyhint="isMobile ? 'enter' : undefined"
          :placeholder="running
              ? '可继续输入下一个问题，待当前回复结束后发送…'
              : mini
                ? '告诉 agent 怎么改… Enter 发送，可拖入 / 粘贴附件'
                : (isMobile ? '输入问题…' : '输入问题…  Enter 发送 · Shift + Enter 换行 · @ 调用技能')"
          @input="handleInput"
          @keydown="handleKeydown"
          @paste="handlePaste"
          @blur="handleCloseSkillPopup"
        />

        <!-- Skill popup -->
        <div v-if="skillPopupOpen && filteredSkills.length" class="skill-popup">
          <div class="skill-popup-head">
            <span class="sp-icon">@</span>
            <span class="sp-label">技&nbsp;能&nbsp;调&nbsp;用</span>
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
              <!-- 显示即调用标识（skillKey）：插入与后端匹配都按 key，显示 name 会误导手打 -->
              <span class="sk-name" :title="`@${sk.skillKey}`">@{{ sk.skillKey }}</span>
              <span class="sk-desc">{{ sk.name !== sk.skillKey ? `${sk.name} · ` : '' }}{{ sk.description || '' }}</span>
              <span v-if="sk.source === 'derived'" class="sk-badge">凝练</span>
            </li>
          </ul>
          <div class="skill-popup-foot">
            <span>↑↓ 选择</span>
            <span>Enter / Tab 插入</span>
            <span>Esc 关闭</span>
          </div>
        </div>
      </div>
      <div class="composer-side">
        <span v-if="modelValue.length > 0" class="char-count">{{ modelValue.length }}</span>
        <button
          v-if="!mini"
          :title="composerExpanded ? '收起输入框' : '放大输入框'"
          class="btn-expand"
          @click="emit('update:composerExpanded', !composerExpanded)"
        >
          <span v-if="!composerExpanded" class="expand-icon">
            <i class="ex-arrow ex-tl" />
            <i class="ex-arrow ex-br" />
          </span>
          <span v-else class="expand-icon">
            <i class="ex-arrow ex-tl-in" />
            <i class="ex-arrow ex-br-in" />
          </span>
        </button>
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

    <div v-if="!mini" class="composer-foot">
      AI · 生成结果 · 请核对关键数据
      <BeianInfo />
    </div>

    <!-- 技能管理面板（右侧宽抽屉：列表/详情/创建/发现/上传，自带 Teleport） -->
    <SkillPanel v-model:show="skillPanelOpen" @use="onPanelUse" @fill="onPanelFill" @change="emit('skillPanelChange')" />
  </footer>
</template>

<style scoped>
.qa-composer {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  border-top: none;
  padding: 14px 24px 16px;
  /* 镂空背景：整条去掉底色与毛玻璃，页面背景（渐变光球/点阵）直接透出，
     输入框与工具按钮以独立玻璃件悬浮其上 */
  background: transparent;
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
}

/* 大框模式：高度定死 56vh，输入框 flex 吃掉剩余空间——
   工具条 / 附件条 / 页脚各占自然高度，页脚永远钉在底边，
   不再依赖魔法数字给页脚"预留"，也就不会被挤出边界 */
.qa-composer.is-expanded {
  height: 56vh;
}

.qa-composer.is-expanded .composer-frame {
  flex: 1;
  min-height: 140px;
  align-items: stretch;
}

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
  /* 只过渡颜色类属性：尺寸/弹性在大小框切换时必须瞬时完成，
     all 会让 flex-grow 参与插值，产生"闪现后缓慢铺开"的撕裂感 */
  transition: border-color 0.2s ease, background-color 0.2s ease, box-shadow 0.2s ease;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.95),
    inset 0 0 0 1px rgba(255, 255, 255, 0.4),
    0 1px 2px rgba(15, 23, 42, 0.04);
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

.composer-frame.is-dragover {
  border-color: rgba(30, 64, 175, 0.3);
  background: rgba(30, 64, 175, 0.06);
  border-radius: 18px;
}

.composer-prompt {
  font-family: 'Plus Jakarta Sans', sans-serif;
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
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 14.5px;
  line-height: 1.6;
  color: var(--ink);
  min-height: 72px;
  max-height: 280px;
  font-feature-settings: 'ss01';
}

.composer-input::placeholder {
  color: var(--ink-4);
  font-style: normal;
  font-family: 'Plus Jakarta Sans', sans-serif;
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

.btn-send {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 18px;
  border: none;
  cursor: pointer;
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 12.5px;
  font-weight: 600;
  letter-spacing: 0.005em;
  border-radius: 11px;
  transition: all 0.2s ease;
  text-transform: none;
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
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 16px;
  font-weight: 700;
  transition: transform 0.2s;
}

.btn-send:hover:not(:disabled) .btn-arrow {
  transform: translateX(4px);
}

.btn-stop {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 18px;
  border: 1px solid rgba(30, 64, 175, 0.25);
  cursor: pointer;
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 12.5px;
  font-weight: 600;
  letter-spacing: 0.005em;
  border-radius: 11px;
  transition: all 0.2s ease;
  text-transform: none;
  background: rgba(255, 255, 255, 0.62);
  color: #1e40af;
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
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  color: var(--ink-4);
  letter-spacing: 0.04em;
  text-transform: none;
  font-weight: 500;
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
  border-bottom: 1px solid rgba(30, 64, 175, 0.08);
  background: rgba(30, 64, 175, 0.04);
  border-radius: 16px 16px 0 0;
}

.sp-icon {
  font-family: 'Plus Jakarta Sans', sans-serif;
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
  align-items: baseline;
  gap: 14px;
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

.sk-name {
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
  letter-spacing: -0.005em;
  min-width: 130px;
  max-width: 340px;
  flex-shrink: 0;
  /* key 可能较长（专属code_技能名）：截断省略，悬停有完整 title */
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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

.sk-badge {
  font-family: var(--font-mono);
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.16em;
  background: rgba(30, 64, 175, 0.08);
  color: #1e40af;
  padding: 1px 6px;
  border-radius: 6px;
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

/* ─── 附件按钮 ────────────────────────────────────────────────── */
.btn-attach {
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(255, 255, 255, 0.62);
  border: 1px solid rgba(30, 64, 175, 0.12);
  border-radius: 10px;
  cursor: pointer;
  transition: all 0.2s ease;
  color: var(--ink-3, #64748b);
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.95), inset 0 0 0 1px rgba(255,255,255,0.4);
}

.btn-attach:hover:not(:disabled) {
  border-color: rgba(30, 64, 175, 0.25);
  color: #1e40af;
  background: rgba(255, 255, 255, 0.78);
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

  .btn-attach {
    width: 36px;
    height: 36px;
    border-radius: 12px;
    flex-shrink: 0;
  }

  .btn-expand {
    display: none;
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
  .btn-expand,
  .skill-popup,
  .skill-item,
  .sk-badge {
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
    flex-wrap: wrap;
    gap: 4px 12px;
  }

  .sk-name {
    min-width: 0;
    max-width: 100%;
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
}

@media (max-width: 480px) {
  .composer-foot {
    font-size: 8px;
    letter-spacing: 0.18em;
  }
}

</style>
