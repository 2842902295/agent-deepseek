<script setup lang="ts">
import { reactive, ref, watch } from 'vue';
import { useAuthStore } from '@/store/modules/auth';
import { fetchUpdateMyProfile } from '@/service/api';

const props = defineProps<{ show: boolean }>();

const emit = defineEmits<{
  'update:show': [value: boolean];
}>();

const authStore = useAuthStore();

const GENDERS = [
  { label: '男', value: '1' },
  { label: '女', value: '2' },
  { label: '保密', value: '3' }
];

const form = reactive({
  nickName: '',
  userGender: '3',
  userEmail: '',
  userPhone: ''
});

/** 打开时的手机号原值：用于拦截「清空手机号」（后端不支持在此清空） */
let origPhone = '';
const saving = ref(false);

watch(
  () => props.show,
  v => {
    if (!v) return;
    form.nickName = authStore.userInfo.nickName || '';
    form.userGender = authStore.userInfo.userGender || '3';
    form.userEmail = authStore.userInfo.userEmail || '';
    form.userPhone = authStore.userInfo.userPhone || '';
    origPhone = authStore.userInfo.userPhone || '';
  }
);

function close() {
  if (saving.value) return;
  emit('update:show', false);
}

async function save() {
  const nick = form.nickName.trim();
  if (!nick) {
    window.$message?.warning('请输入昵称');
    return;
  }
  if (nick.length > 30) {
    window.$message?.warning('昵称不能超过 30 个字符');
    return;
  }
  const email = form.userEmail.trim();
  if (email && !email.includes('@')) {
    window.$message?.warning('邮箱格式不正确');
    return;
  }
  const phone = form.userPhone.trim();
  if (!phone && origPhone) {
    window.$message?.warning('手机号不支持清空');
    return;
  }
  if (phone && !/^1[3-9]\d{9}$/.test(phone)) {
    window.$message?.warning('手机号格式不正确');
    return;
  }

  saving.value = true;
  const { data, error } = await fetchUpdateMyProfile({
    nickName: nick,
    userGender: form.userGender,
    userEmail: email || null,
    userPhone: phone || null
  });
  saving.value = false;

  if (error) return; // 业务错误 msg 由 request 层自动弹出，弹窗保持打开
  if (data) {
    // GET /user-info 有 60s 缓存：用保存响应直接回填 store
    Object.assign(authStore.userInfo, data);
  }
  window.$message?.success('资料已更新');
  emit('update:show', false);
}
</script>

<template>
  <NModal :show="show" :mask-closable="true" @update:show="v => !v && close()">
    <div class="pfm-card" role="dialog" aria-label="编辑个人资料">
      <div class="pfm-glow" aria-hidden="true" />

      <header class="pfm-head">
        <span class="pfm-mark" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="8" r="4" />
            <path d="M4 21c0-4 3.6-6.5 8-6.5s8 2.5 8 6.5" />
          </svg>
        </span>
        <div class="pfm-titles">
          <h3 class="pfm-title">编辑个人资料</h3>
          <p class="pfm-sub">PROFILE · 昵称 / 性别 / 邮箱 / 手机号</p>
        </div>
        <button class="pfm-close" title="关闭" @click="close">×</button>
      </header>

      <div class="pfm-body">
        <label class="pfm-field">
          <span class="pfm-label">昵称 <i class="pfm-req">*</i></span>
          <input v-model="form.nickName" class="pfm-input" type="text" maxlength="30" placeholder="请输入昵称" />
        </label>

        <div class="pfm-field">
          <span class="pfm-label">性别</span>
          <div class="pfm-seg">
            <button
              v-for="g in GENDERS"
              :key="g.value"
              type="button"
              class="pfm-seg-item"
              :class="{ on: form.userGender === g.value }"
              @click="form.userGender = g.value"
            >
              {{ g.label }}
            </button>
          </div>
        </div>

        <label class="pfm-field">
          <span class="pfm-label">邮箱</span>
          <input v-model="form.userEmail" class="pfm-input" type="text" maxlength="255" placeholder="选填" />
        </label>

        <label class="pfm-field">
          <span class="pfm-label">手机号</span>
          <input v-model="form.userPhone" class="pfm-input" type="tel" maxlength="11" placeholder="用于登录，换号后需用新号登录" />
        </label>
      </div>

      <footer class="pfm-foot">
        <button class="pfm-btn pfm-btn--ghost" type="button" :disabled="saving" @click="close">取消</button>
        <button class="pfm-btn pfm-btn--primary" type="button" :disabled="saving" @click="save">
          {{ saving ? '保存中…' : '保存' }}
        </button>
      </footer>
    </div>
  </NModal>
</template>

