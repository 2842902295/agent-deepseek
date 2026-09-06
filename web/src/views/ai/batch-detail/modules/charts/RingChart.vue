<script setup lang="ts">
import { ref, watch, onMounted, onBeforeUnmount } from 'vue';
import * as echarts from 'echarts';
import type { EChartsOption } from 'echarts';

const props = defineProps<{
  needAttentionCount: number;
  totalCount: number;
  highRiskTagCount: number;
}>();

const chartRef = ref<HTMLElement>();
let chartInstance: echarts.ECharts | null = null;

function initChart() {
  if (!chartRef.value) return;

  chartInstance = echarts.init(chartRef.value);

  const option: EChartsOption = {
    title: {
      text: '',
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
      formatter: '{a} <br/>{b}: {c} ({d}%)'
    },
    legend: {
      orient: 'horizontal',
      bottom: 12,
      left: 'center',
      itemGap: 20,
      itemWidth: 12,
      itemHeight: 10,
      data: ['需要关注', '无需关注'],
      textStyle: {
        fontSize: 12
      }
    },
    graphic: [
      {
        type: 'text',
        left: 'center',
        top: 'center',
        style: {
          text: `需要关注\n${props.needAttentionCount}个`,
          textAlign: 'center',
          fill: '#f59e0b',
          fontSize: 13,
          fontWeight: 'bold'
        } as any
      }
    ],
    series: [
      {
        name: '标准类型',
        type: 'pie',
        radius: ['48%', '66%'],
        center: ['50%', '44%'],
        avoidLabelOverlap: false,
        label: {
          show: false
        },
        emphasis: {
          label: {
            show: true,
            fontSize: 14,
            fontWeight: 'bold'
          }
        },
        labelLine: {
          show: false
        },
        data: [
          {
            value: props.needAttentionCount,
            name: '需要关注',
            itemStyle: {
              color: '#f59e0b'
            }
          },
          {
            value: props.totalCount - props.needAttentionCount,
            name: '无需关注',
            itemStyle: {
              color: '#10b981'
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

  chartInstance.setOption({
    graphic: [
      {
        type: 'text',
        left: 'center',
        top: 'center',
        style: {
          text: `需要关注\n${props.needAttentionCount}个`,
          textAlign: 'center',
          fill: '#f59e0b',
          fontSize: 16,
          fontWeight: 'bold'
        }
      }
    ],
    series: [
      {
        data: [
          {
            value: props.needAttentionCount,
            name: '需要关注'
          },
          {
            value: props.totalCount - props.needAttentionCount,
            name: '无需关注'
          }
        ]
      }
    ]
  });
}

watch(
  () => [props.needAttentionCount, props.totalCount, props.highRiskTagCount],
  () => {
    updateChart();
  }
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
  <div ref="chartRef" style="width: 320px; height: 270px;"></div>
</template>
