<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted } from 'vue';
import { NConfigProvider, darkTheme } from 'naive-ui';
import type { WatermarkProps } from 'naive-ui';
import { useAppStore } from './store/modules/app';
import { useThemeStore } from './store/modules/theme';
import { naiveDateLocales, naiveLocales } from './locales/naive';
import NianInboxDrawer from '@/views/ai/nian/components/inbox-drawer.vue';
import { useNian } from '@/views/ai/nian/composables/useNian';

defineOptions({
  name: 'App'
});

const appStore = useAppStore();
const themeStore = useThemeStore();
const { openInbox } = useNian();

// FPS 监视器：仅 test 模式（.env.test 启动）显示，生产构建不渲染
const showFpsMeter = import.meta.env.MODE === 'test';

const naiveDarkTheme = computed(() => (themeStore.darkMode ? darkTheme : undefined));

const naiveLocale = computed(() => {
  return naiveLocales[appStore.locale];
});

const naiveDateLocale = computed(() => {
  return naiveDateLocales[appStore.locale];
});

const watermarkProps = computed<WatermarkProps>(() => {
  return {
    content: themeStore.watermarkContent,
    cross: true,
    fullscreen: true,
    fontSize: 16,
    lineHeight: 16,
    width: 384,
    height: 384,
    xOffset: 12,
    yOffset: 60,
    rotate: -15,
    zIndex: 9999
  };
});

// 全局快捷键：Cmd/Ctrl+K 唤起万用收件箱（任何页面都能用）
function onGlobalKeydown(e: KeyboardEvent) {
  if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
    // 输入框里也截获
    e.preventDefault();
    openInbox();
  }
}

onMounted(() => {
  window.addEventListener('keydown', onGlobalKeydown);
});

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onGlobalKeydown);
});
</script>

<template>
  <NConfigProvider
    :theme="naiveDarkTheme"
    :theme-overrides="themeStore.naiveTheme"
    :locale="naiveLocale"
    :date-locale="naiveDateLocale"
    class="h-full"
  >
    <AppProvider>
      <RouterView class="bg-layout" />
      <NWatermark v-if="themeStore.watermark.visible" v-bind="watermarkProps" />
      <!-- 万用收件箱：全局可唤起 -->
      <NianInboxDrawer />
      <!-- FPS 监视器：仅 .env.test 启动时显示 -->
      <FpsMeter v-if="showFpsMeter" />
    </AppProvider>
  </NConfigProvider>
</template>

<style scoped></style>
