<script lang="tsx" setup>
import {onMounted, onUnmounted, ref, shallowRef, useTemplateRef} from 'vue';
import {Graph} from '@antv/g6';
import {fetchObjRelGraph} from '@/service/api/ai';

defineOptions({name: 'ObjRel'});

const containerRef = useTemplateRef('containerRef');
const graphRef = shallowRef<Graph | null>(null);

const subjObj = ref('便携式电子产品');
const depth = ref(2);
const relType = ref('');
const confidence = ref('');
const loading = ref(false);

const REL_TYPE_OPTIONS = ['包含', '属于', '细分', '归属', '配套'];
const CONFIDENCE_OPTIONS = [
  {label: 'High', value: 'high'},
  {label: 'Medium', value: 'medium'},
  {label: 'Low', value: 'low'},
];

function toggleRelType(t: string) {
  relType.value = relType.value === t ? '' : t;
  loadGraph();
}

function toggleConfidence(c: string) {
  confidence.value = confidence.value === c ? '' : c;
  loadGraph();
}

const REL_COLORS: Record<string, string> = {
  包含: '#f59e0b',
  属于: '#ef4444',
  细分: '#22c55e',
  归属: '#06b6d4',
  配套: '#a855f7',
};

const REL_LABELS: Record<string, string> = {
  包含: '整体 → 部件',
  属于: '部件 → 整体',
  细分: '上位 → 下位',
  归属: '下位 → 上位',
  配套: '协同使用',
};

function edgeColor(d: any): string {
  const base = REL_COLORS[d.data?.label as string] ?? '#94a3b8';
  // low 置信度：颜色灰化，混入 50% 灰
  if (d.data?.confidence === 'low') {
    return base + '80'; // 50% 透明，配合 opacity 进一步弱化
  }
  return base;
}

function edgeDash(d: any): number[] {
  const c = d.data?.confidence ?? 'high';
  if (c === 'medium') return [8, 4];   // 长虚线，节奏感强
  if (c === 'low') return [2, 6];      // 短点线，稀疏
  return [];
}

function edgeLineWidth(d: any): number {
  const c = d.data?.confidence ?? 'high';
  if (c === 'high') return 2.5;
  if (c === 'medium') return 1.8;
  return 1;
}

function edgeOpacity(d: any): number {
  const c = d.data?.confidence ?? 'high';
  if (c === 'high') return 0.9;
  if (c === 'medium') return 0.7;
  return 0.4;
}

async function loadGraph() {
  if (!subjObj.value.trim()) return;
  loading.value = true;
  try {
    const res = await fetchObjRelGraph({
      subj_obj: subjObj.value,
      depth: depth.value,
      rel_type: relType.value || undefined,
      confidence: confidence.value || undefined,
    });

    const data = res.data;
    if (!data) return;
    const {nodes, edges} = data;

    const graphData = {
      nodes: nodes.map((n: Api.AI.ObjRelNode) => ({
        id: n.id,
        data: {label: n.label, isRoot: n.isRoot},
      })),
      edges: edges.map((e: Api.AI.ObjRelEdge) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        data: {label: e.label, confidence: e.confidence},
      })),
    };

    if (graphRef.value) {
      graphRef.value.setData(graphData);
      await graphRef.value.render();
      await graphRef.value.fitView();
      return;
    }

    graphRef.value = new Graph({
      container: containerRef.value!,
      autoFit: 'view',
      animation: false,
      data: graphData,
      behaviors: ['drag-canvas', 'zoom-canvas', 'drag-element'],
      node: {
        style: {
          labelText: (d: any) => d.data?.label ?? d.id,
          labelBackground: true,
          labelBackgroundFill: (d: any) => (d.data?.isRoot ? '#dbeafe' : '#f8fafc'),
          labelBackgroundOpacity: 0.95,
          labelBackgroundRadius: 6,
          labelFontSize: (d: any) => (d.data?.isRoot ? 13 : 12),
          labelFontWeight: (d: any) => (d.data?.isRoot ? 700 : 500),
          labelFill: (d: any) => (d.data?.isRoot ? '#1e40af' : '#1e3a8a'),
          fill: (d: any) => (d.data?.isRoot ? '#bfdbfe' : '#eff6ff'),
          stroke: (d: any) => (d.data?.isRoot ? '#60a5fa' : '#bfdbfe'),
          color: (d: any) => (d.data?.isRoot ? '#1e40af' : '#1e40af'),
          size: (d: any) => (d.data?.isRoot ? 56 : 34),
          lineWidth: (d: any) => (d.data?.isRoot ? 2.5 : 1.5),
          shadowColor: (d: any) => (d.data?.isRoot ? 'rgba(96,165,250,0.4)' : 'rgba(191,219,254,0.5)'),
          shadowBlur: (d: any) => (d.data?.isRoot ? 18 : 6),
          shadowOffsetX: 0,
          shadowOffsetY: (d: any) => (d.data?.isRoot ? 3 : 1),
        },
      },
      edge: {
        style: {
          labelText: (d: any) => d.data?.label ?? '',
          labelBackground: true,
          labelBackgroundFill: '#ffffff',
          labelBackgroundOpacity: 0.92,
          labelBackgroundRadius: 4,
          labelFontSize: 11,
          labelFontWeight: 500,
          stroke: edgeColor,
          lineWidth: edgeLineWidth,
          lineDash: edgeDash,
          startArrow: (d: any) => d.data?.label === '配套',
          startArrowSize: 8,
          endArrow: true,
          endArrowSize: 8,
          opacity: edgeOpacity,
        },
      },
      layout: {
        type: 'd3-force',
        link: {distance: 120},
        manyBody: {strength: -Math.max(500, 600 - nodes.length * 5)},
        collide: {radius: 40},
        iterations: 300,
      },
      transforms: ['process-parallel-edges'],
      plugins: [
        {
          type: 'minimap',
          size: [120, 80],
          position: 'bottom-left',
        },
      ],
    });

    await graphRef.value.render();
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  loadGraph();
});
onUnmounted(() => {
  graphRef.value?.destroy();
});
</script>

