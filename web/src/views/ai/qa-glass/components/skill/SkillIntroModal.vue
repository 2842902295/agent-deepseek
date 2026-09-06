<script setup lang="ts">
defineProps<{ show: boolean }>();
const emit = defineEmits<{
  /** ×/点遮罩：仅关闭，不算知晓，下次点击仍会展示 */
  dismiss: [];
  /** 「知道了」：用户显式确认知晓，之后不再展示 */
  ack: [];
  /** 「现在就创建」：确认知晓并直接进入创建流程 */
  start: [];
}>();
</script>

<template>
  <Teleport to="body">
    <Transition name="si-mask">
      <div v-if="show" class="si-mask" @click="emit('dismiss')" />
    </Transition>

    <Transition name="si-card">
      <div v-if="show" class="si-card" @click.stop>
        <header class="si-head">
          <div class="si-head-main">
            <span class="si-eyebrow">新功能</span>
            <h2 class="si-title">你可以创建自己的技能了</h2>
            <p class="si-sub">讲出你的经验，AI 自动凝练整理成可复用技能——不用写任何文档</p>
          </div>
          <button class="si-close" title="关闭" @click="emit('dismiss')">
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M4 4l8 8M12 4l-8 8" stroke-width="1.7" stroke-linecap="round" /></svg>
          </button>
        </header>

        <!-- 四步流程 -->
        <div class="si-steps">
          <div class="si-step">
            <span class="si-step-num">01</span>
            <div class="si-step-body">
              <p class="si-step-title">启 · 两种方式，想做就做</p>
              <p class="si-step-desc">
                <b>方式一 · 一键凝练</b>：聊完一轮任务后，点右上角 <code>凝练为技能</code> 按钮，AI 自动把这轮任务的做法提炼成技能。<br />
                <b>方式二 · 直接开口</b>：对话里直接说 <code>帮我凝练为一个技能吧</code>，把攒下的经验、要求讲给 AI——一遍讲不完可以多聊几轮，比如「以后会议记录发你，按『决议、待办、风险』三段总结，待办标责任人和截止时间」
              </p>
            </div>
          </div>
          <div class="si-step">
            <span class="si-step-num">02</span>
            <div class="si-step-body">
              <p class="si-step-title">炼 · AI 自动凝练整理</p>
              <p class="si-step-desc">AI 把你零散的经验结构化，凝练成规范的技能文档存入技能库——不用自己写任何文档</p>
            </div>
          </div>
          <div class="si-step">
            <span class="si-step-num">03</span>
            <div class="si-step-body">
              <p class="si-step-title">用 · @ 随叫随到</p>
              <p class="si-step-desc">在任意任务输入 @技能名，AI 就按这套方法办事；「技能商店」里可添加到我的技能、随时启用/禁用</p>
            </div>
          </div>
          <div class="si-step">
            <span class="si-step-num">04</span>
            <div class="si-step-body">
              <p class="si-step-title">改 · @编辑 随时调教</p>
              <p class="si-step-desc">想调整已有技能？任务里说 <code>@编辑 @技能名</code> 加上你想改的点，AI 帮你改好</p>
            </div>
          </div>
        </div>

        <!-- 底部按钮：仅这两个按钮算「知晓」；×/遮罩关闭下次仍会展示 -->
        <footer class="si-foot">
          <button class="si-btn si-btn--ghost" @click="emit('ack')">知道了</button>
          <button class="si-btn si-btn--primary" @click="emit('start')">现在就创建</button>
        </footer>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
/* 层级与技能面板抽屉一致（1500/1501），低于 naive-ui teleport 层 */
.si-mask {
  position: fixed;
  inset: 0;
  z-index: 1500;
  background: rgba(15, 23, 42, 0.32);
  backdrop-filter: blur(3px);
  -webkit-backdrop-filter: blur(3px);
}

.si-card {
  /* ─── 双主题令牌（同 SessionSearchModal .ssm-card 模式：独立 teleport 弹层，
     自带一套设计变量；值与 index.vue .qa-shell 两块保持同步） ─── */
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
  --fill-2: rgba(30, 64, 175, 0.08);
  --card-bg: rgba(255, 255, 255, 0.62);
  --grad-brand: linear-gradient(110deg, #1e40af 0%, #2563eb 35%, #0ea5e9 70%, #0891b2 100%);
  --on-primary: #ffffff;
  --shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.04), 0 4px 16px -8px rgba(30, 64, 175, 0.09);
  --shadow-md: 0 1px 2px rgba(15, 23, 42, 0.05), 0 12px 32px -12px rgba(30, 64, 175, 0.13);
  --font-body: -apple-system, 'PingFang SC', 'HarmonyOS Sans SC', 'Hiragino Sans GB', 'Microsoft YaHei', system-ui, sans-serif;
  --font-mono: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace;

  position: fixed;
  top: 50%;
  left: 50%;
  z-index: 1501;
  transform: translate(-50%, -50%);
  width: min(540px, 92vw);
  display: flex;
  flex-direction: column;
  border-radius: 20px;
  overflow: hidden;
  background: linear-gradient(165deg, #ffffff 0%, var(--paper) 100%);
  border: 1px solid var(--border-strong);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.95),
    0 32px 80px -24px rgba(15, 23, 42, 0.4),
    0 8px 24px -8px rgba(30, 64, 175, 0.25);
  font-family: var(--font-body);
  color: var(--ink);
}

