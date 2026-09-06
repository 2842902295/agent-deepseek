<script setup lang="ts">
// import HeaderBanner from './modules/header-banner.vue';
import CardData from './modules/card-data.vue';
import QuickActions from './modules/quick-actions.vue';
import ProjectNews from './modules/project-news.vue';
</script>

<template>
  <NSpace vertical :size="16">
    <!-- 顶部横幅 -->
<!--    <HeaderBanner />-->

    <!-- 核心数据统计 -->
<!--    <div class="section-wrapper">-->
<!--      <CardData />-->
<!--    </div>-->

    <!-- 快捷入口 -->
    <div class="section-wrapper">
      <QuickActions />
    </div>

  </NSpace>
</template>

<style scoped>
.home-container {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 0;
}

.section-wrapper {
  width: 100%;
}

@media (max-width: 768px) {
  .home-container {
    gap: 12px;
  }
}
</style>