<style scoped>
/* 弹窗 teleport 到 body，脱离 .qa-shell 作用域，这里复刻其玻璃设计变量（同 SessionSearchModal） */
.pfm-card {
  --paper: #f5f7fb;
  --surface: rgba(255, 255, 255, 0.55);
  --surface-strong: rgba(255, 255, 255, 0.72);
  --ink: #0f172a;
  --ink-2: #334155;
  --ink-3: #64748b;
  --ink-4: #94a3b8;
  --accent: #1e40af;
  --accent-2: #2563eb;
  --accent-soft: rgba(30, 64, 175, 0.08);
  --aurora: linear-gradient(110deg, #1e40af 0%, #2563eb 35%, #0ea5e9 70%, #0891b2 100%);
  --font-display: 'Plus Jakarta Sans', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', system-ui, sans-serif;
  --font-mono: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace;

  position: relative;
  width: 460px;
  max-width: 92vw;
  max-height: 84vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border-radius: 20px;
  background: var(--surface);
  backdrop-filter: blur(40px) saturate(200%);
  -webkit-backdrop-filter: blur(40px) saturate(200%);
  font-family: var(--font-display);
  color: var(--ink);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.95),
    inset 0 0 0 1px rgba(255, 255, 255, 0.4),
    0 1px 2px rgba(15, 23, 42, 0.06),
    0 24px 64px -20px rgba(30, 64, 175, 0.32);
  animation: pfm-pop 0.32s cubic-bezier(0.32, 0.72, 0, 1);
}

@keyframes pfm-pop {
  from {
    opacity: 0;
    transform: translateY(14px) scale(0.975);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

.pfm-glow {
  position: absolute;
  top: -70px;
  left: 50%;
  width: 300px;
  height: 150px;
  transform: translateX(-50%);
  background: var(--aurora);
  opacity: 0.16;
  filter: blur(48px);
  pointer-events: none;
}

/* ─── 头部 ─── */
.pfm-head {
  position: relative;
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 22px 24px 16px;
  border-bottom: 1px solid rgba(30, 64, 175, 0.08);
}

.pfm-mark {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  border-radius: 12px;
  background: var(--aurora);
  color: #fff;
  flex-shrink: 0;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.5),
    0 4px 14px -4px rgba(30, 64, 175, 0.55);
}

.pfm-mark svg {
  width: 18px;
  height: 18px;
}

.pfm-titles {
  flex: 1;
  min-width: 0;
}

.pfm-title {
  margin: 0;
  font-size: 17px;
  font-weight: 700;
  letter-spacing: -0.02em;
  line-height: 1.2;
  background: var(--aurora);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}

.pfm-sub {
  margin: 3px 0 0;
  font-family: var(--font-mono);
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.16em;
  color: var(--ink-3);
}

.pfm-close {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: none;
  border-radius: 10px;
  background: transparent;
  color: var(--ink-3);
  font-size: 19px;
  line-height: 1;
  cursor: pointer;
  transition: all 0.18s ease;
  flex-shrink: 0;
}

.pfm-close:hover {
  background: rgba(185, 28, 28, 0.08);
  color: #b91c1c;
  transform: rotate(90deg);
}

/* ─── 表单 ─── */
.pfm-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 18px 24px 4px;
}

.pfm-field {
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.pfm-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-2);
}

.pfm-req {
  font-style: normal;
  color: #dc2626;
}

.pfm-input {
  width: 100%;
  height: 40px;
  padding: 0 14px;
  border: 1px solid rgba(30, 64, 175, 0.14);
  border-radius: 12px;
  background: var(--surface-strong);
  font-family: var(--font-display);
  font-size: 13px;
  color: var(--ink);
  outline: none;
  transition: all 0.2s ease;
}

.pfm-input::placeholder {
  color: var(--ink-4);
}

.pfm-input:focus {
  border-color: rgba(30, 64, 175, 0.35);
  background: rgba(255, 255, 255, 0.85);
  box-shadow: 0 0 0 3px rgba(30, 64, 175, 0.12);
}

/* 性别分段选择 */
.pfm-seg {
  display: flex;
  gap: 8px;
}

.pfm-seg-item {
  flex: 1;
  height: 36px;
  border: 1px solid rgba(30, 64, 175, 0.14);
  border-radius: 12px;
  background: var(--surface-strong);
  font-family: var(--font-display);
  font-size: 13px;
  color: var(--ink-2);
  cursor: pointer;
  transition: all 0.18s ease;
}

.pfm-seg-item:hover {
  border-color: rgba(30, 64, 175, 0.35);
  color: var(--accent);
}

.pfm-seg-item.on {
  background: var(--accent-soft);
  border-color: var(--accent);
  color: var(--accent);
  font-weight: 600;
  box-shadow: 0 0 0 3px rgba(30, 64, 175, 0.08);
}

/* ─── 底部按钮 ─── */
.pfm-foot {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 18px 24px 20px;
}

.pfm-btn {
  height: 38px;
  padding: 0 22px;
  border-radius: 12px;
  font-family: var(--font-display);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.18s ease;
}

.pfm-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.pfm-btn--ghost {
  border: 1px solid rgba(30, 64, 175, 0.18);
  background: transparent;
  color: var(--ink-2);
}

.pfm-btn--ghost:hover:not(:disabled) {
  background: var(--accent-soft);
  color: var(--accent);
}

.pfm-btn--primary {
  border: none;
  background: var(--aurora);
  color: #fff;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.35),
    0 6px 18px -6px rgba(30, 64, 175, 0.55);
}

.pfm-btn--primary:hover:not(:disabled) {
  filter: brightness(1.06);
  transform: translateY(-1px);
}
</style>
