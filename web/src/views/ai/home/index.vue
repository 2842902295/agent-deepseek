<script setup lang="ts">
import { useRouter } from 'vue-router';

const router = useRouter();

const pillars = [
  {
    num: '01',
    tag: '首页',
    route: 'ai_workbench',
    title: '自己提炼技能',
    desc: '将重复流程封装为可复用的技能包，任何人都能定义和调用，知识不再停留在个人头脑里。',
    cta: '进入首页',
    svg: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>`
  },
  {
    num: '02',
    tag: '知识库收集',
    route: 'ai_nian',
    title: '自动提炼灵感',
    desc: '随手记录，AI 自动归类打标关联知识。不用整理，积累自然发生，随时可检索调用。',
    cta: '开始记录',
    svg: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>`
  },
  {
    num: '03',
    tag: '智能问答',
    route: 'ai_qa-glass',
    title: '问答即执行',
    desc: '一句话触发去重、比对、评估等完整分析流程，结果直接结构化输出，不只是对话。',
    cta: '开始提问',
    svg: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>`
  }
];

const extras = [
  { name: '标准去重', desc: '自动识别重复条目，清洗冗余数据' },
  { name: '全文相似度', desc: '跨文档语义比对，定位冲突段落' },
  { name: 'AI 比对', desc: '结构化呈现差异：新增、删除、修改' },
  { name: '指标提取', desc: '抽取技术指标，输出可入库清单' },
  { name: '标准评估', desc: '完整性、规范性、逻辑性综合评分' },
  { name: '对象关系', desc: '推断标准对象层级，构建知识图谱' }
];
</script>

<template>
  <div class="page">
    <!-- Nav -->
    <nav class="topnav">
      <span class="topnav-logo">同道 AI 助理</span>
      <button class="topnav-btn" @click="router.push({ name: 'ai_qa-glass' })">开始工作</button>
    </nav>

    <!-- Hero -->
    <section class="hero">
      <div class="hero-pill">
        <span class="pill-dot" />
        标准化工作的新方式
      </div>
      <h1 class="hero-title">
        和 AI 一起，<em>把工作做轻</em>
      </h1>
      <p class="hero-sub">
        自定义技能包，自动提炼知识，一句话触发完整分析流程， 处理和生成任意格式文档——不只是问答工具。
      </p>
      <div class="hero-btns">
        <button class="btn-primary" @click="router.push({ name: 'ai_qa-glass' })">
          开始工作 →
        </button>
        <button class="btn-ghost" @click="router.push({ name: 'ai_nian' })">
          进入灵感库
        </button>
      </div>
    </section>

    <!-- 三大核心 -->
    <section class="pillars">
      <div
        v-for="p in pillars"
        :key="p.route"
        class="pillar"
      >
        <div class="pillar-top">
          <span class="pillar-num">{{ p.num }}</span>
          <span class="pillar-tag">{{ p.tag }}</span>
        </div>
        <div class="pillar-icon" v-html="p.svg" />
        <h2 class="pillar-title">{{ p.title }}</h2>
        <p class="pillar-desc">{{ p.desc }}</p>
        <button class="pillar-cta" @click="router.push({ name: p.route })">
          {{ p.cta }}
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" class="cta-arrow">
            <path d="M3 8h10M9 4l4 4-4 4"/>
          </svg>
        </button>
      </div>
    </section>

    <!-- 更多能力 -->
    <section class="extras">
      <div class="extras-label">更多内置能力</div>
      <div class="extras-grid">
        <div v-for="e in extras" :key="e.name" class="extra">
          <div class="extra-name">{{ e.name }}</div>
          <div class="extra-desc">{{ e.desc }}</div>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.page {
  background: #fff;
  color: #0f172a;
  font-family: 'PingFang SC', 'Noto Sans SC', system-ui, -apple-system, sans-serif;
  -webkit-font-smoothing: antialiased;
  min-height: 100%;
}

/* ── Nav ── */
.topnav {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 40px;
  height: 56px;
  border-bottom: 1px solid #f1f5f9;
  background: rgba(255,255,255,.92);
  backdrop-filter: blur(12px);
  position: sticky;
  top: 0;
  z-index: 10;
}

.topnav-logo {
  font-size: 15px;
  font-weight: 700;
  letter-spacing: -.02em;
  color: #0f172a;
}

.topnav-btn {
  background: #0f172a;
  color: #fff;
  border: none;
  border-radius: 8px;
  padding: 7px 16px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  font-family: inherit;
  transition: background .15s;
}

.topnav-btn:hover {
  background: #1e293b;
}

/* ── Hero ── */
.hero {
  min-height: 56vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 80px 24px 72px;
  background: radial-gradient(ellipse 80% 50% at 50% -10%, rgba(99,102,241,.07) 0%, transparent 70%);
}

.hero-pill {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 4px 14px;
  border-radius: 99px;
  border: 1px solid #e2e8f0;
  font-size: 12px;
  font-weight: 500;
  color: #64748b;
  margin-bottom: 28px;
}

.pill-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #6366f1;
  flex-shrink: 0;
}

.hero-title {
  font-size: clamp(38px, 6vw, 68px);
  font-weight: 800;
  letter-spacing: -.04em;
  line-height: 1.1;
  color: #0f172a;
  margin: 0 0 20px;
  max-width: 700px;
}

.hero-title em {
  font-style: normal;
  background: linear-gradient(120deg, #6366f1, #8b5cf6);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.hero-sub {
  font-size: 17px;
  line-height: 1.72;
  color: #64748b;
  max-width: 460px;
  margin: 0 0 36px;
}

.hero-btns {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  justify-content: center;
}

.btn-primary {
  background: #0f172a;
  color: #fff;
  border: none;
  border-radius: 10px;
  padding: 13px 28px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  font-family: inherit;
  transition: transform .15s, box-shadow .15s;
}

.btn-primary:hover {
  transform: translateY(-1px);
  box-shadow: 0 8px 24px rgba(15,23,42,.2);
}

.btn-ghost {
  background: transparent;
  color: #0f172a;
  border: 1.5px solid #e2e8f0;
  border-radius: 10px;
  padding: 12px 24px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  font-family: inherit;
  transition: border-color .15s, background .15s;
}

.btn-ghost:hover {
  border-color: #cbd5e1;
  background: #f8fafc;
}

/* ── 三大核心 ── */
.pillars {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 24px;
  max-width: 960px;
  margin: 0 auto;
  padding: 0 24px 80px;
}

.pillar {
  padding: 32px 24px;
  text-align: center;
}

.pillar-top {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  margin-bottom: 20px;
}

.pillar-num {
  font-size: 11px;
  font-weight: 800;
  letter-spacing: .1em;
  color: #cbd5e1;
}

.pillar-tag {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: .05em;
  text-transform: uppercase;
  color: #94a3b8;
}

.pillar-icon {
  width: 48px;
  height: 48px;
  border-radius: 14px;
  background: linear-gradient(135deg, #ede9fe, #ddd6fe);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #6366f1;
  margin: 0 auto 18px;
}

.pillar-icon :deep(svg) {
  width: 20px;
  height: 20px;
}

.pillar-title {
  font-size: 18px;
  font-weight: 800;
  letter-spacing: -.025em;
  color: #0f172a;
  margin-bottom: 10px;
}

.pillar-desc {
  font-size: 13.5px;
  color: #64748b;
  line-height: 1.68;
  margin-bottom: 24px;
}

.pillar-cta {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 13px;
  font-weight: 700;
  color: #0f172a;
  background: none;
  border: none;
  padding: 0;
  cursor: pointer;
  font-family: inherit;
  transition: gap .15s;
}

.pillar-cta:hover {
  gap: 9px;
}

.cta-arrow {
  width: 14px;
  height: 14px;
  transition: transform .15s;
}

.pillar-cta:hover .cta-arrow {
  transform: translateX(2px);
}

/* ── 更多能力 ── */
.extras {
  background: #f8fafc;
  border-top: 1px solid #f1f5f9;
  padding: 56px 24px 72px;
}

.extras-label {
  text-align: center;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: .1em;
  text-transform: uppercase;
  color: #cbd5e1;
  margin-bottom: 24px;
}

.extras-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  border: 1px solid #e8e8e6;
  border-radius: 12px;
  overflow: hidden;
  background: #e8e8e6;
  gap: 1px;
  max-width: 800px;
  margin: 0 auto;
}

.extra {
  background: #fff;
  padding: 18px 20px;
  transition: background .15s;
}

.extra:hover {
  background: #fafafa;
}

.extra-name {
  font-size: 13.5px;
  font-weight: 600;
  color: #334155;
  margin-bottom: 4px;
}

.extra-desc {
  font-size: 12px;
  color: #94a3b8;
  line-height: 1.5;
}

/* ── 响应式 ── */
@media (max-width: 768px) {
  .pillars       { grid-template-columns: 1fr; }
  .extras-grid   { grid-template-columns: repeat(2, 1fr); }
}

@media (max-width: 440px) {
  .extras-grid   { grid-template-columns: 1fr; }
  .hero-btns     { flex-direction: column; align-items: stretch; }
}
</style>
