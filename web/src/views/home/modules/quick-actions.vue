<script setup lang="ts">
import { useRouter } from 'vue-router';

defineOptions({
  name: 'QuickActions'
});

const router = useRouter();

interface QuickAction {
  title: string;
  description: string;
  icon: string;
  route: string;
  color: string;
}

const actions: QuickAction[] = [
  {
    title: '批量查重',
    description: '快速识别重复标准',
    icon: 'mdi:content-duplicate',
    route: '/ai/batch-deduplication',
    color: '#3b82f6'
  },
  {
    title: 'AI智能比对',
    description: '标准对比分析',
    icon: 'mdi:robot-outline',
    route: '/ai/batch-detail',
    color: '#10b981'
  },
  // 以下入口暂时隐藏，保留以便后续恢复
  /*
  {
    title: '标准管理',
    description: '标准信息维护',
    icon: 'mdi:file-document-edit',
    route: '/standard/list',
    color: '#f59e0b'
  },
  {
    title: '系统设置',
    description: '配置系统参数',
    icon: 'mdi:cog',
    route: '/system/settings',
    color: '#8b5cf6'
  }
  */
];

function handleAction(route: string) {
  router.push(route);
}
</script>

<template>
  <NCard :bordered="false" title="快捷入口" class="card-wrapper">
    <template #header-extra>
      <span class="text-sm text-gray-500">常用功能快速访问</span>
    </template>
    <div class="quick-actions-grid">
      <div
        v-for="(action, index) in actions"
        :key="index"
        class="action-item"
        @click="handleAction(action.route)"
      >
        <div class="action-icon" :style="{ background: `${action.color}15`, color: action.color }">
          <icon-mdi:content-duplicate v-if="action.icon === 'mdi:content-duplicate'" />
          <icon-mdi:robot-outline v-else-if="action.icon === 'mdi:robot-outline'" />
          <icon-mdi:file-document-edit v-else-if="action.icon === 'mdi:file-document-edit'" />
          <icon-mdi:cog v-else />
        </div>
        <div class="action-content">
          <h4 class="action-title">{{ action.title }}</h4>
          <p class="action-description">{{ action.description }}</p>
        </div>
        <div class="action-arrow">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
          </svg>
        </div>
      </div>
    </div>
  </NCard>
</template>

<style scoped>
.quick-actions-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 16px;
}

.action-item {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 20px;
  background: white;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  cursor: pointer;
  transition: all 0.3s ease;
}

.action-item:hover {
  transform: translateX(4px);
  border-color: #3b82f6;
  box-shadow: 0 4px 12px rgba(59, 130, 246, 0.1);
}

.action-icon {
  width: 48px;
  height: 48px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  font-size: 24px;
}

.action-content {
  flex: 1;
  min-width: 0;
}

.action-title {
  font-size: 16px;
  font-weight: 600;
  color: #111827;
  margin: 0 0 4px 0;
}

.action-description {
  font-size: 13px;
  color: #6b7280;
  margin: 0;
}

.action-arrow {
  width: 20px;
  height: 20px;
  color: #9ca3af;
  flex-shrink: 0;
  transition: transform 0.3s ease;
}

.action-item:hover .action-arrow {
  color: #3b82f6;
  transform: translateX(4px);
}

@media (max-width: 768px) {
  .quick-actions-grid {
    grid-template-columns: 1fr;
  }

  .action-item {
    padding: 16px;
  }

  .action-icon {
    width: 40px;
    height: 40px;
    font-size: 20px;
  }
}
</style>
