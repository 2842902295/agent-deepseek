<script setup lang="ts">
import {onBeforeUnmount, onMounted, ref, watch} from 'vue';
import * as echarts from 'echarts/core';
import {
  BarChart,
  BoxplotChart,
  CandlestickChart,
  FunnelChart,
  GaugeChart,
  HeatmapChart,
  LineChart,
  PieChart,
  RadarChart,
  SankeyChart,
  ScatterChart,
  TreemapChart
} from 'echarts/charts';
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  PolarComponent,
  RadarComponent,
  TitleComponent,
  TooltipComponent,
  VisualMapComponent
} from 'echarts/components';
import {CanvasRenderer} from 'echarts/renderers';

echarts.use([
  BarChart,
  BoxplotChart,
  CandlestickChart,
  FunnelChart,
  GaugeChart,
  HeatmapChart,
  LineChart,
  PieChart,
  RadarChart,
  SankeyChart,
  ScatterChart,
  TreemapChart,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  PolarComponent,
  RadarComponent,
  TitleComponent,
  TooltipComponent,
  VisualMapComponent,
  CanvasRenderer
]);

interface ChartSpec {
  type?: string;
  title?: string | { text?: string };
  // 简化格式兼容字段
  xField?: string;
  yField?: string;
  data?: any;
  x?: string;
  y?: string;
  value?: string;
  category?: string;
  categories?: any[];
  values?: any[];
  labels?: any[];
  // 原生 ECharts option 字段
  series?: any;
  xAxis?: any;
  yAxis?: any;
  radar?: any;
  visualMap?: any;
  [k: string]: any;
}

const props = defineProps<{ spec: ChartSpec; noTitle?: boolean }>();

const container = ref<HTMLElement | null>(null);
let instance: echarts.ECharts | null = null;

function pickTitle(spec: ChartSpec): string | undefined {
  if (props.noTitle) return undefined;
  if (!spec.title) return undefined;
  if (typeof spec.title === 'string') return spec.title;
  if (typeof spec.title === 'object' && spec.title.text) return spec.title.text;
  return undefined;
}

function normalizeNumber(val: any): number {
  if (typeof val === 'number') return val;
  if (typeof val === 'string') {
    const n = parseFloat(val.replace(/[^\d.+-]/g, ''));
    return isNaN(n) ? 0 : n;
  }
  return 0;
}

// 根据标签数量决定 axisLabel 配置：≤7 不旋转，≤12 旋转30°，更多旋转45°
function categoryAxisLabel(categories: any[]): Record<string, any> {
  const n = categories.length;
  return {interval: 0, rotate: n > 12 ? 45 : n > 7 ? 30 : 0};
}

