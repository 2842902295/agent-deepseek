<script setup lang="ts">
/**
 * ExcalidrawCanvas —— Vue 里嵌入 Excalidraw（React 18 微渲染）的纯画布。
 *
 * 通用、可复用：上层只负责"什么时候挂载、保存到哪里"，本组件只关心
 * "把场景渲染好、把当前场景吐出来"。
 *
 * 用法：
 *   <ExcalidrawCanvas ref="canvasRef" :source-url="url" @dirty-change="..." />
 *   const json = await canvasRef.value.getSceneJson();
 *
 * 资源：window.EXCALIDRAW_ASSET_PATH = '/excalidraw-assets/'，对应
 * web/scripts/copy-excalidraw-assets.cjs 在 postinstall 时拷过去的目录。
 *
 * 注意：v0.17+ 起 Excalidraw 不再自动注入 CSS，必须手动 import；否则
 * 工具栏/侧边栏布局会"散开"。
 */
import {nextTick, onBeforeUnmount, ref, shallowRef, watch} from 'vue';
import '@excalidraw/excalidraw/index.css';

interface Props {
  /** 远程 .excalidraw 文件 URL，组件会 fetch 后渲染 */
  sourceUrl?: string;
  /** 已经在内存里的场景对象，与 sourceUrl 二选一 */
  initialScene?: Record<string, any> | null;
  /** 是否只读 */
  readOnly?: boolean;
  /** 主题：'light' | 'dark' */
  theme?: 'light' | 'dark';
  /** 界面语言（Excalidraw 内置 i18n），默认中文 */
  langCode?: string;
  /** 是否隐藏 UI（工具栏、侧边栏等），用于 inline 预览模式 */
  hideUI?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  sourceUrl: '',
  initialScene: null,
  readOnly: false,
  theme: 'light',
  langCode: 'zh-CN',
  hideUI: false
});

const emit = defineEmits<{
  (e: 'dirty-change', dirty: boolean): void;
  (e: 'load-error', message: string): void;
  (e: 'ready'): void;
}>();

const containerRef = ref<HTMLDivElement | null>(null);
const loading = ref(true);
const errorMsg = ref('');

const reactRootRef = shallowRef<any>(null);
const excalidrawApiRef = shallowRef<any>(null);
const resizeObserverRef = shallowRef<ResizeObserver | null>(null);
const dirtyRef = ref(false);

async function fetchScene(url: string): Promise<Record<string, any> | null> {
  try {
    const resp = await fetch(url, {cache: 'no-store'});
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return await resp.json();
  } catch (err: any) {
    const msg = err?.message || String(err);
    errorMsg.value = msg;
    emit('load-error', msg);
    return null;
  }
}

// 逐帧探测容器 rect，连续两帧不动才认为「布局稳了」。
// 比依赖 NModal onAfterEnter / 写死 setTimeout 更稳：
// 跟动画时长解耦，动画再快/再慢/被关掉都自适应。
// 90 帧 (~1.5s) 是病态情况下的兜底，正常 2 帧就退出。
async function waitForStable(el: HTMLElement) {
  let prev: DOMRect | null = null;
  let stable = 0;
  for (let i = 0; i < 90; i++) {
    await new Promise((r) => requestAnimationFrame(() => r(null)));
    const cur = el.getBoundingClientRect();
    if (
      prev &&
      cur.width === prev.width &&
      cur.height === prev.height &&
      cur.left === prev.left &&
      cur.top === prev.top
    ) {
      if (++stable >= 2) return;
    } else {
      stable = 0;
    }
    prev = cur;
  }
}

