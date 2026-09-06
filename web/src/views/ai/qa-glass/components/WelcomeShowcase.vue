<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import type { QuickAction, QuickActionExample } from '@/service/api';
import { getServiceBaseURL } from '@/utils/service';
import { customIconHtml } from './skill/skill-icon';

/** 旧案例体系入口开关：暂时隐藏（留给后续「探索」栏目），翻回 true 即恢复 */
const SHOW_CASES = false;

/**
 * 首屏下半（探索区）——独立组件。
 *
 * 两个互相独立的区块，上下叠放：
 *   ① 技能区（上）：pill 点分类展开该组技能卡（默认收起，再点收起）；
 *      点有案例的技能卡=技能卡区整体替换为该技能的案例卡（左上角返回按钮回技能列表），
 *      无案例的技能点击=直接用该技能开问
 *   ② 探索案例区（下）：铺系统里全部案例（分类 → 案例两级平铺，案例卡上带所属技能），
 *      随滚动带出；「探索案例」按钮=滚动锚点
 * pill 只驱动①，与②完全解耦。
 */
const props = defineProps<{
  /** generic 品牌显示知识库状态；standard 显示标准池范围 */
  isGeneric: boolean;
  /** 分类分组（pill 与技能区数据源），[{cat, icon?, actions}]；icon 来自 agent_skill_category.icon */
  groups: Array<{ cat: string; icon?: string; actions: QuickAction[] }>;
  /** 探索案例数据：分类 → 案例平铺（每条案例带所属技能，卡上回显） */
  caseGroups: Array<{ cat: string; icon?: string; cases: Array<{ action: QuickAction; example: QuickActionExample }> }>;
  /** 正在 fork 加载的案例 id（加载中描边、其余禁用） */
  loadingExampleId: number | null;
}>();

const emit = defineEmits<{
  /** 点无案例的技能卡 / 案例面板头部「试一试」：用这个技能开问（父级塞 @key 进输入框） */
  useSkill: [action: QuickAction];
  /** 点案例卡：fork 成真实会话 */
  loadExample: [example: QuickActionExample];
}>();

// ── ① 技能区：pill 开关 + 当前分类 ─────────────────────────
const activeCat = ref('');
const skillsOpen = ref(false);

// groups 到位后默认选中第一组（不展开，仅记录）
watch(
  () => props.groups,
  gs => {
    if (!activeCat.value && gs.length) activeCat.value = gs[0].cat;
  },
  { immediate: true }
);

const activeGroup = computed(
  () => props.groups.find(g => g.cat === activeCat.value) || props.groups[0] || null
);

/** 点未选中的分类=展开该组；再点同一分类=收起（切分类同时收起就地案例面板） */
function pickPill(cat: string) {
  expandedSkillId.value = null;
  if (skillsOpen.value && activeCat.value === cat) {
    skillsOpen.value = false;
  } else {
    activeCat.value = cat;
    skillsOpen.value = true;
  }
}

// 案例视图：点有案例的技能卡，技能卡区整体替换为该技能的案例卡（与下方探索案例区无关）
const expandedSkillId = ref<number | null>(null);
const expandedAction = computed(
  () => activeGroup.value?.actions.find(a => a.id === expandedSkillId.value && a.examples.length) ?? null
);

/** 点技能卡：直接用该技能开问 */
function toggleSkillCard(action: QuickAction) {
  // [案例弹层暂时下线，后面可能回滚] 原逻辑：有案例=就地展开/收起其案例面板；无案例=直接开问
  // if (!action.examples.length) {
  //   emit('useSkill', action);
  //   return;
  // }
  // expandedSkillId.value = expandedSkillId.value === action.id ? null : action.id;
  emit('useSkill', action);
}

/** pill 排横向滚动条是隐藏的，多数人不会「横向滚轮」——把常见的垂直滚轮转成横向滚动。
 *  到横向边界时放行事件，让页面继续上下滚（滚动链），不霸占滚轮。
 *  用 onMounted 手动挂非 passive 监听：preventDefault 在 passive 监听上会失效 */
const pillRowRef = ref<HTMLElement | null>(null);

