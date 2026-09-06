<script setup lang="ts">
import { computed, ref } from 'vue';
import { customIconHtml } from './skill-icon';

/**
 * 图标选择器（技能 / 连接器共用）：
 * - 上传用户图片：客户端压缩（长边 ≤512px）转 data URI，随 icon 字段入库
 * - 粘贴 SVG 源码（agent 生成的 svg 同款存法）
 * - 清除图标（emit null，父级按「空串=清除」落库）
 */
const props = defineProps<{
  /** 当前图标：svg 源码 / 图片 data URI / null 未设置 */
  icon: string | null;
}>();

const emit = defineEmits<{
  (e: 'update:icon', v: string | null): void;
}>();

const fileInput = ref<HTMLInputElement | null>(null);
const svgOpen = ref(false);
const svgText = ref('');

const previewHtml = computed(() => customIconHtml(props.icon));

function pickFile() {
  fileInput.value?.click();
}

async function onFile(e: Event) {
  const input = e.target as HTMLInputElement;
  const f = input.files?.[0];
  input.value = '';
  if (!f) return;
  if (!f.type.startsWith('image/')) {
    window.$message?.error('请选择图片文件（png / jpg / webp / gif）');
    return;
  }
  const dataUri = await compressToDataUri(f);
  if (!dataUri) {
    window.$message?.error('图片读取失败');
    return;
  }
  emit('update:icon', dataUri);
  window.$message?.success('图标已更新');
}

/** 客户端压缩：长边缩到 ≤512px 再 base64（png 保透明通道，其余转 jpeg） */
function compressToDataUri(file: File): Promise<string | null> {
  return new Promise(resolve => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      try {
        const MAX = 512;
        const scale = Math.min(1, MAX / Math.max(img.width, img.height));
        const w = Math.max(1, Math.round(img.width * scale));
        const h = Math.max(1, Math.round(img.height * scale));
        const canvas = document.createElement('canvas');
        canvas.width = w;
        canvas.height = h;
        const ctx = canvas.getContext('2d');
        if (!ctx) {
          resolve(null);
          return;
        }
        if (file.type === 'image/png') {
          ctx.drawImage(img, 0, 0, w, h);
          resolve(canvas.toDataURL('image/png'));
        } else {
          ctx.fillStyle = '#ffffff';
          ctx.fillRect(0, 0, w, h);
          ctx.drawImage(img, 0, 0, w, h);
          resolve(canvas.toDataURL('image/jpeg', 0.85));
        }
      } finally {
        URL.revokeObjectURL(url);
      }
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      resolve(null);
    };
    img.src = url;
  });
}

function applySvg() {
  const t = svgText.value.trim();
  if (!/^<svg[\s>]/i.test(t)) {
    window.$message?.error('请粘贴以 <svg> 开头的 SVG 元素源码');
    return;
  }
  emit('update:icon', t);
  svgOpen.value = false;
  svgText.value = '';
  window.$message?.success('图标已更新');
}
</script>

<template>
  <div class="ip">
    <div class="ip-row">
      <span class="ip-preview" :class="{ 'ip-preview--empty': !previewHtml }">
        <span v-if="previewHtml" v-html="previewHtml"></span>
        <svg v-else width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" aria-hidden="true">
          <path d="M8 1.8l1.5 3.1 3.4.5-2.5 2.4.6 3.4L8 9.6l-3 1.6.6-3.4-2.5-2.4 3.4-.5L8 1.8z" stroke-width="1.3" stroke-linejoin="round" />
        </svg>
      </span>
      <div class="ip-actions">
        <button type="button" class="ip-btn" @click="pickFile">上传图片</button>
        <button type="button" class="ip-btn" :class="{ 'ip-btn--on': svgOpen }" @click="svgOpen = !svgOpen">粘贴 SVG</button>
        <button v-if="icon" type="button" class="ip-btn ip-btn--danger" @click="emit('update:icon', null)">清除</button>
      </div>
    </div>
    <input ref="fileInput" hidden type="file" accept="image/*" @change="onFile" />
    <div v-if="svgOpen" class="ip-svg">
      <textarea v-model="svgText" class="ip-svg-input" rows="3" placeholder='粘贴单个 <svg> 元素源码，如 <svg viewBox="0 0 24 24">…</svg>'></textarea>
      <button type="button" class="ip-btn ip-btn--primary" :disabled="!svgText.trim()" @click="applySvg">应用</button>
    </div>
  </div>
</template>

<style scoped>
.ip {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.ip-row {
  display: flex;
  align-items: center;
  gap: 12px;
}
.ip-preview {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  border-radius: 11px;
  background: var(--surface-strong);
  border: 1px solid var(--border);
  color: var(--ink-4);
  overflow: hidden;
}
.ip-preview :deep(svg) {
  width: 26px;
  height: 26px;
}
.ip-preview :deep(img) {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.ip-actions {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
}
.ip-btn {
  font-family: inherit;
  font-size: 11.5px;
  font-weight: 600;
  color: var(--ink-2);
  background: var(--surface-strong);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 4px 11px;
  cursor: pointer;
  transition: all 0.15s;
}
.ip-btn:hover:not(:disabled) {
  background: var(--fill-hover);
  border-color: var(--border-strong);
}
.ip-btn--on {
  color: var(--accent);
  border-color: color-mix(in srgb, var(--accent) 34%, transparent);
  background: var(--accent-soft);
}
.ip-btn--primary {
  color: var(--on-primary);
  background: var(--grad-brand);
  border-color: transparent;
}
.ip-btn--primary:hover:not(:disabled) {
  background: var(--grad-brand);
  filter: brightness(0.96);
}
.ip-btn--primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.ip-btn--danger {
  color: #dc2626;
  border-color: rgba(220, 38, 38, 0.2);
  background: rgba(220, 38, 38, 0.04);
}
.ip-btn--danger:hover {
  background: rgba(220, 38, 38, 0.1);
  border-color: rgba(220, 38, 38, 0.34);
}
.ip-svg {
  display: flex;
  flex-direction: column;
  gap: 7px;
  align-items: flex-start;
}
.ip-svg-input {
  width: 100%;
  font-family: var(--font-mono);
  font-size: 11.5px;
  color: var(--ink);
  background: var(--surface-strong);
  border: 1px solid var(--border);
  border-radius: 9px;
  padding: 8px 11px;
  outline: none;
  resize: vertical;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.ip-svg-input:focus {
  border-color: color-mix(in srgb, var(--accent) 45%, transparent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}
</style>
