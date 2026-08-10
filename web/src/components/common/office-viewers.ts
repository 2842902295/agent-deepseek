import {defineAsyncComponent} from 'vue';

/**
 * vue-office 四件套：异步按需加载（首次预览才拉包，不影响首屏）。
 * 共享于 attachment-preview-modal.vue（全屏弹层）与 wf-node.vue（节点内联回显），
 * 模块级单例 —— 两处复用同一次 chunk 加载，不会重复拉包。
 */
export const VueOfficeDocx = defineAsyncComponent(async () => {
  await import('@vue-office/docx/lib/index.css');
  return import('@vue-office/docx');
});

export const VueOfficeExcel = defineAsyncComponent(async () => {
  await import('@vue-office/excel/lib/index.css');
  return import('@vue-office/excel');
});

export const VueOfficePdf = defineAsyncComponent(() => import('@vue-office/pdf'));

export const VueOfficePptx = defineAsyncComponent(() => import('@vue-office/pptx'));