function onPillWheel(e: WheelEvent) {
  const el = pillRowRef.value;
  if (!el) return;
  const maxLeft = el.scrollWidth - el.clientWidth;
  // 无横向溢出 / 用户本就在横向滚（触控板）→ 交给原生处理
  if (maxLeft <= 0 || Math.abs(e.deltaY) <= Math.abs(e.deltaX)) return;
  // 已到边界且还要继续往该方向 → 放行，让页面上下滚动
  if ((e.deltaY < 0 && el.scrollLeft <= 0) || (e.deltaY > 0 && el.scrollLeft >= maxLeft)) return;
  e.preventDefault();
  el.scrollLeft += e.deltaY;
}

onMounted(() => {
  pillRowRef.value?.addEventListener('wheel', onPillWheel, { passive: false });
});
onBeforeUnmount(() => {
  pillRowRef.value?.removeEventListener('wheel', onPillWheel);
});

// ── ② 探索案例区：滚动锚点 + 缩略图扫动 ─────────────────────
const exploreBodyRef = ref<HTMLElement | null>(null);

function scrollToExplore() {
  nextTick(() => exploreBodyRef.value?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
}

function exampleImages(ex: QuickActionExample): string[] {
  if (ex.previewImages?.length) return ex.previewImages;
  if (ex.previewImage) return [ex.previewImage];
  return [];
}

function imgUrl(path?: string): string {
  if (!path) return '';
  const isHttpProxy = import.meta.env.DEV && import.meta.env.VITE_HTTP_PROXY === 'Y';
  const { baseURL } = getServiceBaseURL(import.meta.env, isHttpProxy);
  const origin = /^https?:\/\/[^/]+/.exec(baseURL)?.[0] ?? '';
  return origin + path;
}

// 案例缩略图：悬停横向扫动切换预览帧
const scrubId = ref<number | null>(null);
const scrubIdx = ref(0);

function onThumbMove(e: MouseEvent, ex: QuickActionExample) {
  scrubId.value = ex.id;
  const images = exampleImages(ex);
  if (images.length <= 1) return;
  const el = e.currentTarget as HTMLElement;
  const rect = el.getBoundingClientRect();
  const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
  scrubIdx.value = Math.min(images.length - 1, Math.floor(ratio * images.length));
}

function onThumbLeave() {
  scrubId.value = null;
  scrubIdx.value = 0;
}
</script>

<template>
  <div class="kimi-foot">
    <!-- pill = 技能区开关：点分类展开该组技能卡，再点同一分类收起（默认收起）；
         垂直滚轮可直接横滚 pill 排（见 onPillWheel，隐藏滚动条的可达性补偿） -->
    <div ref="pillRowRef" class="kimi-pill-row">
      <button
        v-for="group in groups"
        :key="group.cat"
        type="button"
        class="kimi-pill"
        :class="{ on: skillsOpen && activeCat === group.cat }"
        @click="pickPill(group.cat)"
      >
        <!-- 分类图标：DB（agent_skill_category.icon）下发的 svg / data URI；无图标则不占位 -->
        <span v-if="customIconHtml(group.icon)" class="kimi-pill-ico" aria-hidden="true" v-html="customIconHtml(group.icon)" />
        {{ group.cat }}
      </button>
    </div>

    <!-- ① 技能区（在探索案例上面，默认隐藏）：默认展示当前分类的技能卡；
         点有案例的技能卡=整体替换为该技能的案例视图（左上角返回）；无案例的技能=直接用该技能开问 -->
    <template v-if="skillsOpen && activeGroup">
      <!-- 暂时下线案例弹层（点技能卡展开案例视图），后面可能回滚：
           恢复时把 SHOW_CASES 翻回 true，并还原 toggleSkillCard 与技能卡注释处 -->
      <template v-if="SHOW_CASES">
      <!-- 案例视图：案例卡直接替换技能卡，左上角返回按钮回技能列表。案例卡点击 = fork 成真实会话 -->
      <div v-if="expandedAction" class="kimi-skill-cases">
        <header class="kimi-skill-cases-head">
          <button type="button" class="kimi-back" title="返回技能列表" @click="expandedSkillId = null">
            <svg
              viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
              width="14" height="14" aria-hidden="true"
            ><path d="m15 6-6 6 6 6"/></svg>
            返回
          </button>
          <span class="kimi-skill-cases-name">{{ expandedAction.name }} · 案例</span>
          <span v-if="expandedAction.skillKey" class="kimi-skill-cases-at">@{{ expandedAction.skillKey }}</span>
          <span class="kimi-spacer" />
          <button type="button" class="kimi-try" title="用这个技能开新问" @click="emit('useSkill', expandedAction)">试一试 ↗</button>
        </header>
        <div class="kimi-example-grid">
          <button
            v-for="ex in expandedAction.examples"
            :key="ex.id"
            type="button"
            class="kimi-example-card"
            :class="{
              'is-loading': loadingExampleId === ex.id,
              'is-dim': loadingExampleId !== null && loadingExampleId !== ex.id,
              'kimi-example-card--text': !exampleImages(ex).length
            }"
            :title="`加载案例：${ex.title}`"
            :disabled="loadingExampleId !== null"
            @click="emit('loadExample', ex)"
            @mouseenter="scrubId = ex.id"
            @mousemove="onThumbMove($event, ex)"
            @mouseleave="onThumbLeave()"
          >
            <span v-if="exampleImages(ex).length" class="kimi-example-media">
              <img
                :src="imgUrl(exampleImages(ex)[scrubId === ex.id ? scrubIdx : 0])"
                :alt="ex.title"
                loading="lazy"
              />
              <span v-if="exampleImages(ex).length > 1" class="kimi-example-frame">
                {{ (scrubId === ex.id ? scrubIdx : 0) + 1 }}/{{ exampleImages(ex).length }}
              </span>
            </span>
            <span class="kimi-example-body">
              <!-- 无底部入口行，加载状态借眉标位展示 -->
              <span class="kimi-example-kicker" :class="{ loading: loadingExampleId === ex.id }">
                {{ loadingExampleId === ex.id ? '加载中…' : 'CASE 案例' }}
              </span>
              <span class="kimi-example-title">{{ ex.title }}</span>
              <span v-if="ex.description" class="kimi-example-sub">{{ ex.description }}</span>
            </span>
          </button>
        </div>
      </div>
      </template>

      <!-- 技能卡视图：点击=直接用该技能开问（案例弹层下线后不再区分有无案例） -->
      <div class="kimi-case-grid">
        <article
          v-for="action in activeGroup.actions"
          :key="action.id"
          class="kimi-case-card"
          :title="`试用「${action.name}」`"
          @click="toggleSkillCard(action)"
        >
          <div class="kimi-case-head">
            <span class="kimi-case-name">{{ action.name }}</span>
            <!-- 暂时隐藏技能 key（@xxx）展示，恢复时把 v-if="false" 改回 v-if="action.skillKey" -->
            <span v-if="false" class="kimi-case-skill">@{{ action.skillKey }}</span>
          </div>
          <p v-if="action.description" class="kimi-case-desc">{{ action.description }}</p>
          <!-- 「N 个案例」入口随案例弹层一起下线，恢复时把 SHOW_CASES 翻回 true（原条件 action.examples.length 一并保留） -->
          <div v-if="SHOW_CASES && action.examples.length" class="kimi-case-foot">
            <span class="kimi-case-open">
              <span class="kimi-case-count"><b>{{ action.examples.length }}</b> 个案例</span>
              <svg
                viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
                width="12" height="12" aria-hidden="true"
              ><path d="m9 6 6 6-6 6"/></svg>
            </span>
          </div>
        </article>
      </div>
    </template>

    <!-- ② 探索案例（独立区块）：按钮=滚动锚点，往下滑也能自然带出 -->
    <!-- 暂时隐藏「探索案例」整块（入口按钮 + 案例内容），恢复时把 SHOW_CASES 翻回 true -->
    <template v-if="SHOW_CASES">
    <button class="kimi-explore" type="button" @click="scrollToExplore()">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" width="16" height="16">
        <path d="M12 3a6 6 0 0 1 3.6 10.8c-.7.6-1.1 1.3-1.3 2.2h-4.6c-.2-.9-.6-1.6-1.3-2.2A6 6 0 0 1 12 3Z"/>
        <path d="M10 19h4m-3 3h2"/>
      </svg>
      <span>探索案例</span>
      <span class="kimi-explore-right">
        点击或下滑探索
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="14" height="14" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>
      </span>
    </button>

    <!-- 探索案例内容：分类 → 案例两级平铺（案例卡眉标带所属技能）；案例卡点击 fork 成真实会话 -->
    <div ref="exploreBodyRef" class="kimi-explore-body">
      <template v-if="caseGroups.length">
        <section v-for="g in caseGroups" :key="g.cat" class="kimi-explore-cat">
          <header class="kimi-explore-cat-head">
            <span v-if="customIconHtml(g.icon)" class="kimi-explore-cat-ico" aria-hidden="true" v-html="customIconHtml(g.icon)" />
            <span class="kimi-explore-cat-name">{{ g.cat }}</span>
            <span class="kimi-explore-cat-count">{{ g.cases.length }} 个案例</span>
          </header>
          <div class="kimi-example-grid">
            <button
              v-for="item in g.cases"
              :key="item.example.id"
              type="button"
              class="kimi-example-card"
              :class="{
                'is-loading': loadingExampleId === item.example.id,
                'is-dim': loadingExampleId !== null && loadingExampleId !== item.example.id,
                'kimi-example-card--text': !exampleImages(item.example).length
              }"
              :title="`加载案例：${item.example.title}`"
              :disabled="loadingExampleId !== null"
              @click="emit('loadExample', item.example)"
              @mouseenter="scrubId = item.example.id"
              @mousemove="onThumbMove($event, item.example)"
              @mouseleave="onThumbLeave()"
            >
              <!-- 案例预览图：悬停扫动切帧；无图则不留媒体区，直接紧凑文字卡 -->
              <span v-if="exampleImages(item.example).length" class="kimi-example-media">
                <img
                  :src="imgUrl(exampleImages(item.example)[scrubId === item.example.id ? scrubIdx : 0])"
                  :alt="item.example.title"
                  loading="lazy"
                />
                <span v-if="exampleImages(item.example).length > 1" class="kimi-example-frame">
                  {{ (scrubId === item.example.id ? scrubIdx : 0) + 1 }}/{{ exampleImages(item.example).length }}
                </span>
              </span>
              <span class="kimi-example-body">
                <!-- 眉标 = 所属技能（分类 → 案例两级，技能是附带信息）：只显示 @key，无 key 才回落名称；
                     无底部入口行，fork 加载状态借眉标位展示 -->
                <span class="kimi-example-kicker" :class="{ loading: loadingExampleId === item.example.id }">
                  <template v-if="loadingExampleId === item.example.id">加载中…</template>
                  <template v-else-if="item.action.skillKey">@{{ item.action.skillKey }}</template>
                  <template v-else>{{ item.action.name }}</template>
                </span>
                <span class="kimi-example-title">{{ item.example.title }}</span>
                <span v-if="item.example.description" class="kimi-example-sub">{{ item.example.description }}</span>
              </span>
            </button>
          </div>
        </section>
      </template>
      <div v-else class="kimi-example-empty">案例征集中，敬请期待</div>
    </div>
    </template>
  </div>
