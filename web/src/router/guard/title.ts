import type { Router } from 'vue-router';
import { useTitle } from '@vueuse/core';
import { getBrandVariant } from '@/utils/brand-config';

const BRAND_COPY = {
  standard: {
    title: 'AI-Agent 同道'
  },
  generic: {
    title: '个人助理服务（agent-deepseek）'
  }
} as const;

export function createDocumentTitleGuard(router: Router) {
  router.afterEach(() => {
    const brandTitle = BRAND_COPY[getBrandVariant()].title;
    useTitle(brandTitle);
  });
}
