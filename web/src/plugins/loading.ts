// @unocss-include
import { getRgb } from '@sa/color';
import { DARK_CLASS } from '@/constants/app';
import { localStg } from '@/utils/storage';
import { toggleHtmlClass } from '@/utils/common';

const BRAND_COPY = {
  standard: {
    title: 'AI-Agent 同道'
  },
  generic: {
    title: '个人助理服务（agent-deepseek）'
  }
} as const;

export function setupLoading() {
  const themeColor = localStg.get('themeColor') || '#646cff';
  const darkMode = localStg.get('darkMode') || false;

  const { r, g, b } = getRgb(themeColor);

  const cssVars = `--primary-color: ${r} ${g} ${b}`;

  if (darkMode) {
    toggleHtmlClass(DARK_CLASS).add();
  }

  const brandKey = (localStorage.getItem('app_brand') ?? 'standard') as keyof typeof BRAND_COPY;
  const brand = BRAND_COPY[brandKey] ?? BRAND_COPY.standard;

  document.title = brand.title;

  const loadingClasses = [
    'left-0 top-0',
    'left-0 bottom-0 animate-delay-500',
    'right-0 top-0 animate-delay-1000',
    'right-0 bottom-0 animate-delay-1500'
  ];

  const dot = loadingClasses
    .map(item => {
      return `<div class="absolute w-16px h-16px bg-primary rounded-8px animate-pulse ${item}"></div>`;
    })
    .join('\n');

  const loading = `
<div class="fixed-center flex-col bg-layout" style="${cssVars}">
  <div class="w-200px h-200px">
    <img src="/xiaonian-logo.png" alt="logo" draggable="false" style="display:block;width:100%;height:100%;object-fit:contain;" />
  </div>
  <div class="w-56px h-56px my-36px">
    <div class="relative h-full animate-spin">
      ${dot}
    </div>
  </div>
  <h2 class="text-32px font-500 text-primary">${brand.title}</h2>
</div>`;

  const app = document.getElementById('app');

  if (app) {
    app.innerHTML = loading;
  }
}
