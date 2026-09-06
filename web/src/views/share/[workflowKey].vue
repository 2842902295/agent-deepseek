<script setup lang="ts">
/**
 * 应用制作分享视图页（/share/:workflowKey）
 *
 * 板主在顶栏开启分享后，访客经此链接打开使用这个应用——看到的就是应用本身
 * （HtmlBoard iframe 全屏铺满，没有 wf-topbar 那一层外壳）：数据层写回与页内 AI 通道照常，
 * 但后端对 share 态 token 不注入「编辑文字」脚本，访客不能编辑应用本身。
 *
 * 双模式（板主在分享面板选择）：
 * - 免登录公开：匿名访客直接打开（share-view 免鉴权签发）。
 * - 仅登录用户：匿名访客签发失败（后端 4000 + needLogin 标记）→ HtmlBoard 上报 need-login
 *   → 本页带 redirect 跳登录页，登录后自动回到这里。
 * 页面本身是常量路由（免登录可达），是否需要登录由后端按分享模式裁决。
 */
import {computed} from 'vue';
import {useRoute, useRouter} from 'vue-router';
import HtmlBoard from '@/views/ai/workflow/modules/html-board.vue';

const route = useRoute();
const router = useRouter();
const workflowKey = computed(() => String(route.params.workflowKey || ''));

/** 「仅登录用户」模式 + 匿名访客：去登录页，登录后自动回到本分享页 */
function onNeedLogin() {
  router.push({name: 'login', query: {redirect: `/share/${workflowKey.value}`}});
}
</script>

<template>
  <div class="share-page">
    <HtmlBoard
      v-if="workflowKey"
      :key="workflowKey"
      :workflow-key="workflowKey"
      :version="0"
      :entry-ready="true"
      share
      @need-login="onNeedLogin"
    />
    <div v-else class="share-missing">
      <p class="share-missing-title">分享链接不完整</p>
      <p class="share-missing-sub">链接缺少应用标识，请与分享者确认完整地址</p>
    </div>
  </div>
</template>

<style scoped>
.share-page {
  width: 100vw;
  height: 100vh;
  overflow: hidden;
  background: #fff;
}

.share-missing {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 24px;
}

.share-missing-title {
  margin: 0 0 6px;
  font-size: 15px;
  font-weight: 600;
  color: #0f172a;
}

.share-missing-sub {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: #64748b;
}
</style>