// 简化格式 → 完整 ECharts option（兼容旧格式）
function buildFromSimple(spec: ChartSpec): echarts.EChartsCoreOption | null {
  // 如果已经是原生 ECharts option（有 series 字段），不走此分支
  if (spec.series !== undefined) return null;

  const type = (spec.type || 'bar') as string;
  let rows: Array<Record<string, any>> = [];
  let xField = spec.xField || spec.x || spec.category || 'name';
  let yField = spec.yField || spec.y || spec.value || 'value';

  if (Array.isArray(spec.data) && spec.data.length) {
    const first = spec.data[0];
    if (Array.isArray(first)) {
      rows = spec.data.map((r: any[]) => ({[xField]: r[0], [yField]: r[1]}));
    } else if (typeof first === 'object' && first !== null) {
      rows = spec.data;
      if (!spec.xField && !spec.x && !spec.category) {
        const keys = Object.keys(first);
        xField = keys.includes('name') ? 'name' : keys[0];
      }
      if (!spec.yField && !spec.y && !spec.value) {
        const keys = Object.keys(first);
        yField = keys.includes('value') ? 'value' : (keys.length > 1 ? keys[1] : keys[0]);
      }
    } else {
      rows = spec.data.map((v: any, i: number) => ({[xField]: String(i + 1), [yField]: v}));
    }
  } else if (Array.isArray(spec.categories) && Array.isArray(spec.values)) {
    rows = spec.categories.map((c: any, i: number) => ({[xField]: c, [yField]: spec.values![i]}));
  } else if (Array.isArray(spec.labels) && Array.isArray(spec.values)) {
    rows = spec.labels.map((c: any, i: number) => ({[xField]: c, [yField]: spec.values![i]}));
  }

  const title = pickTitle(spec);
  const common: any = {
    title: title ? {text: title, left: 'center', textStyle: {fontSize: 14, fontWeight: 500}} : undefined,
    tooltip: {trigger: type === 'pie' || type === 'donut' ? 'item' : 'axis'},
    grid: {left: 48, right: 24, top: title ? 48 : 24, bottom: 36, containLabel: true}
  };

  if (type === 'pie' || type === 'donut') {
    return {
      ...common,
      series: [{
        type: 'pie',
        radius: type === 'donut' ? ['35%', '58%'] : '58%',
        data: rows.map(d => ({name: d[xField], value: normalizeNumber(d[yField])}))
      }]
    };
  }
  if (type === 'scatter') {
    return {
      ...common,
      xAxis: {type: 'value', name: xField},
      yAxis: {type: 'value', name: yField},
      series: [{type: 'scatter', data: rows.map(d => [normalizeNumber(d[xField]), normalizeNumber(d[yField])])}]
    };
  }
  return {
    ...common,
    xAxis: {type: 'category', data: rows.map(d => d[xField]), axisLabel: categoryAxisLabel(rows.map(d => d[xField]))},
    yAxis: {type: 'value'},
    series: [{type: type === 'line' ? 'line' : 'bar', data: rows.map(d => normalizeNumber(d[yField]))}]
  };
}

// 修复原生 ECharts option 中的常见问题（深拷贝后再修改，避免触发 Vue 响应式）
function sanitizeOption(opt: any): any {
  if (!opt || typeof opt !== 'object') return opt;
  opt = JSON.parse(JSON.stringify(opt));

  // 将 series 统一为数组
  if (opt.series && !Array.isArray(opt.series)) {
    opt.series = [opt.series];
  }

  // 修复 series 中的数值字段
  if (Array.isArray(opt.series)) {
    opt.series = opt.series.map((s: any) => {
      if (!s || typeof s !== 'object') return s;
      const seriesType = s.type || '';

      if (Array.isArray(s.data)) {
        // pie/funnel：[{name, value}] 中的 value 必须是数字
        if (seriesType === 'pie' || seriesType === 'funnel') {
          s.data = s.data.map((item: any) => {
            if (typeof item === 'object' && item !== null && 'value' in item) {
              return {...item, value: normalizeNumber(item.value)};
            }
            return item;
          });
        }
        // bar/line：[数值] 或 [{value: 数值}]
        else if (seriesType === 'bar' || seriesType === 'line') {
          s.data = s.data.map((item: any) => {
            if (typeof item === 'number') return item;
            if (typeof item === 'string') return normalizeNumber(item);
            if (typeof item === 'object' && item !== null && 'value' in item) {
              return {...item, value: normalizeNumber(item.value)};
            }
            return item;
          });
        }
      }
      return s;
    });
  }

  return opt;
}

