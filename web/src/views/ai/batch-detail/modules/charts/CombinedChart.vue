<script setup lang="ts">
import { ref, watch, onMounted, onBeforeUnmount, computed } from 'vue';
import * as echarts from 'echarts';
import type { EChartsOption } from 'echarts';

const props = defineProps<{
  tagRanking: Array<{
    tag: string;
    count: number;
    percentage: number;
  }>;
  tagCombinations: {
    single_tag: number;
    double_tag: number;
    triple_plus: number;
  };
}>();

const chartRef = ref<HTMLElement>();
let chartInstance: echarts.ECharts | null = null;

// 标签固定顺序
const tagOrder = [
  '标准化对象一致',
  '适用范围重叠',
  '通用专用关系',
  '同系列标准'
];

// 计算组合强度（更均匀的分布）
const combinedData = computed(() => {
  const tagDataMap = new Map(props.tagRanking.map(item => [item.tag, item]));

  // 计算每个标签的组合强度估算
  const singleDouble = props.tagCombinations.single_tag + props.tagCombinations.double_tag;
  const triple = Math.round(props.tagCombinations.triple_plus * 0.4); // 假设40%是三标签
  const multi = props.tagCombinations.triple_plus - triple; // 剩余是多标签
  const total = singleDouble + triple + multi;

  // 调整比例，让每种类型都更明显
  const singleDoubleRatio = singleDouble / total;
  const tripleRatio = triple / total;
  const multiRatio = multi / total;

  return tagOrder.map(tag => {
    const tagData = tagDataMap.get(tag) || { tag, count: 0, percentage: 0 };
    const count = tagData.count;

    // 按实际比例分配，确保三部分都可见
    const sd = Math.max(1, Math.round(count * singleDoubleRatio));
    const tr = Math.max(1, Math.round(count * tripleRatio));
    const mu = Math.max(1, Math.round(count * multiRatio));

    // 调整使总和接近实际值
    const sum = sd + tr + mu;
    const factor = count / sum;

    return {
      tag,
      singleDouble: Math.round(sd * factor),
      triple: Math.round(tr * factor),
      multi: Math.round(mu * factor),
      total: count
    };
  });
});

function initChart() {
  if (!chartRef.value) return;

  chartInstance = echarts.init(chartRef.value);

  const data = combinedData.value;

  const option: EChartsOption = {
    title: {
      text: '标签激活与组合强度分析',
      left: 'center',
      top: 0,
      textStyle: {
        fontSize: 14,
        fontWeight: 'normal',
        color: '#333'
      }
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: {
        type: 'shadow'
      },
      formatter: (params: any) => {
        let result = `${params[0].axisValue}<br/>`;
        let total = 0;
        params.forEach((item: any) => {
          result += `${item.marker} ${item.seriesName}: ${item.value}<br/>`;
          total += item.value;
        });
        result += `<b>总激活: ${total}</b>`;
        return result;
      }
    },
    legend: {
      bottom: 5,
      left: 'center',
      data: ['单双标签组合', '三标签组合', '多标签组合'],
      textStyle: {
        fontSize: 11
      }
    },
    grid: {
      left: '3%',
      right: '4%',
      top: 40,
      bottom: 40,
      containLabel: true
    },
    xAxis: {
      type: 'category',
      data: data.map(item => item.tag),
      axisLabel: {
        fontSize: 11,
        rotate: 0,
        interval: 0
      }
    },
    yAxis: {
      type: 'value',
      axisLabel: {
        fontSize: 11
      },
      splitLine: {
        lineStyle: {
          type: 'dashed',
          color: '#e5e7eb'
        }
      }
    },
    series: [
      {
        name: '单双标签组合',
        type: 'bar',
        stack: 'total',
        data: data.map(item => item.singleDouble),
        itemStyle: {
          color: '#93c5fd'  // 浅蓝色
        },
        barWidth: '60%'
      },
      {
        name: '三标签组合',
        type: 'bar',
        stack: 'total',
        data: data.map(item => item.triple),
        itemStyle: {
          color: '#60a5fa'  // 中蓝色
        }
      },
      {
        name: '多标签组合',
        type: 'bar',
        stack: 'total',
        data: data.map(item => item.multi),
        itemStyle: {
          color: '#2563eb'  // 深蓝色
        },
        label: {
          show: true,
          position: 'top',
          formatter: (params: any) => {
            return data[params.dataIndex].total;
          },
          fontSize: 11,
          fontWeight: 'bold',
          color: '#333'
        }
      }
    ]
  };

  chartInstance.setOption(option);
}

function updateChart() {
  if (!chartInstance) return;

  const data = combinedData.value;

  chartInstance.setOption({
    xAxis: {
      data: data.map(item => item.tag)
    },
    series: [
      {
        data: data.map(item => item.singleDouble)
      },
      {
        data: data.map(item => item.triple)
      },
      {
        data: data.map(item => item.multi)
      }
    ]
  });
}

watch(
  () => [props.tagRanking, props.tagCombinations],
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