async function mount() {
  if (!containerRef.value) return;

  // 等容器布局稳定（动画结束 / 已经在最终位置）再让 Excalidraw 第一次测量，
  // 避免 transform 动画期间 getBoundingClientRect 测出错位坐标。
  await waitForStable(containerRef.value);
  if (!containerRef.value) return;

  if (!(window as any).EXCALIDRAW_ASSET_PATH) {
    (window as any).EXCALIDRAW_ASSET_PATH = '/excalidraw-assets/';
  }

  let scene: Record<string, any> | null = props.initialScene;
  if (!scene && props.sourceUrl) {
    scene = await fetchScene(props.sourceUrl);
  }
  if (!scene) {
    loading.value = false;
    return;
  }

  const [{default: React}, ReactDOM, excalidrawModule] = await Promise.all([
    import('react'),
    import('react-dom/client'),
    import('@excalidraw/excalidraw')
  ]);
  // Excalidraw 内部代码偶尔读 window.React
  (window as any).React = React;

  const Excalidraw = (excalidrawModule as any).Excalidraw;

  // 关键：清掉历史 appState 里的视口字段。
  // 保存场景时会把 scrollX/scrollY/zoom 一起序列化进文件，下次回显如果带着
  // 这些字段进 initialData，Excalidraw 会优先用它们而不是 scrollToContent。
  // 等于复用了上一次的视口快照——上一次如果在动画中测错了，错误就会传染。
  const cleanAppState: Record<string, any> = {...scene.appState};
  delete cleanAppState.scrollX;
  delete cleanAppState.scrollY;
  delete cleanAppState.zoom;
  // collaborators 必须是 Map，不能是 plain object（v0.18 内部要 .forEach）
  cleanAppState.collaborators = new Map();

  const initialData = {
    elements: scene.elements || [],
    appState: cleanAppState,
    files: scene.files || {},
    scrollToContent: true
  };

  // 用 Excalidraw 官方 getSceneVersion(elements) 做基线判 dirty，避免
  // 时序兜底的脆弱：初始化期间 Excalidraw 会自发触发若干次 onChange（
  // collaborators Map 化 / scrollToContent 调视口 / 内部状态对齐），但
  // elements 数组其实没变，所以版本不变，自然不标 dirty；只有用户真的画
  // 一笔后版本号才会前进。
  const {getSceneVersion} = excalidrawModule as any;
  let baselineVersion: number | null = null;

  const onChange = (elements: any) => {
    if (typeof getSceneVersion !== 'function') return;
    const v = getSceneVersion(elements || []);
    if (baselineVersion === null) {
      // 首次 onChange 当成基线（一定发生在用户操作之前）
      baselineVersion = v;
      return;
    }
    if (v === baselineVersion) return;
    if (!dirtyRef.value) {
      dirtyRef.value = true;
      emit('dirty-change', true);
    }
  };
  const onApi = (api: any) => {
    excalidrawApiRef.value = api;
    emit('ready');
  };

  const root = ReactDOM.createRoot(containerRef.value);
  reactRootRef.value = root;

  const excalidrawProps: any = {
    initialData,
    onChange,
    excalidrawAPI: onApi,
    viewModeEnabled: props.readOnly,
    zenModeEnabled: props.hideUI,
    theme: props.theme,
    langCode: props.langCode
  };

  // viewMode 下隐藏底部工具栏，并禁用滚轮缩放
  if (props.readOnly) {
    excalidrawProps.UIOptions = {
      canvasActions: {
        changeViewBackgroundColor: false,
        clearCanvas: false,
        export: false,
        loadScene: false,
        saveToActiveFile: false,
        saveAsImage: false,
        theme: false,
        toggleTheme: false
      }
    };
    // 禁用滚轮缩放
    excalidrawProps.isCollaborating = false;
    excalidrawProps.detectScroll = false;
    excalidrawProps.handleKeyboardGlobally = false;
  }

  // hideUI 模式：进一步隐藏所有 UI
  if (props.hideUI) {
    excalidrawProps.UIOptions = {
      ...excalidrawProps.UIOptions,
      canvasActions: {
        ...excalidrawProps.UIOptions?.canvasActions,
        changeViewBackgroundColor: false,
        clearCanvas: false,
        export: false,
        loadScene: false,
        saveToActiveFile: false,
        saveAsImage: false,
        theme: false,
        toggleTheme: false
      }
    };
  }

  root.render(React.createElement(Excalidraw, excalidrawProps));
  loading.value = false;

  // 动画期间宿主尺寸/位置会变化，Excalidraw 用 getBoundingClientRect 测一次
  // 后会缓存，导致点击错位。监听 resize，每次都让它重测一遍，最稳。
  if (containerRef.value && typeof ResizeObserver !== 'undefined') {
    const ro = new ResizeObserver(() => {
      excalidrawApiRef.value?.refresh?.();
    });
    ro.observe(containerRef.value);
    resizeObserverRef.value = ro;
  }
}

