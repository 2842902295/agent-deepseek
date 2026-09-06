<script setup lang="ts">
import { ref } from 'vue';
import { fetchUploadAgentSkill } from '@/service/api';
import type { UploadSkillChange } from '@/service/api';

const emit = defineEmits<{
  /** 一次上传完成（新增或升级），携带技能 key 供外层刷新数据/高亮（不触发跳转） */
  uploaded: [skillKey: string];
  /** 点击「查看我的技能」：跳转到我的技能页并高亮最近上传的技能 */
  gotoMine: [];
}>();

const dragOver = ref(false);
const uploading = ref(false);
const progress = ref(0);
const fileInput = ref<HTMLInputElement | null>(null);
// 本次进入页面以来的上传变动记录（最新在前）
const changes = ref<UploadSkillChange[]>([]);

function triggerSelect() {
  if (!uploading.value) fileInput.value?.click();
}

function onDragOver(e: DragEvent) {
  e.preventDefault();
  dragOver.value = true;
}
function onDragLeave() {
  dragOver.value = false;
}
function onDrop(e: DragEvent) {
  e.preventDefault();
  dragOver.value = false;
  if (uploading.value) return;
  const file = e.dataTransfer?.files?.[0];
  if (file) upload(file);
}

function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = '';
  if (file) upload(file);
}

async function upload(file: File) {
  if (!file.name.toLowerCase().endsWith('.zip')) {
    window.$message?.error('请上传 .zip 技能包');
    return;
  }
  if (file.size > 50 * 1024 * 1024) {
    window.$message?.error('文件不能超过 50MB');
    return;
  }
  uploading.value = true;
  progress.value = 0;
  try {
    const res = await fetchUploadAgentSkill(file, p => {
      progress.value = p;
    });
    window.$message?.success(res.message || '上传成功');
    if (res.change && res.change.action !== 'failed') {
      changes.value.unshift(res.change);
      if (res.change.skillKey) emit('uploaded', res.change.skillKey);
    }
  } catch (err) {
    window.$message?.error((err as Error).message || '上传失败');
  } finally {
    uploading.value = false;
  }
}
</script>

<template>
  <div class="su">
    <div class="su-scroll">
      <!-- 拖拽 / 点击上传区 -->
      <div
        class="su-drop"
        :class="{ 'su-drop-over': dragOver, 'su-drop-busy': uploading }"
        @click="triggerSelect"
        @dragover="onDragOver"
        @dragleave="onDragLeave"
        @drop="onDrop"
      >
        <input ref="fileInput" type="file" accept=".zip" class="su-file-input" @change="onFileChange" />

        <template v-if="uploading">
          <span class="su-drop-spin" />
          <div class="su-drop-title">正在上传… {{ progress }}%</div>
          <div class="su-progress">
            <span class="su-progress-fill" :style="{ width: progress + '%' }" />
          </div>
        </template>
        <template v-else>
          <svg class="su-drop-icon" width="26" height="26" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M8 11V3M5 6l3-3 3 3" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /><path d="M3 12.5h10" stroke-width="1.6" stroke-linecap="round" /></svg>
          <div class="su-drop-title">点击选择，或拖拽 .zip 技能包到此处</div>
          <div class="su-drop-sub">包内需含 SKILL.md（支持带一层文件夹包裹）· 同名技能自动升级版本 · ≤ 50MB</div>
        </template>
      </div>

      <!-- 本次上传变动 -->
      <template v-if="changes.length">
        <div class="su-changes-head">
          <span class="su-changes-label">本次上传变动</span>
          <span class="su-changes-line" />
          <span class="su-changes-count">{{ changes.length }}</span>
          <button class="su-goto-mine" @click="emit('gotoMine')">
            查看我的技能
            <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M6 3l5 5-5 5" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" /></svg>
          </button>
        </div>

        <div class="su-changes">
          <div v-for="(c, i) in changes" :key="i" class="su-change">
            <span class="su-change-badge" :class="c.action === 'created' ? 'su-badge-new' : 'su-badge-up'">
              {{ c.action === 'created' ? '新增' : '升级' }}
            </span>
            <div class="su-change-main">
              <div class="su-change-title">
                {{ c.action === 'created' ? '新增了一个技能包' : '升级了一个技能包的版本' }}
                <code>@{{ c.skillKey }}</code>
                <span v-if="c.name" class="su-change-name">{{ c.name }}</span>
              </div>
              <div class="su-change-meta">
                <template v-if="c.action === 'upgraded'">
                  <span class="su-ver">{{ c.oldVersion || '?' }}</span>
                  <span class="su-arrow">→</span>
                  <span class="su-ver su-ver-new">{{ c.newVersion || '?' }}</span>
                </template>
                <template v-else>
                  <span class="su-ver su-ver-new">v{{ c.newVersion || '1.0.0' }}</span>
                </template>
                <span class="su-sep">·</span>
                <span>{{ c.fileCount }} 个文件</span>
              </div>
            </div>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.su {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}
