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
        <!-- 头部：点阵纹理 + 幽灵水印，与技能面板同一视觉语言 -->
        <header class="si-head">
          <span class="si-head-dots" />
          <span class="si-head-ghost">✦</span>
          <div class="si-head-main">
            <span class="si-eyebrow">NEW · CREATE SKILL</span>
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
              <p class="si-step-title">说 · 像聊天一样讲出你的经验</p>
              <p class="si-step-desc">技能是你反复用到的做事方法。把积累的经验、要求讲给 AI，一遍讲不完可以多聊几轮——比如「以后会议记录发你，按『决议、待办、风险』三段总结，待办标责任人和截止时间，没写进纪要的口头约定单独列在最后」</p>
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
              <p class="si-step-desc">在任意对话输入 @技能名，AI 就按这套方法办事；「技能管理」里可随时启停</p>
            </div>
          </div>
          <div class="si-step">
            <span class="si-step-num">04</span>
            <div class="si-step-body">
              <p class="si-step-title">改 · @编辑 随时调教</p>
              <p class="si-step-desc">想调整已有技能？对话里说 <code>@编辑 @技能名</code> 加上你想改的点，AI 帮你改好</p>
            </div>
          </div>
        </div>

        <!-- 底部按钮：仅这两个按钮算「知晓」；×/遮罩关闭下次仍会展示 -->
        <footer class="si-foot">
          <button class="si-btn si-btn--ghost" @click="emit('ack')">知道了</button>
          <button class="si-btn si-btn--primary" @click="emit('start')">
            <span class="si-btn-spark">✦</span>
            现在就创建
          </button>
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
  background: linear-gradient(165deg, #ffffff 0%, #f2f6fd 100%);
  border: 1px solid rgba(30, 64, 175, 0.14);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.95),
    0 32px 80px -24px rgba(15, 23, 42, 0.4),
    0 8px 24px -8px rgba(30, 64, 175, 0.25);
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
  background: linear-gradient(160deg, rgba(37, 99, 235, 0.07), rgba(8, 145, 178, 0.04));
  border-bottom: 1px solid rgba(30, 64, 175, 0.08);
}

/* 点阵纹理（与技能面板同款） */
.si-head-dots {
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  width: 220px;
  background-image: radial-gradient(circle, rgba(30, 64, 175, 0.13) 1px, transparent 1px);
  background-size: 14px 14px;
  -webkit-mask-image: linear-gradient(to left, rgba(0, 0, 0, 0.7), transparent);
  mask-image: linear-gradient(to left, rgba(0, 0, 0, 0.7), transparent);
  pointer-events: none;
}

/* 幽灵水印大字 */
.si-head-ghost {
  position: absolute;
  top: -26px;
  right: 14px;
  font-family: var(--font-display, 'Plus Jakarta Sans', sans-serif);
  font-size: 110px;
  line-height: 1;
  color: rgba(30, 64, 175, 0.07);
  pointer-events: none;
  user-select: none;
}

.si-head-main {
  position: relative;
  flex: 1;
  min-width: 0;
}

.si-eyebrow {
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.26em;
  color: #0891b2;
}

.si-title {
  margin: 8px 0 0;
  font-family: var(--font-display, 'Plus Jakarta Sans', sans-serif);
  font-size: 24px;
  font-weight: 800;
  letter-spacing: -0.02em;
  line-height: 1.2;
  background: linear-gradient(110deg, #1e40af 0%, #0891b2 100%);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  color: transparent;
}

.si-sub {
  margin: 8px 0 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--ink-3, #64748b);
}

.si-close {
  position: relative;
  flex-shrink: 0;
  width: 30px;
  height: 30px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--ink-4, #94a3b8);
  background: rgba(255, 255, 255, 0.6);
  border: 1px solid rgba(30, 64, 175, 0.12);
  border-radius: 9px;
  cursor: pointer;
  transition: all 0.15s;
}

.si-close:hover {
  background: #fff;
  color: var(--ink, #0f172a);
  border-color: rgba(30, 64, 175, 0.24);
  transform: rotate(90deg);
}

/* ── 三步流程 ─────────────────────────────────────── */
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
  background: rgba(255, 255, 255, 0.72);
  border: 1px solid rgba(30, 64, 175, 0.09);
  border-radius: 13px;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.9);
  transition: border-color 0.18s, box-shadow 0.18s, transform 0.18s;
}

.si-step:hover {
  border-color: rgba(30, 64, 175, 0.22);
  transform: translateY(-1px);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.9),
    0 6px 18px -8px rgba(30, 64, 175, 0.25);
}

.si-step-num {
  flex-shrink: 0;
  width: 34px;
  height: 34px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 10px;
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.04em;
  color: #fff;
  background: linear-gradient(135deg, #2563eb, #0891b2);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.4),
    0 3px 10px -3px rgba(30, 64, 175, 0.5);
}

.si-step-body {
  flex: 1;
  min-width: 0;
}

.si-step-title {
  margin: 0;
  font-family: var(--font-display, 'Plus Jakarta Sans', sans-serif);
  font-size: 14px;
  font-weight: 700;
  color: var(--ink, #0f172a);
  letter-spacing: 0.01em;
}

.si-step-desc {
  margin: 4px 0 0;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--ink-3, #64748b);
}

/* 行内 @指令 标记 */
.si-step-desc code {
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: 11.5px;
  color: var(--accent, #1e40af);
  background: rgba(30, 64, 175, 0.07);
  padding: 1px 5px;
  border-radius: 4px;
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
  font-family: var(--font-display, 'Plus Jakarta Sans', sans-serif);
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.005em;
  cursor: pointer;
  transition: all 0.18s ease;
}

.si-btn--ghost {
  background: rgba(255, 255, 255, 0.6);
  border: 1px solid rgba(30, 64, 175, 0.15);
  color: var(--ink-2, #334155);
}

.si-btn--ghost:hover {
  background: #fff;
  border-color: rgba(30, 64, 175, 0.28);
  color: var(--ink, #0f172a);
}

/* 主按钮：与「新建对话 / 创建技能」同一渐变语言 */
.si-btn--primary {
  background: linear-gradient(110deg, #1e40af 0%, #2563eb 35%, #0ea5e9 70%, #0891b2 100%);
  border: none;
  color: #fff;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.4),
    0 4px 14px -2px rgba(30, 64, 175, 0.45);
}

.si-btn--primary:hover {
  transform: translateY(-1px);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.4),
    0 8px 24px -4px rgba(30, 64, 175, 0.55);
}

.si-btn-spark {
  font-size: 13px;
  line-height: 1;
}

/* ── 响应式 ──────────────────────────────────────── */
@media (max-width: 600px) {
  .si-head {
    padding: 20px 18px 14px;
  }

  .si-title {
    font-size: 20px;
  }

  .si-head-ghost {
    font-size: 84px;
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
</style>