/* 过渡 */
.si-mask-enter-active,
.si-mask-leave-active {
  transition: opacity 0.28s ease;
}
.si-mask-enter-from,
.si-mask-leave-to {
  opacity: 0;
}
.si-card-enter-active {
  transition: opacity 0.32s ease, transform 0.4s cubic-bezier(0.22, 1, 0.36, 1);
}
.si-card-leave-active {
  transition: opacity 0.2s ease, transform 0.24s cubic-bezier(0.4, 0, 1, 1);
}
.si-card-enter-from {
  opacity: 0;
  transform: translate(-50%, -46%);
}
.si-card-leave-to {
  opacity: 0;
  transform: translate(-50%, -52%);
}

/* ── 头部 ─────────────────────────────────────────── */
.si-head {
  position: relative;
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 26px 26px 18px;
  overflow: hidden;
  background: var(--accent-soft);
  border-bottom: 1px solid var(--line-hair);
}

.si-head-main {
  position: relative;
  flex: 1;
  min-width: 0;
}

/* 小号中文眉题，对齐技能面板头部语言 */
.si-eyebrow {
  font-family: var(--font-body);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--ink-3);
}

.si-title {
  margin: 8px 0 0;
  font-family: var(--font-body);
  font-size: 24px;
  font-weight: 800;
  letter-spacing: -0.02em;
  line-height: 1.2;
  color: var(--ink);
}

.si-sub {
  margin: 8px 0 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--ink-3);
}

.si-close {
  position: relative;
  flex-shrink: 0;
  width: 30px;
  height: 30px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--ink-4);
  background: var(--surface-strong);
  border: 1px solid var(--border);
  border-radius: 9px;
  cursor: pointer;
  transition: all 0.15s;
}

.si-close:hover {
  background: var(--fill-hover);
  color: var(--ink);
  border-color: var(--border-strong);
}

/* ── 四步流程 ─────────────────────────────────────── */
.si-steps {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 20px 26px 4px;
}

.si-step {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  padding: 13px 15px;
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: 13px;
  transition: border-color 0.18s, box-shadow 0.18s;
}

.si-step:hover {
  border-color: var(--border-strong);
  box-shadow: var(--shadow-sm);
}

/* 步骤号：浅底深字，与来源徽章同格式（去渐变块） */
.si-step-num {
  flex-shrink: 0;
  width: 34px;
  height: 34px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 10px;
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.04em;
  color: var(--accent);
  background: var(--fill-2);
}

.si-step-body {
  flex: 1;
  min-width: 0;
}

.si-step-title {
  margin: 0;
  font-family: var(--font-body);
  font-size: 14px;
  font-weight: 700;
  color: var(--ink);
  letter-spacing: 0.01em;
}

.si-step-desc {
  margin: 4px 0 0;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--ink-3);
}

/* 行内 @指令 标记 */
.si-step-desc code {
  font-family: var(--font-mono);
  font-size: 11.5px;
  color: var(--accent);
  background: var(--accent-soft);
  padding: 1px 5px;
  border-radius: 4px;
}

/* 行内方式标签（方式一 / 方式二） */
.si-step-desc b {
  font-weight: 600;
  color: var(--ink-2);
}

/* ── 底部按钮 ─────────────────────────────────────── */
.si-foot {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 18px 26px 22px;
}

.si-btn {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 9px 20px;
  border-radius: 11px;
  font-family: var(--font-body);
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.005em;
  cursor: pointer;
  transition: all 0.18s ease;
}

.si-btn--ghost {
  background: var(--surface-strong);
  border: 1px solid var(--border);
  color: var(--ink-2);
}

.si-btn--ghost:hover {
  background: #fff;
  border-color: var(--border-strong);
  color: var(--ink);
}

/* 主按钮：与「新建对话 / 创建技能」同一品牌语言（ink 下自动塌成墨黑实底） */
.si-btn--primary {
  background: var(--grad-brand);
  border: none;
  color: var(--on-primary);
  box-shadow: var(--shadow-sm);
}

.si-btn--primary:hover {
  transform: translateY(-1px);
  filter: brightness(0.96);
  box-shadow: var(--shadow-md);
}

/* ── 响应式 ──────────────────────────────────────── */
@media (max-width: 600px) {
  .si-head {
    padding: 20px 18px 14px;
  }

  .si-title {
    font-size: 20px;
  }

  .si-steps {
    padding: 14px 18px 0;
    gap: 8px;
  }

  .si-foot {
    padding: 14px 18px 18px;
  }

  .si-btn {
    flex: 1;
    justify-content: center;
  }
}

/* ─── Kimi 主题：令牌塌成墨灰阶 + 实色白卡，去玻璃/极光 ─── */
:root[data-qa-theme='ink'] .si-card {
  --paper: #f1f1f3;
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
  --fill-2: #efeff1;
  --card-bg: #ffffff;
  --grad-brand: #131316;
  --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.04);
  --shadow-md: 0 1px 2px rgba(0, 0, 0, 0.03), 0 10px 30px -14px rgba(0, 0, 0, 0.1);

  background: #ffffff;
  box-shadow:
    0 2px 8px rgba(0, 0, 0, 0.05),
    0 32px 80px -24px rgba(0, 0, 0, 0.25);
}

:root[data-qa-theme='ink'] .si-head {
  background: #fafafa;
}
</style>
