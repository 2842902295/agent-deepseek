<script setup lang="ts">
import {nextTick, onBeforeUnmount, onMounted, ref, watch} from 'vue';
import {NDrawer, NDrawerContent, NUpload, NUploadDragger, NButton, type UploadCustomRequestOptions, type UploadFileInfo} from 'naive-ui';
import {fetchUploadFile} from '@/service/api';
import {useNian} from '../composables/useNian';

const {inboxOpen, inboxPrefill, inboxSourceHint, closeInbox, commitInbox} = useNian();

const text = ref('');
const committing = ref(false);
const taRef = ref<HTMLTextAreaElement | null>(null);

/** 已成功上传的文件：fileId → {name, path, size, isImage} */
const uploadedFiles = ref<Map<string, {name: string; path: string; size: number; isImage: boolean}>>(new Map());

const isMobile = ref(false);
function updateIsMobile() {
  isMobile.value = window.innerWidth <= 640;
}
onMounted(() => {
  updateIsMobile();
  window.addEventListener('resize', updateIsMobile);
});
onBeforeUnmount(() => window.removeEventListener('resize', updateIsMobile));

/** NUpload custom-request：调用 fetchUploadFile 上传到服务端 */
async function customUpload(options: UploadCustomRequestOptions) {
  const {file, onProgress, onFinish, onError} = options;
  if (!file.file) {
    onError();
    return;
  }
  try {
    const result = await fetchUploadFile(file.file, 'inbox', (pct: number) => {
      onProgress({percent: pct});
    });
    uploadedFiles.value.set(file.id, {
      name: file.name,
      path: result.path,
      size: file.file.size,
      isImage: file.file.type.startsWith('image/'),
    });
    onFinish();
  } catch (e: any) {
    onError();
  }
}

/** 用户从文件列表移除 */
function onUploadRemove(file: UploadFileInfo) {
  uploadedFiles.value.delete(file.id);
  return true;
}

const validFileCount = () => uploadedFiles.value.size;

async function commit() {
  const t = text.value.trim();
  const atts = Array.from(uploadedFiles.value.values());
  if ((!t && !atts.length) || committing.value) return;
  const captured = {
    text: t || '(附件)',
    sourceHint: inboxSourceHint.value,
    attachments: atts,
  };
  text.value = '';
  uploadedFiles.value = new Map();
  closeInbox();
  committing.value = true;
  try {
    await commitInbox(captured.text, captured.sourceHint, captured.attachments);
  } finally {
    committing.value = false;
  }
}

function autoGrow() {
  const el = taRef.value;
  if (!el) return;
  el.style.height = 'auto';
  const cap = Math.floor(window.innerHeight * (isMobile.value ? 0.6 : 0.7));
  el.style.height = `${Math.min(el.scrollHeight, cap)}px`;
}

function onInput() {
  autoGrow();
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    commit();
    return;
  }
  if (e.key === 'Escape') {
    closeInbox();
  }
}

watch(inboxOpen, async (v) => {
  if (v) {
    text.value = inboxPrefill.value || '';
    await nextTick();
    taRef.value?.focus();
    if (taRef.value && text.value) {
      taRef.value.setSelectionRange(text.value.length, text.value.length);
    }
    autoGrow();
  } else {
    text.value = '';
    uploadedFiles.value = new Map();
    if (taRef.value) taRef.value.style.height = '';
  }
});

watch(text, async () => {
  await nextTick();
  autoGrow();
});
</script>

<template>
  <NDrawer
    :show="inboxOpen"
    :width="isMobile ? undefined : 520"
    :height="isMobile ? '88vh' : undefined"
    :placement="isMobile ? 'bottom' : 'right'"
    :auto-focus="false"
    :mask-closable="!committing"
    @update:show="(v) => !v && closeInbox()"
  >
    <NDrawerContent :native-scrollbar="false" :closable="false">
      <template #header>
        <div class="hd">
          <span class="hd-mark" />
          <span class="hd-title">万 用 收 件 箱</span>
          <span class="hd-hint">Enter 记住 · Esc 关闭</span>
        </div>
      </template>

      <div class="body">
        <textarea
          ref="taRef"
          v-model="text"
          class="ta"
          placeholder="把它丢进知识库… 写完按 Enter，AI 会替你判断该归到哪里"
          rows="8"
          @input="onInput"
          @keydown="onKeydown"
        />

        <!-- Naive UI 附件上传（支持拖拽） -->
        <NUpload
          :custom-request="customUpload"
          :show-file-list="true"
          :multiple="true"
          :max="10"
          :on-remove="onUploadRemove"
          list-type="text"
          class="inbox-upload"
        >
          <NUploadDragger :disabled="committing">
            <div class="drag-zone">
              <span class="drag-icon">📎</span>
              <span class="drag-text">点击或拖拽文件到这里</span>
            </div>
          </NUploadDragger>
        </NUpload>

        <div v-if="inboxSourceHint" class="src-hint">
          来自：{{ inboxSourceHint }}
        </div>

        <div class="rule" />

        <p class="lead">
          不必整理、不必分类。<br>
          写下来 → AI 替你判读类型、补标签、查重、归位。<br>
          有结果会浮一条提示出来。
        </p>
      </div>

      <template #footer>
        <button
          class="btn-go"
          :disabled="(!text.trim() && !validFileCount()) || committing"
          @click="commit"
        >
          <span class="btn-icon">↵</span>
          <span class="btn-text">记 下 来</span>
        </button>
      </template>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>

.hd {
  display: flex;
  align-items: center;
  gap: 10px;
  font-family: 'Plus Jakarta Sans', sans-serif;
}

