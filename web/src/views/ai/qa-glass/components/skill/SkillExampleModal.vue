<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import type { AgentSkill, AgentSession, AgentMessage, SkillExample } from '@/service/api';
import {
  fetchAgentSessions,
  fetchAgentMessages,
  fetchCreateSkillExampleFromSession,
  fetchUploadSkillExampleImage
} from '@/service/api';

const props = defineProps<{
  show: boolean;
  skill: AgentSkill;
}>();

const emit = defineEmits<{
  'update:show': [value: boolean];
  /** 提取成功：新案例 */
  added: [example: SkillExample];
}>();

// ── ① 会话选择 ─────────────────────────────────────────
const sessions = ref<AgentSession[]>([]);
const sessionsLoading = ref(false);
const query = ref('');
const selectedKey = ref('');
let searchTimer: ReturnType<typeof setTimeout> | null = null;

const selectedSession = computed(() => sessions.value.find(s => s.sessionKey === selectedKey.value) || null);

async function loadSessions(keyword?: string) {
  sessionsLoading.value = true;
  try {
    const { data, error } = await fetchAgentSessions(100, keyword ? { keyword } : undefined);
    if (!error && data) sessions.value = data;
  } finally {
    sessionsLoading.value = false;
  }
}

function onQueryInput() {
  if (searchTimer) clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    loadSessions(query.value.trim() || undefined);
  }, 320);
}

function selectSession(key: string) {
  if (selectedKey.value === key) return;
  selectedKey.value = key;
  // 标题未手改过时跟随会话标题
  if (!titleTouched.value) title.value = selectedSession.value?.title || '';
  loadPreview(key);
}

