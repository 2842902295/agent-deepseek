<script setup lang="ts">
import { ref, watch, onMounted, onBeforeUnmount } from 'vue';
import * as echarts from 'echarts';
import type { EChartsOption } from 'echarts';

const props = defineProps<{
  tagDistribution: Record<string, number>;
}>();

const chartRef = ref<HTMLElement>();
let chartInstance: echarts.ECharts | null = null;

// 标签显示顺序（按重要性）
const tagOrder = [
  '标准化对象一致',
  '适用范围重叠',
  '通用专用关系',
  '同系列标准'
];

function initChart() {
  if (!chartRef.value) return;

  chartInstance = echarts.init(chartRef.value);

  // 按指定顺序获取数据
  const indicators = tagOrder.map(tag => ({
    name: tag,
    max: 1
  }));

  const values = tagOrder.map(tag => props.tagDistribution[tag] || 0);

  const option: EChartsOption = {
    title: {
      text: '标签分布概览',
      left: 'center',
      top: 0,
      textStyle: {
        fontSize: 14,
        fontWeight: 'normal',
        color: '#333'
      }
    },
    tooltip: {
      trigger: 'item',
      formatter: (params: any) => {
        const data = params.value;
        let result = '标签激活率<br/>';
        tagOrder.forEach((tag, index) => {
          const percentage = ((data[index] || 0) * 100).toFixed(1);
          result += `${tag}: ${percentage}%<br/>`;
        });
        return result;
      }
    },
    radar: {
      indicator: indicators,
      shape: 'polygon',
      center: ['50%', '55%'],
      radius: '65%',
      axisName: {
        fontSize: 11,
        color: '#333'
      },
      axisLabel: {
        show: false  // 隐藏刻度值
      },
      splitArea: {
        areaStyle: {
          color: ['rgba(59, 130, 246, 0.05)', 'rgba(59, 130, 246, 0.1)']
        }
      },
      splitLine: {
        lineStyle: {
          color: '#e5e7eb'
        }
      }
    },
    series: [
      {
        name: '标签激活率',
        type: 'radar',
        data: [
          {
            value: values,
            name: '激活率',
            areaStyle: {
              color: 'rgba(59, 130, 246, 0.3)'
            },
            lineStyle: {
              color: '#3b82f6',
              width: 2
            },
            itemStyle: {
              color: '#3b82f6'
            }
          }
        ]
      }
    ]
  };

  chartInstance.setOption(option);
}

function updateChart() {
  if (!chartInstance) return;

  const values = tagOrder.map(tag => props.tagDistribution[tag] || 0);

  chartInstance.setOption({
    series: [
      {
        data: [
          {
            value: values
          }
        ]
      }
    ]
  });
}

watch(
  () => props.tagDistribution,
  () => {
    updateChart();
  },
  { deep: true }
);

onMounted(() => {
  initChart();
  window.addEventListener('resize', () => chartInstance?.resize());
});

onBeforeUnmount(() => {
  chartInstance?.dispose();
  window.removeEventListener('resize', () => chartInstance?.resize());
});
</script>

<template>
  <div ref="chartRef" class="w-full h-full" style="min-height: 200px"></div>
</template>
