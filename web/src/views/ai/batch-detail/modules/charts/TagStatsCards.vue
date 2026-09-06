<script setup lang="ts">
import { computed } from 'vue';
import { NCard, NProgress } from 'naive-ui';

const props = defineProps<{
  tagDistribution: Record<string, number>;
  tagRanking?: Array<{ tag: string; count: number; percentage: number }>;
}>();

// 标签配置（按重要性排序）
const tagConfigs = [
  {
    key: '标准化对象一致',
    label: '标准化对象一致',
    color: '#facc15',
    icon: '<svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>'
  },
  {
    key: '适用范围重叠',
    label: '适用范围相似',
    color: '#f59e0b',
    icon: '<svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" /></svg>'
  },
  {
    key: '通用专用关系',
    label: '通用专用关系',
    color: '#3b82f6',
    icon: '<svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" /></svg>'
  },
  {
    key: '同系列标准',
    label: '同系列标准',
    color: '#6b7280',
    icon: '<svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" /></svg>'
  }
];

// 计算每个标签的数据
const tagStats = computed(() => {
  return tagConfigs.map(config => {
    // 优先使用tag_ranking中的count数据
    const rankingItem = props.tagRanking?.find(item => item.tag === config.key);
    const count = rankingItem?.count || 0;
    const percentage = rankingItem?.percentage || (props.tagDistribution[config.key] || 0) * 100;

    return {
      ...config,
      count,
      percentage: Math.round(percentage)
    };
  });
});
</script>

<template>
  <div class="tag-stats-container">
    <div class="cards-grid">
      <div
        v-for="tag in tagStats"
        :key="tag.key"
        class="stat-card"
      >
        <div class="card-header">
          <span class="card-icon" :style="{ color: tag.color }" v-html="tag.icon"></span>
          <span class="card-label">{{ tag.label }}</span>
        </div>
        <div class="card-value">
          <span class="count" :style="{ color: tag.color }">{{ tag.count }}</span>
        </div>
        <div class="card-progress">
          <NProgress
            type="line"
            :percentage="tag.percentage"
            :color="tag.color"
            :show-indicator="false"
            :height="6"
            :border-radius="3"
          />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tag-stats-container {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
}

.cards-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;
  flex: 1;
}

.stat-card {
  background: #ffffff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  transition: all 0.2s ease;
  cursor: pointer;
}

.stat-card:hover {
  border-color: #3b82f6;
  box-shadow: 0 2px 8px rgba(59, 130, 246, 0.15);
  transform: translateY(-2px);
}

.card-header {
  display: flex;
  align-items: center;
  gap: 6px;
}

.card-icon {
  width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.card-icon :deep(svg) {
  width: 100%;
  height: 100%;
}

.card-label {
  font-size: 12px;
  color: #666;
  font-weight: 500;
  line-height: 1.4;
  flex: 1;
}

.card-value {
  display: flex;
  align-items: baseline;
  justify-content: center;
  margin: 4px 0;
}

.count {
  font-size: 24px;
  font-weight: 700;
  line-height: 1;
}

.card-progress {
  margin-top: auto;
}

/* 响应式布局 */
@media (max-width: 1200px) {
  .cards-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media (max-width: 768px) {
  .cards-grid {
    grid-template-columns: 1fr;
  }
}
</style>