<template>
  <div class="h-full flex flex-col gap-3" style="background:#f8fafc;">

    <!-- 查询栏 -->
    <div class="filter-bar">
      <!-- 第一行：主查询 -->
      <div class="filter-row">
        <div class="filter-group">
          <label class="filter-label">根节点</label>
          <NInput
            v-model:value="subjObj"
            placeholder="输入对象名称"
            style="width:200px;"
            @keydown.enter="loadGraph"
          />
        </div>
        <div class="filter-divider"/>
        <div class="filter-group">
          <label class="filter-label">展开层级</label>
          <NInputNumber v-model:value="depth" :min="-1" placeholder="-1 不限" style="width:100px;"/>
          <span class="filter-hint">-1 为不限</span>
        </div>
        <div class="filter-divider"/>
        <div class="filter-group">
          <label class="filter-label">关系类型</label>
          <div class="tag-group">
            <button
              v-for="t in REL_TYPE_OPTIONS"
              :key="t"
              :class="{ active: relType === t }"
              :style="relType === t ? `--tag-color:${REL_COLORS[t]}` : ''"
              class="tag-btn"
              @click="toggleRelType(t)"
            >
              <span :style="`background:${REL_COLORS[t]}`" class="tag-dot"/>
              {{ t }}
            </button>
          </div>
        </div>
        <div class="filter-divider"/>
        <div class="filter-group">
          <label class="filter-label">置信度</label>
          <div class="tag-group">
            <button
              v-for="c in CONFIDENCE_OPTIONS"
              :key="c.value"
              :class="{ active: confidence === c.value, [`conf-${c.value}`]: true }"
              class="tag-btn conf-btn"
              @click="toggleConfidence(c.value)"
            >{{ c.label }}
            </button>
          </div>
        </div>
        <div style="margin-left:auto;">
          <NButton :loading="loading" style="min-width:72px;" type="primary" @click="loadGraph">
            查询
          </NButton>
        </div>
      </div>
    </div>

    <!-- 图谱区域 -->
    <div class="flex-1"
         style="position:relative;background:#fff;border-radius:12px;box-shadow:0 1px 4px rgba(30,64,175,0.07);border:1px solid #e2e8f0;overflow:hidden;min-height:560px;">
      <div ref="containerRef" style="width:100%;height:100%;min-height:560px;"/>

      <!-- 图例面板 -->
      <div
        style="position:absolute;top:16px;right:16px;background:rgba(255,255,255,0.97);border:1px solid #e2e8f0;border-radius:10px;padding:14px 16px;font-size:12px;box-shadow:0 4px 16px rgba(30,64,175,0.1);min-width:148px;">
        <div
          style="font-size:10px;font-weight:700;color:#94a3b8;letter-spacing:0.08em;text-transform:uppercase;margin-bottom:10px;">
          关系类型
        </div>
        <div
          v-for="(color, name) in REL_COLORS"
          :key="name"
          style="display:flex;align-items:center;gap:8px;margin-bottom:7px;"
        >
          <svg height="10" style="flex-shrink:0;" width="28">
            <line :stroke="color" stroke-linecap="round" stroke-width="2.5" x1="0" x2="20" y1="5" y2="5"/>
            <polygon :fill="color" points="20,2 28,5 20,8"/>
          </svg>
          <span style="color:#334155;font-weight:600;">{{ name }}</span>
          <span style="color:#94a3b8;font-size:10px;">{{ REL_LABELS[name] }}</span>
        </div>

        <div
          style="border-top:1px solid #f1f5f9;margin:10px 0 8px;font-size:10px;font-weight:700;color:#94a3b8;letter-spacing:0.08em;text-transform:uppercase;">
          置信度
        </div>
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:5px;">
          <svg height="10" width="28">
            <line stroke="#64748b" stroke-linecap="round" stroke-width="2.5" x1="0" x2="28" y1="5" y2="5"/>
          </svg>
          <span style="color:#334155;font-weight:500;">High</span>
        </div>
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:5px;opacity:0.7;">
          <svg height="10" width="28">
            <line stroke="#64748b" stroke-dasharray="8,4" stroke-linecap="round" stroke-width="1.8" x1="0" x2="28"
                  y1="5" y2="5"/>
          </svg>
          <span style="color:#334155;font-weight:500;">Medium</span>
        </div>
        <div style="display:flex;align-items:center;gap:8px;opacity:0.4;">
          <svg height="10" width="28">
            <line stroke="#64748b" stroke-dasharray="2,6" stroke-linecap="round" stroke-width="1" x1="0" x2="28" y1="5"
                  y2="5"/>
          </svg>
          <span style="color:#334155;font-weight:500;">Low</span>
        </div>
      </div>
    </div>

  </div>