</template>

<style scoped>
/* ── 新首屏下半：标准池灰带 + pill 排 + 技能区 + 探索案例 ──
   主题令牌（--ink/--border/--card-bg/…）由 .qa-shell 下发，scoped 仅隔离选择器不隔离变量 */
.kimi-foot {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  padding: 26px 32px 18px; /* 顶部留白 = 原灰带撑出的间距，去掉灰带后保持输入框与 pill 排的自然距离 */
}

/* pill 排：单行展示，比输入框略窄；超出宽度隐藏滚动条、左右滑动（首尾 auto 外边距：不满一行时居中） */
.kimi-pill-row {
  display: flex;
  flex-wrap: nowrap;
  padding: 2px 0;
  gap: 10px;
  width: 100%;
  max-width: 720px;
  overflow-x: auto;
  scrollbar-width: none;
  -webkit-overflow-scrolling: touch;
}

.kimi-pill-row::-webkit-scrollbar {
  display: none;
}

.kimi-pill-row .kimi-pill:first-child {
  margin-left: auto;
}

.kimi-pill-row .kimi-pill:last-child {
  margin-right: auto;
}

.kimi-pill {
  flex: none;
  display: flex;
  align-items: center;
  gap: 6px;
  height: 34px;
  white-space: nowrap;
  padding: 0 12px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--card-bg);
  box-shadow: var(--card-shadow);
  font-family: var(--font-body);
  font-size: 13px;
  color: var(--ink);
  cursor: pointer;
  transition: box-shadow 0.15s ease, transform 0.15s ease, background 0.15s ease, color 0.15s ease;
}