function fmtSessionTime(ts: number): string {
  const d = new Date(ts);
  const p = (n: number) => n.toString().padStart(2, '0');
  return `${d.getMonth() + 1}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

// ── ② 消息预览 ─────────────────────────────────────────
const previewMsgs = ref<Array<{ role: string; excerpt: string }>>([]);
const previewTotal = ref(0);
const previewLoading = ref(false);

async function loadPreview(sessionKey: string) {
  previewLoading.value = true;
  previewMsgs.value = [];
  previewTotal.value = 0;
  try {
    const { data, error } = await fetchAgentMessages(sessionKey);
    if (error || !data) return;
    const msgs = data as AgentMessage[];
    previewTotal.value = msgs.length;
    previewMsgs.value = msgs.slice(0, 8).map(m => ({
      role: m.role,
      excerpt: (m.content || '').replace(/\s+/g, ' ').trim().slice(0, 100)
    }));
  } finally {
    previewLoading.value = false;
  }
}

// ── ③ 案例信息 ─────────────────────────────────────────
const title = ref('');
const titleTouched = ref(false);
const desc = ref('');

// ── ④ 预览图 ───────────────────────────────────────────
const images = ref<string[]>([]);
const uploading = ref(false);
const fileInput = ref<HTMLInputElement | null>(null);

function pickImages() {
  fileInput.value?.click();
}

async function onFilesPicked(ev: Event) {
  const input = ev.target as HTMLInputElement;
  const files = Array.from(input.files || []).filter(f => f.type.startsWith('image/'));
  input.value = '';
  if (!files.length) return;
  uploading.value = true;
  try {
    for (const f of files) {
      try {
        const path = await fetchUploadSkillExampleImage(f);
        images.value = [...images.value, path];
      } catch (e) {
        window.$message?.error(`「${f.name}」上传失败：${(e as Error).message}`);
      }
    }
  } finally {
    uploading.value = false;
  }
}

function removeImage(i: number) {
  images.value = images.value.filter((_, idx) => idx !== i);
}

// ── 提交 ───────────────────────────────────────────────
const submitting = ref(false);
const canSubmit = computed(() => Boolean(selectedKey.value) && !submitting.value);

async function submit() {
  if (!canSubmit.value) return;
  submitting.value = true;
  try {
    const { data, error } = await fetchCreateSkillExampleFromSession(props.skill.id, {
      sessionKey: selectedKey.value,
      title: title.value.trim() || undefined,
      description: desc.value.trim() || undefined,
      previewImages: images.value.length ? images.value : undefined
    });
    if (!error && data) {
      emit('added', data);
      window.$message?.success('案例已添加');
      close();
    } else {
      window.$message?.error('提取案例失败');
    }
  } finally {
    submitting.value = false;
  }
}

function close() {
  emit('update:show', false);
}

function reset() {
  sessions.value = [];
  query.value = '';
  selectedKey.value = '';
  previewMsgs.value = [];
  previewTotal.value = 0;
  title.value = '';
  titleTouched.value = false;
  desc.value = '';
  images.value = [];
}

watch(
  () => props.show,
  open => {
    if (open) {
      reset();
      loadSessions();
    }
  }
);
</script>

<template>
  <Teleport to="body">
    <Transition name="sem-mask">
      <div v-if="show" class="sem-mask" @click="close" />
    </Transition>

    <Transition name="sem-card">
      <div v-if="show" class="sem-card" @click.stop>
        <header class="sem-head">
          <div>
            <h2 class="sem-title">从任务提取案例</h2>
            <p class="sem-sub">为「{{ skill.name }}」挑一段精彩任务，挂到技能卡片上供团队成员试用</p>
          </div>
          <button class="sem-close" title="关闭" @click="close">
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M4 4l8 8M12 4l-8 8" stroke-width="1.7" stroke-linecap="round" /></svg>
          </button>
        </header>

        <div class="sem-body">
          <!-- 左：会话选择 -->
          <section class="sem-sessions">
            <div class="sem-step-label">① 选择任务</div>
            <div class="sem-search">
              <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor">
                <circle cx="7" cy="7" r="4.5" stroke-width="1.6" />
                <path d="M10.5 10.5L14 14" stroke-width="1.6" stroke-linecap="round" />
              </svg>
              <input v-model="query" placeholder="搜索任务标题…" @input="onQueryInput" />
            </div>
            <div class="sem-session-list">
              <div v-if="sessionsLoading && !sessions.length" class="sem-list-hint">加载中…</div>
              <div v-else-if="!sessions.length" class="sem-list-hint">没有匹配的任务</div>
              <button
                v-for="s in sessions"
                :key="s.sessionKey"
                type="button"
                class="sem-session"
                :class="{ 'is-on': selectedKey === s.sessionKey }"
                @click="selectSession(s.sessionKey)"
              >
                <span class="sem-session-title">{{ s.title || '未命名任务' }}</span>
                <span class="sem-session-meta">
                  {{ fmtSessionTime(s.updatedAt) }} · {{ s.messageCount }} 条
                  <em v-if="s.isStarred">★</em>
                </span>
              </button>
            </div>
          </section>

          <!-- 右：预览 + 案例信息 + 预览图 -->
          <section class="sem-detail">
            <div class="sem-step-label">② 预览提取内容</div>
            <div class="sem-preview">
              <template v-if="!selectedKey">
                <div class="sem-list-hint">先在左侧选一段任务</div>
              </template>
              <template v-else-if="previewLoading">
                <div class="sem-list-hint">加载消息中…</div>
              </template>
              <template v-else>
                <div v-for="(m, i) in previewMsgs" :key="i" class="sem-msg" :class="`sem-msg--${m.role}`">
                  <span class="sem-msg-role">{{ m.role === 'user' ? '用户' : 'AI' }}</span>
                  <span class="sem-msg-text">{{ m.excerpt || '（附件/媒体消息）' }}</span>
                </div>
                <div class="sem-preview-foot">共 {{ previewTotal }} 条消息将完整提取（此处仅预览前 {{ previewMsgs.length }} 条）</div>
              </template>
            </div>

            <div class="sem-step-label">③ 案例信息</div>
            <input
              v-model="title"
              class="sem-input"
              placeholder="案例标题（不填用任务标题）"
              @input="titleTouched = true"
            />
            <input v-model="desc" class="sem-input" placeholder="一句话案例描述（可选，展示在橱窗卡片上）" />

            <div class="sem-step-label">④ 预览图（可选，橱窗卡片缩略图）</div>
            <div class="sem-images">
              <div v-for="(img, i) in images" :key="img" class="sem-thumb">
                <img :src="img" alt="" />
                <button class="sem-thumb-x" title="移除" @click="removeImage(i)">×</button>
              </div>
              <button class="sem-img-add" :disabled="uploading" @click="pickImages">
                <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M8 3v10M3 8h10" stroke-width="1.8" stroke-linecap="round" /></svg>
                {{ uploading ? '上传中…' : '上传图片' }}
              </button>
              <input ref="fileInput" type="file" accept="image/*" multiple hidden @change="onFilesPicked" />
            </div>
          </section>
        </div>

        <footer class="sem-foot">
          <span class="sem-foot-hint">提取后案例出现在商店该技能的卡片上，用户可点击 fork 试用</span>
          <div class="sem-foot-actions">
            <button class="sem-btn sem-btn--ghost" @click="close">取消</button>
            <button class="sem-btn sem-btn--primary" :disabled="!canSubmit" @click="submit">
              {{ submitting ? '提取中…' : '提取为案例' }}
            </button>
          </div>
        </footer>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
/* teleport 弹层脱离 .sk-panel 作用域，自带一套令牌（同 skill 面板取值） */
.sem-mask {
  position: fixed;
  inset: 0;
  z-index: 1502;
  background: rgba(15, 23, 42, 0.32);
  backdrop-filter: blur(2px);
}
.sem-card {
  --paper: #f5f7fb;
  --surface-strong: rgba(255, 255, 255, 0.62);
  --ink: #0f172a;
  --ink-2: #334155;
  --ink-3: #64748b;
  --ink-4: #94a3b8;
  --border: rgba(30, 64, 175, 0.1);
  --border-strong: rgba(30, 64, 175, 0.18);
  --line-hair: rgba(30, 64, 175, 0.1);
  --accent: #1e40af;
  --accent-soft: rgba(30, 64, 175, 0.08);
  --fill-hover: rgba(30, 64, 175, 0.06);
  --grad-brand: linear-gradient(110deg, #1e40af 0%, #2563eb 35%, #0ea5e9 70%, #0891b2 100%);
  --shadow-md: 0 1px 2px rgba(15, 23, 42, 0.05), 0 12px 32px -12px rgba(30, 64, 175, 0.13);

  position: fixed;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  z-index: 1503;
  width: min(880px, 94vw);
  max-height: 86vh;
  display: flex;
  flex-direction: column;
  background: var(--paper);
  border: 1px solid var(--border);
  border-radius: 16px;
  box-shadow: var(--shadow-md);
  color: var(--ink);
  overflow: hidden;
}
:root[data-qa-theme='ink'] .sem-card {
  --paper: #fbfbfc;
  --surface-strong: #ffffff;
  --ink: #17181a;
  --ink-2: #494a50;
  --ink-3: #7e7f86;
  --ink-4: #b9bac0;
  --border: rgba(31, 32, 36, 0.08);
  --border-strong: rgba(31, 32, 36, 0.15);
  --line-hair: rgba(31, 32, 36, 0.07);
  --accent: #17181a;
  --accent-soft: rgba(23, 24, 26, 0.05);
  --fill-hover: rgba(23, 24, 26, 0.045);
  --grad-brand: #131316;
}

/* 过渡 */
.sem-mask-enter-active,
.sem-mask-leave-active {
  transition: opacity 0.24s ease;
}
.sem-mask-enter-from,
.sem-mask-leave-to {
  opacity: 0;
}
.sem-card-enter-active {
  transition: all 0.28s cubic-bezier(0.22, 1, 0.36, 1);
}
.sem-card-leave-active {
  transition: all 0.18s ease;
}
.sem-card-enter-from {
  opacity: 0;
  transform: translate(-50%, -47%) scale(0.98);
}
.sem-card-leave-to {
  opacity: 0;
  transform: translate(-50%, -50%) scale(0.985);
}

/* 头部 */
.sem-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 18px 22px 14px;
  border-bottom: 1px solid var(--line-hair);
  flex-shrink: 0;
}
.sem-title {
  margin: 0;
  font-size: 17px;
  font-weight: 800;
  letter-spacing: -0.01em;
}
.sem-sub {
  margin: 4px 0 0;
  font-size: 12.5px;
  color: var(--ink-3);
}
.sem-close {
  flex-shrink: 0;
  width: 30px;
  height: 30px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: 8px;
  background: var(--fill-hover);
  color: var(--ink-3);
  cursor: pointer;
  transition: all 0.15s;
}
.sem-close:hover {
  background: var(--accent-soft);
  color: var(--ink);
}

/* 主体：左右两栏 */
.sem-body {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 300px 1fr;
  gap: 0;
}

/* 左栏：会话选择 */
.sem-sessions {
  display: flex;
  flex-direction: column;
  min-height: 0;
  padding: 14px 14px 14px 22px;
  border-right: 1px solid var(--line-hair);
}
.sem-step-label {
  font-size: 11.5px;
  font-weight: 700;
  color: var(--ink-3);
  margin-bottom: 8px;
  flex-shrink: 0;
}
.sem-search {
  display: flex;
  align-items: center;
  gap: 7px;
  height: 34px;
  padding: 0 11px;
  border: 1px solid var(--border);
  border-radius: 9px;
  background: var(--surface-strong);
  color: var(--ink-4);
  margin-bottom: 10px;
  flex-shrink: 0;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.sem-search:focus-within {
  border-color: color-mix(in srgb, var(--accent) 45%, transparent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}
.sem-search input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  font-family: inherit;
  font-size: 12.5px;
  color: var(--ink);
}
.sem-search input::placeholder {
  color: var(--ink-4);
}
.sem-session-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  scrollbar-gutter: stable; /* 预留滚动条槽位，内容增减不抖 */
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-right: 4px;
}
.sem-session-list::-webkit-scrollbar {
  width: 5px;
}
.sem-session-list::-webkit-scrollbar-thumb {
  background: var(--border-strong);
  border-radius: 3px;
}
.sem-list-hint {
  padding: 20px 8px;
  text-align: center;
  font-size: 12px;
  color: var(--ink-4);
}
.sem-session {
  display: flex;
  flex-direction: column;
  gap: 3px;
  text-align: left;
  font-family: inherit;
  padding: 9px 11px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface-strong);
  cursor: pointer;
  transition: all 0.14s;
  flex-shrink: 0;
}
.sem-session:hover {
  border-color: var(--border-strong);
}
.sem-session.is-on {
  border-color: color-mix(in srgb, var(--accent) 50%, transparent);
  background: var(--accent-soft);
  box-shadow: 0 0 0 2px var(--accent-soft);
}
.sem-session-title {
  font-size: 12.5px;
  font-weight: 650;
  color: var(--ink-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sem-session.is-on .sem-session-title {
  color: var(--accent);
}
.sem-session-meta {
  font-size: 10.5px;
  color: var(--ink-4);
  display: flex;
  align-items: center;
  gap: 4px;
}
.sem-session-meta em {
  font-style: normal;
  color: #b45309;
}

/* 右栏：预览 + 信息 + 图 */
.sem-detail {
  display: flex;
  flex-direction: column;
  min-height: 0;
  padding: 14px 22px 14px 16px;
  overflow-y: auto;
  scrollbar-gutter: stable; /* 预留滚动条槽位，内容增减不抖 */
}
.sem-preview {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface-strong);
  margin-bottom: 14px;
  max-height: 240px;
  overflow-y: auto;
  scrollbar-gutter: stable; /* 预留滚动条槽位，内容增减不抖 */
}
.sem-preview::-webkit-scrollbar {
  width: 5px;
}
.sem-preview::-webkit-scrollbar-thumb {
  background: var(--border-strong);
  border-radius: 3px;
}
.sem-msg {
  display: flex;
  gap: 8px;
  font-size: 12px;
  line-height: 1.55;
}
.sem-msg-role {
  flex-shrink: 0;
  font-weight: 700;
  font-size: 10.5px;
  padding: 1px 7px;
  border-radius: 20px;
  height: fit-content;
  margin-top: 1px;
}
.sem-msg--user .sem-msg-role {
  color: var(--accent);
  background: var(--accent-soft);
}
.sem-msg--assistant .sem-msg-role {
  color: #047857;
  background: rgba(5, 150, 105, 0.1);
}
.sem-msg-text {
  color: var(--ink-2);
  word-break: break-all;
}
.sem-preview-foot {
  font-size: 11px;
  color: var(--ink-4);
  padding-top: 4px;
  border-top: 1px dashed var(--line-hair);
}
.sem-input {
  width: 100%;
  height: 34px;
  padding: 0 11px;
  border: 1px solid var(--border);
  border-radius: 9px;
  background: var(--surface-strong);
  font-family: inherit;
  font-size: 12.5px;
  color: var(--ink);
  outline: none;
  margin-bottom: 10px;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.sem-input:focus {
  border-color: color-mix(in srgb, var(--accent) 45%, transparent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}
.sem-input::placeholder {
  color: var(--ink-4);
}
.sem-images {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.sem-thumb {
  position: relative;
  width: 56px;
  height: 56px;
  border-radius: 9px;
  overflow: hidden;
  border: 1px solid var(--border);
}
.sem-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.sem-thumb-x {
  position: absolute;
  top: 2px;
  right: 2px;
  width: 16px;
  height: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: 50%;
  background: rgba(15, 23, 42, 0.62);
  color: #fff;
  font-size: 11px;
  line-height: 1;
  cursor: pointer;
}
.sem-img-add {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 56px;
  padding: 0 14px;
  border: 1px dashed var(--border-strong);
  border-radius: 9px;
  background: transparent;
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-3);
  cursor: pointer;
  transition: all 0.15s;
}
.sem-img-add:hover:not(:disabled) {
  border-color: color-mix(in srgb, var(--accent) 45%, transparent);
  color: var(--accent);
  background: var(--accent-soft);
}
.sem-img-add:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

/* 底部 */
.sem-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 22px;
  border-top: 1px solid var(--line-hair);
  flex-shrink: 0;
}
.sem-foot-hint {
  font-size: 11.5px;
  color: var(--ink-4);
}
.sem-foot-actions {
  display: flex;
  gap: 8px;
}
.sem-btn {
  font-family: inherit;
  font-size: 12.5px;
  font-weight: 700;
  padding: 8px 18px;
  border-radius: 9px;
  cursor: pointer;
  transition: all 0.15s;
}
.sem-btn--ghost {
  border: 1px solid var(--border-strong);
  background: var(--surface-strong);
  color: var(--ink-2);
}
.sem-btn--ghost:hover {
  background: var(--fill-hover);
}
.sem-btn--primary {
  border: none;
  color: #fff;
  background: var(--grad-brand);
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.05);
}
.sem-btn--primary:hover:not(:disabled) {
  filter: brightness(0.96);
}
.sem-btn--primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

@media (max-width: 760px) {
  .sem-body {
    grid-template-columns: 1fr;
  }
  .sem-sessions {
    border-right: none;
    border-bottom: 1px solid var(--line-hair);
    max-height: 240px;
  }
}
</style>
