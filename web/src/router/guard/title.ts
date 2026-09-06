import type { Router } from 'vue-router';
import { useTitle } from '@vueuse/core';
import { getBrandVariant } from '@/utils/brand-config';

const BRAND_COPY = {
  standard: {
    title: '同道·标准工作台'
  },
  generic: {
    title: 'agent-deepseek'
  }
} as const;

export function createDocumentTitleGuard(router: Router) {
  router.afterEach(() => {
    const brandTitle = BRAND_COPY[getBrandVariant()].title;
    useTitle(brandTitle);
  });
}