.kimi-pill:hover {
  box-shadow: var(--shadow-md);
  transform: translateY(-1px);
}

.kimi-pill.on {
  background: var(--grad-brand);
  border-color: transparent;
  color: var(--on-primary);
}

/* 分类图标：线条 svg 随 pill 主题色（选中态自动变 on-primary） */
.kimi-pill-ico {
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.kimi-pill-ico :deep(svg) {
  width: 15px;
  height: 15px;
  display: block;
}

/* 技能区网格：当前分类的技能卡（点有案例的卡=整体切到案例视图） */
.kimi-case-grid {
  width: 100%;
  max-width: 880px;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
  gap: 12px;
}

.kimi-case-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 16px;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: var(--card-bg);
  cursor: pointer;
  transition: box-shadow 0.16s ease, transform 0.16s ease, border-color 0.16s ease;
}

.kimi-case-card:hover {
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.07);
  transform: translateY(-1px);
  border-color: var(--border-strong);
}

.kimi-case-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.kimi-case-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
}

.kimi-case-skill {
  margin-left: auto;
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--ink-4);
}

.kimi-case-desc {
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--ink-3);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.kimi-case-foot {
  display: flex;
  justify-content: space-between;
  font-size: 11.5px;
  color: var(--ink-4);
}

.kimi-case-foot b {
  color: var(--ink-3);
  font-weight: 600;
}