function unmount() {
  try {
    resizeObserverRef.value?.disconnect();
  } catch {
    /* noop */
  }
  resizeObserverRef.value = null;
  try {
    reactRootRef.value?.unmount?.();
  } catch {
    /* noop */
  }
  reactRootRef.value = null;
  excalidrawApiRef.value = null;
}

async function remount() {
  unmount();
  loading.value = true;
  errorMsg.value = '';
  dirtyRef.value = false;
  emit('dirty-change', false);
  await nextTick();
  await mount();
}

// 任一关键 prop 变更都强制重挂，避免 Excalidraw 内部状态被错误复用
watch(
  () => [props.sourceUrl, props.initialScene],
  () => {
    if (reactRootRef.value) void remount();
  }
);

watch(
  () => props.theme,
  () => {
    if (reactRootRef.value) void remount();
  }
);

// 挂载交给 mount() 内的 waitForStable 自适应等容器稳定，
// 不再分 defer / 非 defer 两条路径
nextTick(mount);
onBeforeUnmount(unmount);

// ── 公开 API ────────────────────────────────────────────────────────────────

/** 拿到当前场景的 .excalidraw JSON 字符串；尚未 ready 时返回 null */
async function getSceneJson(): Promise<string | null> {
  const api = excalidrawApiRef.value;
  if (!api) return null;
  const elements = api.getSceneElements ? api.getSceneElements() : [];
  const appState = api.getAppState ? api.getAppState() : {};
  const files = typeof api.getFiles === 'function' ? api.getFiles() : {};
  const {serializeAsJSON} = await import('@excalidraw/excalidraw');
  return serializeAsJSON(elements, appState, files, 'local');
}

/** 标记为已保存，重置 dirty */
function markClean() {
  if (!dirtyRef.value) return;
  dirtyRef.value = false;
  emit('dirty-change', false);
}

/** 是否有改动 */
function isDirty(): boolean {
  return dirtyRef.value;
}

/** 强制 Excalidraw 重新测量画布尺寸/偏移（外层动画结束后调用） */
function refresh() {
  try {
    excalidrawApiRef.value?.refresh?.();
  } catch {
    /* noop */
  }
}

/**
 * 重新测量 + 把内容居中到视口。
 * 弹窗动画结束后必须调一次，否则：
 *   1) 鼠标点击坐标与画布坐标错位（getBoundingClientRect 在 transform 动画期间测错）
 *   2) 内容不居中（scrollToContent 用错位的中心算）
 */
function centerView() {
  const api = excalidrawApiRef.value;
  if (!api) return;
  try {
    api.refresh?.();
    const elements = api.getSceneElements ? api.getSceneElements() : [];
    if (elements.length && api.scrollToContent) {
      api.scrollToContent(elements, {fitToContent: true, animate: false});
    }
  } catch {
    /* noop */
  }
}

defineExpose({
  getSceneJson,
  markClean,
  isDirty,
  remount,
  refresh,
  centerView
});
</script>

<template>
  <div class="excal-canvas-wrap">
    <div v-if="loading" class="excal-canvas-loading">加载画布中...</div>
    <div v-else-if="errorMsg" class="excal-canvas-error">画布加载失败：{{ errorMsg }}</div>
    <div ref="containerRef" class="excal-canvas-host"/>
  </div>
</template>

<style scoped>
.excal-canvas-wrap {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 300px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.excal-canvas-host {
  flex: 1;
  width: 100%;
  min-height: 0;
  position: relative;
}

/* Excalidraw 自带 .excalidraw 容器需要明确高度，否则 absolute 定位的 UI 会
 * 撑满整个视口（这就是"散开"的根因之一）。这里强制吃满宿主。 */
.excal-canvas-host :deep(.excalidraw) {
  position: absolute;
  inset: 0;
  height: 100%;
  width: 100%;
}

.excal-canvas-loading,
.excal-canvas-error {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  color: #64748b;
  background: rgba(255, 255, 255, 0.6);
  z-index: 1;
  pointer-events: none;
}

.excal-canvas-error {
  color: #dc2626;
}
</style>
