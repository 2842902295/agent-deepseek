import { ref } from 'vue';

/**
 * qa-glass 页主题状态（极光玻璃 / Kimi 风）。
 *
 * 真相源：html[data-qa-theme] —— .qa-shell 与 teleport 到 body 的弹层
 * （ProfileModal / QAUserMenu / SessionSearchModal 等）共用这一个属性，
 * 各组件 scoped 样式里用 `:root[data-qa-theme='ink']` 祖先选择器做覆盖。
 *
 * 默认 glass（存量用户视觉零突变），选择存 localStorage。
 */
export type QaTheme = 'glass' | 'ink';

const STORAGE_KEY = 'qa-glass-theme';

function readStored(): QaTheme {
  try {
    return localStorage.getItem(STORAGE_KEY) === 'ink' ? 'ink' : 'glass';
  } catch {
    return 'glass';
  }
}

export const qaTheme = ref<QaTheme>(readStored());

/* 模块初始化即应用，避免等 onMounted 造成首帧主题闪烁/漏应用 */
applyQaTheme();

/** 把当前主题应用到 html 属性（挂载时 / 切换时调用） */
export function applyQaTheme(theme: QaTheme = qaTheme.value) {
  document.documentElement.dataset.qaTheme = theme;
}

/** 切换并持久化 */
export function setQaTheme(theme: QaTheme) {
  qaTheme.value = theme;
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    /* 隐私模式等场景写失败可忽略 */
  }
  applyQaTheme(theme);
}