</template>

<style scoped>
/* ── 查询栏容器 ── */
.filter-bar {
  background: #fff;
  border-radius: 12px;
  padding: 12px 20px;
  box-shadow: 0 1px 4px rgba(30, 64, 175, 0.07);
  border: 1px solid #e2e8f0;
}

.filter-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 0;
}

/* ── 每组控件 ── */
.filter-group {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 16px;
}

.filter-group:first-child {
  padding-left: 0;
}

.filter-label {
  font-size: 12px;
  font-weight: 600;
  color: #94a3b8;
  white-space: nowrap;
  letter-spacing: 0.02em;
  text-transform: uppercase;
}

.filter-hint {
  font-size: 11px;
  color: #cbd5e1;
}

/* ── 竖向分隔线 ── */
.filter-divider {
  width: 1px;
  height: 28px;
  background: #e2e8f0;
  flex-shrink: 0;
}

/* ── 标签按钮组 ── */
.tag-group {
  display: flex;
  gap: 4px;
}

.tag-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 10px;
  border-radius: 20px;
  border: 1px solid #e2e8f0;
  font-size: 12px;
  font-weight: 500;
  color: #64748b;
  background: #f8fafc;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s, color 0.15s, box-shadow 0.15s;
  user-select: none;
  outline: none;
}

.tag-btn:hover {
  border-color: #bfdbfe;
  color: #1d4ed8;
  background: #eff6ff;
}

.tag-btn:focus-visible {
  box-shadow: 0 0 0 2px #93c5fd;
}

/* 关系类型激活：用 CSS 变量注入颜色 */
.tag-btn.active {
  background: var(--tag-color, #3b82f6);
  border-color: var(--tag-color, #3b82f6);
  color: #fff;
  box-shadow: 0 2px 6px color-mix(in srgb, var(--tag-color, #3b82f6) 40%, transparent);
}

/* 置信度激活色 */
.conf-btn.active.conf-high {
  --tag-color: #22c55e;
}

.conf-btn.active.conf-medium {
  --tag-color: #f59e0b;
}

.conf-btn.active.conf-low {
  --tag-color: #94a3b8;
}

/* 置信度未激活时的小色块提示 */
.conf-btn:not(.active).conf-high {
  border-left: 3px solid #22c55e;
}

.conf-btn:not(.active).conf-medium {
  border-left: 3px solid #f59e0b;
}

.conf-btn:not(.active).conf-low {
  border-left: 3px solid #94a3b8;
}

/* 关系类型色点 */
.tag-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
  opacity: 0.85;
}

.tag-btn.active .tag-dot {
  background: rgba(255, 255, 255, 0.8) !important;
}
</style>