.su-scroll {
  flex: 1;
  overflow-y: auto;
  scrollbar-gutter: stable; /* 预留滚动条槽位，内容增减不抖 */
  padding: 16px 24px 16px;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.su-scroll::-webkit-scrollbar {
  width: 5px;
}
.su-scroll::-webkit-scrollbar-thumb {
  background: var(--border-strong);
  border-radius: 3px;
}

/* ─── 拖拽 / 点击上传区 ─────────────────────────────────── */
.su-drop {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 34px 20px;
  border: 1.5px dashed var(--border-strong);
  border-radius: 12px;
  background: var(--surface);
  cursor: pointer;
  transition: all 0.16s;
}
.su-drop:hover {
  border-color: color-mix(in srgb, var(--accent) 45%, transparent);
  background: var(--fill-hover);
}
.su-drop-over {
  border-color: var(--accent);
  border-style: solid;
  background: var(--accent-soft);
  box-shadow: 0 0 0 4px var(--accent-soft);
}
.su-drop-busy {
  cursor: default;
}
.su-drop-icon {
  color: var(--accent, #1e40af);
  opacity: 0.8;
}
.su-drop-title {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--ink-2, #334155);
}
.su-drop-sub {
  font-size: 11.5px;
  color: var(--ink-4, #94a3b8);
}
.su-file-input {
  display: none;
}
.su-drop-spin {
  width: 18px;
  height: 18px;
  border: 2px solid color-mix(in srgb, var(--accent) 20%, transparent);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: su-rotate 0.7s linear infinite;
}
@keyframes su-rotate {
  to {
    transform: rotate(360deg);
  }
}
.su-progress {
  width: min(320px, 80%);
  height: 4px;
  border-radius: 2px;
  background: var(--fill-2);
  overflow: hidden;
}
.su-progress-fill {
  display: block;
  height: 100%;
  border-radius: 2px;
  background: var(--grad-brand);
  transition: width 0.15s;
}

/* ─── 本次上传变动 ─────────────────────────────────────── */
.su-changes-head {
  display: flex;
  align-items: center;
  gap: 10px;
}
/* 区块标题：中文平实语言，对齐详情页 sd-eyebrow */
.su-changes-label {
  font-family: var(--font-body);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: normal;
  color: var(--ink-3, #64748b);
}
.su-changes-line {
  flex: 1;
  height: 1px;
  background: var(--line-hair);
}
.su-changes-count {
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: 10px;
  font-weight: 700;
  color: var(--ink-4, #94a3b8);
}
/* 查看我的技能：变动区头部右侧跳转入口 */
.su-goto-mine {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-left: auto;
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  color: var(--accent, #1e40af);
  background: var(--accent-soft, rgba(30, 64, 175, 0.08));
  border: 1px solid color-mix(in srgb, var(--accent, #1e40af) 18%, transparent);
  border-radius: 20px;
  padding: 4px 12px;
  cursor: pointer;
  transition: all 0.15s;
}
.su-goto-mine:hover {
  background: color-mix(in srgb, var(--accent, #1e40af) 14%, transparent);
  border-color: color-mix(in srgb, var(--accent, #1e40af) 34%, transparent);
}

.su-changes {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.su-change {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 12px 14px;
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: 10px;
}
.su-change-badge {
  flex-shrink: 0;
  font-size: 11px;
  font-weight: 700;
  padding: 3px 9px;
  border-radius: 20px;
  margin-top: 1px;
}
.su-badge-new {
  color: #059669;
  background: rgba(5, 150, 105, 0.1);
}
.su-badge-up {
  color: var(--accent);
  background: var(--accent-soft);
}
.su-change-main {
  flex: 1;
  min-width: 0;
}
.su-change-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink, #0f172a);
  line-height: 1.5;
}
.su-change-title code {
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  color: var(--accent, #1e40af);
  background: var(--accent-soft);
  padding: 1px 5px;
  border-radius: 4px;
  margin-left: 2px;
}
.su-change-name {
  font-size: 12px;
  font-weight: 500;
  color: var(--ink-3, #64748b);
  margin-left: 6px;
}
.su-change-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 5px;
  font-size: 12px;
  color: var(--ink-3, #64748b);
}
.su-ver {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11.5px;
  background: var(--fill-hover);
  padding: 1px 6px;
  border-radius: 4px;
}
.su-ver-new {
  color: #059669;
  background: rgba(5, 150, 105, 0.08);
}
.su-arrow {
  color: var(--ink-4, #94a3b8);
}
.su-sep {
  color: var(--ink-4, #94a3b8);
}
</style>