.kimi-case-open {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--ink-3);
  transition: color 0.15s ease;
}

.kimi-case-card:hover .kimi-case-open {
  color: var(--accent);
}

/* 案例视图：整体替换技能卡区（头部=返回按钮 + 技能名，正文=案例网格） */
.kimi-skill-cases {
  width: 100%;
  max-width: 880px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 16px 16px;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: var(--fill-2);
}

.kimi-skill-cases-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.kimi-skill-cases-name {
  font-size: 13.5px;
  font-weight: 700;
  color: var(--ink);
}

.kimi-skill-cases-at {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--ink-4);
}

.kimi-spacer {
  flex: 1;
}

/* 案例视图左上角返回按钮：回到技能卡列表 */
.kimi-back {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--card-bg);
  font-family: inherit;
  font-size: 12.5px;
  color: var(--ink-3);
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
}

.kimi-back:hover {
  background: var(--fill-hover);
  border-color: var(--border-strong);
  color: var(--accent);
}

/* ── 探索案例内容容器：常驻滚动流，scroll-margin 给滚动定位留位 ── */
.kimi-explore-body {
  width: 100%;
  max-width: 880px;
  display: flex;
  flex-direction: column;
  gap: 20px;
  scroll-margin-top: 24px;
}

/* 探索案例分类区块：分类 → 案例两级平铺，案例卡眉标带所属技能 */
.kimi-explore-cat {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.kimi-explore-cat-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.kimi-explore-cat-ico {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--ink-3);
}

.kimi-explore-cat-ico :deep(svg) {
  width: 16px;
  height: 16px;
  display: block;
}

.kimi-explore-cat-name {
  font-size: 14px;
  font-weight: 700;
  color: var(--ink);
}

.kimi-explore-cat-count {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--ink-4);
}