function buildOption(spec: ChartSpec): echarts.EChartsCoreOption {
  // 先尝试简化格式
  const simple = buildFromSimple(spec);
  if (simple) return sanitizeOption(simple);

  // 原生 ECharts option：注入 tooltip，修复数值问题
  const opt: any = {...spec};

  // noTitle 处理
  if (props.noTitle && opt.title) {
    delete opt.title;
  } else if (opt.title) {
    // 统一 title 格式
    if (typeof opt.title === 'string') {
      opt.title = {text: opt.title, left: 'center'};
    }
  }

  if (!opt.tooltip) {
    const firstSeriesType = Array.isArray(opt.series) ? opt.series[0]?.type : opt.series?.type;
    opt.tooltip = {trigger: (firstSeriesType === 'pie' || firstSeriesType === 'funnel') ? 'item' : 'axis'};
  }

  // 强制 category 轴不跳标签，根据标签数量自动旋转
  for (const axis of [opt.xAxis, opt.yAxis].flat().filter(Boolean)) {
    if (axis?.type === 'category') {
      const n = Array.isArray(axis.data) ? axis.data.length : 0;
      axis.axisLabel = {interval: 0, rotate: n > 12 ? 45 : n > 7 ? 30 : 0, ...axis.axisLabel};
    }
  }

  // radar 图必须有 indicator，否则 ECharts 内部会报错；从 series data 推断兜底
  const firstSeriesType = Array.isArray(opt.series) ? opt.series[0]?.type : opt.series?.type;
  if (firstSeriesType === 'radar') {
    if (!opt.radar) opt.radar = {};
    if (!Array.isArray(opt.radar.indicator) || opt.radar.indicator.length === 0) {
      const firstValue = opt.series[0]?.data?.[0]?.value;
      if (Array.isArray(firstValue) && firstValue.length > 0) {
        opt.radar.indicator = firstValue.map((_: any, i: number) => ({name: `维度${i + 1}`, max: 100}));
      } else {
        console.error('[chart-render] radar 图缺少 indicator 且无法推断，已跳过渲染', opt);
        return null;
      }
    }
  }

  // treemap 补间距（AI 手写 spec 通常不带 levels）
  if (firstSeriesType === 'treemap') {
    const s = Array.isArray(opt.series) ? opt.series[0] : opt.series;
    if (s && !s.levels) {
      s.nodeGap = s.nodeGap ?? 4;
      s.levels = [
        {itemStyle: {gapWidth: 3, borderWidth: 2, borderColor: '#fff'}},
        {itemStyle: {gapWidth: 0.5, borderWidth: 1, borderColor: '#ffffff60'}},
      ];
    }
  }

  // pie 图兜底布局（避免图例遮挡标签文字）
  if (firstSeriesType === 'pie') {
    if (!opt.legend) {
      opt.legend = {bottom: 10, left: 'center'};
    } else if (opt.legend.bottom === 0) {
      opt.legend.bottom = 10;
    }
    const s = Array.isArray(opt.series) ? opt.series[0] : opt.series;
    if (s && !s.center) {
      s.center = ['50%', '42%'];
    }
    if (s && !s.label) {
      s.label = {fontSize: 11, lineHeight: 14};
    }
  }

  return sanitizeOption(opt);
}

// 动态调整容器高度（sankey / treemap / radar 等需要更多空间）
function getChartHeight(spec: ChartSpec): string {
  const type = spec.type || (Array.isArray(spec.series) ? spec.series[0]?.type : spec.series?.type) || 'bar';
  if (['sankey', 'treemap', 'sunburst'].includes(type)) return '400px';
  if (['radar', 'heatmap'].includes(type)) return '360px';
  if (type === 'gauge') return '320px';
  return '280px';
}

const chartHeight = ref('280px');

function render() {
  if (!container.value) return;
  chartHeight.value = getChartHeight(props.spec);
  if (!instance) {
    instance = echarts.init(container.value);
  }
  try {
    const option = buildOption(props.spec);
    if (!option) {
      console.warn('[chart-render] buildOption 返回 null，跳过渲染');
      return;
    }
    instance.setOption(option, true);
  } catch (e) {
    console.error('[chart-render] ECharts setOption 失败:', e, props.spec);
  }
}

function handleResize() {
  instance?.resize();
}

onMounted(() => {
  render();
  window.addEventListener('resize', handleResize);
});

// 用序列化字符串做 watch 源，避免 deep:true 对响应式对象的递归追踪导致死循环
watch(
  () => JSON.stringify(props.spec),
  render,
  {flush: 'post'}
);

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize);
  instance?.dispose();
  instance = null;
});
</script>

<template>
  <div ref="container" class="chart-render" :style="{height: chartHeight}"/>
</template>

<style scoped>
.chart-render {
  width: 100%;
  height: 280px;
  border-radius: 12px;
}
</style>