.hd-mark {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: linear-gradient(110deg, #1e40af 0%, #2563eb 35%, #0ea5e9 70%, #0891b2 100%);
  box-shadow: 0 0 10px rgba(30, 64, 175, 0.5);
  flex-shrink: 0;
  animation: hd-mark-breathe 2.4s ease-in-out infinite;
}

@keyframes hd-mark-breathe {
  0%, 100% { transform: scale(1); opacity: 1; }
  50%      { transform: scale(1.18); opacity: 0.78; }
}

.hd-title {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.32em;
  background: linear-gradient(110deg, #1e40af 0%, #2563eb 35%, #0ea5e9 70%, #0891b2 100%);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  text-transform: uppercase;
}

.hd-hint {
  margin-left: auto;
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  letter-spacing: 0.1em;
  color: #94a3b8;
}

.body {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 4px 4px 0;
  font-family: 'Plus Jakarta Sans', sans-serif;
  color: #0f172a;
}

.ta {
  width: 100%;
  border: 1px solid rgba(30, 64, 175, 0.14);
  border-radius: 14px;
  padding: 14px 16px;
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 14px;
  font-weight: 500;
  line-height: 1.7;
  color: #0f172a;
  background: rgba(255, 255, 255, 0.65);
  resize: none;
  min-height: 200px;
  outline: none;
  overflow-y: auto;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.9),
    0 1px 2px rgba(15, 23, 42, 0.03);
  transition: border-color 0.18s ease, background 0.18s ease, box-shadow 0.18s ease;
}

.ta:hover {
  border-color: rgba(30, 64, 175, 0.28);
  background: rgba(255, 255, 255, 0.82);
}

.ta:focus {
  border-color: rgba(30, 64, 175, 0.45);
  background: #fff;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.9),
    0 0 0 3px rgba(30, 64, 175, 0.12),
    0 4px 14px -4px rgba(30, 64, 175, 0.22);
}

.ta::placeholder {
  color: #94a3b8;
  font-style: italic;
  font-weight: 400;
}

.src-hint {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  align-self: flex-start;
  padding: 4px 11px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.06em;
  color: #1e40af;
  background: rgba(30, 64, 175, 0.08);
  border: 1px solid rgba(30, 64, 175, 0.14);
  border-radius: 999px;
}

.rule {
  height: 1px;
  background: linear-gradient(to right, rgba(30, 64, 175, 0.5) 0%, rgba(30, 64, 175, 0.5) 32px, rgba(30, 64, 175, 0.1) 32px, rgba(30, 64, 175, 0.04) 100%);
}

.lead {
  margin: 0;
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 12.5px;
  font-weight: 500;
  line-height: 1.85;
  color: #64748b;
  letter-spacing: 0.005em;
}

.btn-go {
  position: relative;
  width: 100%;
  padding: 13px;
  border: 1px solid transparent;
  background: linear-gradient(110deg, #1e40af 0%, #2563eb 35%, #0ea5e9 70%, #0891b2 100%);
  color: #fff;
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 12.5px;
  font-weight: 700;
  letter-spacing: 0.28em;
  cursor: pointer;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.4),
    0 4px 14px -2px rgba(30, 64, 175, 0.5);
  transition: transform 0.15s ease, box-shadow 0.2s ease, opacity 0.15s ease;
}

.btn-go:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.5),
    0 6px 20px -2px rgba(30, 64, 175, 0.6);
}

.btn-go:active:not(:disabled) {
  transform: translateY(1px);
}

.btn-go:disabled {
  opacity: 0.45;
  cursor: not-allowed;
  background: rgba(255, 255, 255, 0.6);
  color: #94a3b8;
  border-color: rgba(30, 64, 175, 0.1);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.9);
}

.btn-icon {
  font-family: 'JetBrains Mono', monospace;
  font-size: 16px;
  font-weight: 700;
  letter-spacing: 0;
}

.btn-text {
  font-family: 'Plus Jakarta Sans', sans-serif;
}

/* ─── 移动端：抽屉切到底部，缩短输入区，隐掉次要文案 ─── */
@media (max-width: 640px) {
  .hd-hint { display: none; }
  .body {
    gap: 12px;
    padding: 0;
  }
  .ta {
    min-height: 140px;
    padding: 12px 14px;
    font-size: 15px; /* 移动端 ≥16px 防 iOS 自动缩放，14 已够，给 15 折中 */
    border-radius: 12px;
  }
  .lead { display: none; }
  .rule { display: none; }
  .btn-go {
    padding: 12px;
    letter-spacing: 0.18em;
    border-radius: 11px;
  }
}

/* ─── Naive UI 上传组件微调 ─── */
.inbox-upload {
  padding: 4px 0;
}
.inbox-upload :deep(.n-upload-dragger) {
  padding: 14px;
  border-radius: 8px;
  border: 1px dashed rgba(148, 163, 184, 0.4);
  background: rgba(248, 250, 252, 0.6);
  transition: all 0.15s;
}
.inbox-upload :deep(.n-upload-dragger:hover) {
  border-color: rgba(30, 64, 175, 0.4);
  background: rgba(30, 64, 175, 0.04);
}
.inbox-upload :deep(.n-upload-dragger--dragover) {
  border-color: #1e40af;
  background: rgba(30, 64, 175, 0.08);
}
.drag-zone {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  color: #94a3b8;
  font-size: 12px;
  user-select: none;
}
.drag-icon { font-size: 15px; }
.drag-text { font-weight: 500; }

</style>