.kimi-try {
  padding: 4px 10px;
  border: none;
  border-radius: 8px;
  background: none;
  font-family: inherit;
  font-size: 12.5px;
  color: var(--ink-3);
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}

.kimi-try:hover {
  background: var(--fill-hover);
  color: var(--accent);
}

/* 案例网格：案例卡 = 大图 + 标题/描述（无底部入口行，整卡可点）。
   fork 可能耗时数秒——加载中的卡描边强调、眉标显示「加载中…」，其余卡降透明度并禁用（防并发 fork） */
.kimi-example-grid {
  width: 100%;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
  gap: 12px;
}

.kimi-example-card {
  display: flex;
  flex-direction: column;
  padding: 0;
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: var(--card-bg);
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  transition: box-shadow 0.16s ease, transform 0.16s ease, border-color 0.16s ease, opacity 0.15s ease;
}

.kimi-example-card:hover:not(:disabled) {
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.07);
  transform: translateY(-1px);
  border-color: var(--border-strong);
}

.kimi-example-card.is-loading {
  border-color: var(--accent);
}

.kimi-example-card.is-dim {
  opacity: 0.55;
}

.kimi-example-media {
  position: relative;
  display: block;
  aspect-ratio: 16 / 9;
  background: var(--fill-2);
}

.kimi-example-media img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.kimi-example-frame {
  position: absolute;
  right: 6px;
  bottom: 6px;
  padding: 0 5px;
  border-radius: 6px;
  background: rgba(15, 23, 42, 0.55);
  color: #fff;
  font-family: var(--font-mono);
  font-size: 9.5px;
  line-height: 16px;
}

/* 正文即卡片底部（无底部入口行），底部留白直接由正文 padding 承担 */
.kimi-example-body {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 10px 12px 12px;
}

/* 无图案例卡：去掉媒体区，正文顶部加 CASE 眉标补足层级，整体改紧凑文字排版 */
.kimi-example-card--text .kimi-example-body {
  padding: 13px 14px 13px;
}

.kimi-example-kicker {
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.1em;
  font-weight: 700;
  text-transform: uppercase;
  color: var(--ink-4);
  max-width: 100%;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.kimi-example-card--text .kimi-example-kicker {
  color: var(--accent);
}

/* fork 加载中：眉标位借显主题色提示 */
.kimi-example-kicker.loading {
  color: var(--accent);
}

.kimi-example-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.kimi-example-sub {
  font-size: 11.5px;
  line-height: 1.5;
  color: var(--ink-4);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.kimi-example-empty {
  width: 100%;
  padding: 28px 0;
  text-align: center;
  font-size: 13px;
  color: var(--ink-4);
}

/* 探索案例按钮：滚动锚点，点击把案例区推入视野 */
.kimi-explore {
  width: 100%;
  max-width: 880px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 17px 20px;
  border: none;
  border-radius: 16px;
  background: var(--fill-2);
  font-family: var(--font-body);
  font-size: 14px;
  color: var(--ink-2);
  cursor: pointer;
  transition: background 0.15s ease, filter 0.15s ease;
}

.kimi-explore:hover {
  filter: brightness(0.98);
}

.kimi-explore-right {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--ink-3);
  font-size: 13px;
}

.kimi-explore-right svg {
  flex-shrink: 0;
  color: var(--ink-4);
}

/* ── 手机端：卡片区收窄、网格塌单列 ── */
@media (max-width: 640px) {
  .kimi-foot {
    padding: 18px 16px 14px;
    gap: 12px;
  }

  .kimi-pill-row {
    gap: 8px;
  }

  .kimi-pill {
    height: 32px;
    padding: 0 11px;
    font-size: 12.5px;
  }

  .kimi-pill-ico :deep(svg) {
    width: 14px;
    height: 14px;
  }

  .kimi-example-grid {
    grid-template-columns: 1fr;
    gap: 10px;
  }

  .kimi-explore {
    padding: 13px 16px;
    font-size: 13px;
    border-radius: 13px;
  }

  .kimi-explore-right {
    font-size: 12px;
  }
}
</style>
