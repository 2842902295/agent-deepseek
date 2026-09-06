<script setup lang="ts">
/**
 * ExcalidrawDialog —— 通用 Excalidraw 编辑弹窗，可在任何页面使用。
 *
 * 两种保存模式（二选一）：
 *   A. 落库：传 :artifact-id（与 :source-url），保存时调 saveExcalidrawArtifact
 *      接口覆盖文件 + 重生成 SVG，触发 'saved' 事件。
 *   B. 托管：不传 artifact-id，监听 'save' 事件接收 sceneJson 字符串自行处理
 *      （比如做成临时画板、写入别的存储）。
 */
import {computed, ref, shallowRef, watch} from 'vue';
import {NButton, NModal, useDialog, useMessage} from 'naive-ui';
import {saveExcalidrawArtifact} from '@/service/api';
import ExcalidrawCanvas from './excalidraw-canvas.vue';

interface Props {
  show: boolean;
  /** 标题，默认"Excalidraw 编辑器" */
  title?: string;
  /** 远程 .excalidraw 文件 URL */
  sourceUrl?: string;
  /** 内存中的场景对象，与 sourceUrl 二选一 */
  initialScene?: Record<string, any> | null;
  /** 模式 A：落库 artifact ID；保存时走内置接口 */
  artifactId?: number | null;
  /** 只读模式 */
  readOnly?: boolean;
  /** 主题 */
  theme?: 'light' | 'dark';
  /** 自定义 z-index；嵌套在其他 Modal 内打开时需要传更高的值（默认 500） */
  zIndex?: number;
}

const props = withDefaults(defineProps<Props>(), {
  title: 'Excalidraw 编辑器',
  sourceUrl: '',
  initialScene: null,
  artifactId: null,
  readOnly: false,
  theme: 'light',
  zIndex: 500
});

const emit = defineEmits<{
  (e: 'update:show', show: boolean): void;
  /** 模式 A 保存成功后触发，payload 含后端返回数据（id/size/svg） */
  (e: 'saved', payload: {sceneJson: string; svgArtifactId: number | null}): void;
  /** 模式 B 由外部托管保存时触发；外部成功后请调 markCleanFromOutside() */
  (e: 'save', sceneJson: string): void;
}>();

const message = useMessage();
const dialog = useDialog();

const canvasRef = shallowRef<InstanceType<typeof ExcalidrawCanvas> | null>(null);
const dirty = ref(false);
const saving = ref(false);
const ready = ref(false);
const isFullscreen = ref(false);

// 每次打开都让 canvas key 变化，强制完全重挂——多次打开不会出现状态污染
const mountKey = ref(0);

watch(
  () => props.show,
  (v) => {
    if (v) {
      mountKey.value += 1;
      dirty.value = false;
      saving.value = false;
      ready.value = false;
    }
  }
);

// canvas 内部已用 waitForStable 等容器布局稳定后再 mount，
// 所以 ready 触发时容器一定在最终位置，直接居中即可。
watch(ready, (r) => {
  if (r) {
    requestAnimationFrame(() => {
      canvasRef.value?.centerView?.();
    });
  }
});

const showProxy = computed({
  get: () => props.show,
  set: (v) => emit('update:show', v)
});

// sourceUrl 直接用 props 传入的值；canvas 的 cache: 'no-store' 保证每次打开都拉最新文件
const versionedSourceUrl = computed(() => props.sourceUrl || '');

// NModal 用 style/contentStyle 控制尺寸最稳，避免 :deep 抢属性优先级问题
const modalStyle = computed(() =>
  isFullscreen.value
    ? {
        width: '100vw',
        maxWidth: '100vw',
        height: '100vh',
        display: 'flex',
        flexDirection: 'column' as const,
        borderRadius: '0',
        margin: '0',
        top: '0',
        left: '0',
        transform: 'none'
      }
    : {
        width: '96vw',
        maxWidth: '1600px',
        height: 'calc(100vh - 48px)',
        display: 'flex',
        flexDirection: 'column' as const
      }
);

const contentStyle = {
  padding: '0',
  flex: '1',
  minHeight: '0',
  display: 'flex',
  flexDirection: 'column' as const,
  overflow: 'hidden'
};

function onDirtyChange(v: boolean) {
  dirty.value = v;
}

function onLoadError(msg: string) {
  message.error(`画布加载失败：${msg}`);
}

async function doSave() {
  const canvas = canvasRef.value;
  if (!canvas) return;
  const json = await canvas.getSceneJson();
  if (!json) {
    message.error('画布尚未就绪');
    return;
  }

  saving.value = true;
  try {
    if (props.artifactId != null) {
      // 模式 A：内置接口
      const res: any = await saveExcalidrawArtifact(props.artifactId, json);
      if (res?.error) throw new Error(res.error.response?.data?.msg || res.error.message || '保存失败');
      message.success('已保存');
      canvas.markClean();
      emit('saved', {sceneJson: json, svgArtifactId: res?.data?.svg?.id ?? null});
    } else {
      // 模式 B：交给外部
      emit('save', json);
      // 外部应在成功后调 markCleanFromOutside()，这里不主动 markClean
    }
  } catch (err: any) {
    message.error(`保存失败：${err?.message || err}`);
  } finally {
    saving.value = false;
  }
}

function tryClose(): boolean {
  if (!dirty.value) {
    showProxy.value = false;
    return true;
  }
  dialog.warning({
    title: '有未保存的修改',
    content: '关闭后改动会丢失，确定关闭吗？',
    positiveText: '关闭',
    negativeText: '继续编辑',
    // 高过 Excalidraw 内部浮层（4000）和外层 NModal（zIndex 默认或调用方传入的值）
    zIndex: Math.max(props.zIndex, 4000) + 1000,
    onPositiveClick: () => {
      showProxy.value = false;
    }
  });
  // 返回 false 阻止 NModal 默认关闭，等用户在 dialog 里确认
  return false;
}

function onMaskClose() {
  // mask-closable 默认 false，这里仅在不 dirty 时允许关掉
  if (!dirty.value) showProxy.value = false;
}

function onReady() {
  ready.value = true;
}

/** 模式 B 外部保存完成后调用，重置 dirty */
function markCleanFromOutside() {
  canvasRef.value?.markClean();
}

defineExpose({markCleanFromOutside});
</script>

<template>
  <NModal
    v-model:show="showProxy"
    :mask-closable="false"
    :close-on-esc="!dirty"
    :closable="false"
    :z-index="zIndex"
    preset="card"
    :class="['excal-dialog', isFullscreen && 'excal-dialog-fullscreen']"
    :style="modalStyle"
    :content-style="contentStyle"
    :title="title"
    :on-close="tryClose"
    :segmented="{ content: 'soft' }"
    display-directive="if"
  >
    <template #header-extra>
      <div class="excal-dialog-header-extra">
        <span v-if="dirty" class="excal-dirty">未保存</span>
        <NButton
          size="small"
          type="primary"
          :loading="saving"
          :disabled="readOnly || !ready"
          @click="doSave"
        >
          保存
        </NButton>
        <NButton size="small" quaternary :title="isFullscreen ? '退出全屏' : '全屏'" @click="isFullscreen = !isFullscreen">
          <template #icon>
            <svg v-if="!isFullscreen" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" width="14" height="14">
              <path d="M2 6V2h4M10 2h4v4M14 10v4h-4M6 14H2v-4" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            <svg v-else viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" width="14" height="14">
              <path d="M6 2v4H2M10 2v4h4M2 10h4v4M14 10h-4v4" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
          </template>
        </NButton>
        <NButton size="small" quaternary @click="tryClose">关闭</NButton>
      </div>
    </template>

    <div class="excal-dialog-body">
      <ExcalidrawCanvas
        :key="mountKey"
        ref="canvasRef"
        :source-url="versionedSourceUrl"
        :initial-scene="initialScene"
        :read-only="readOnly"
        :theme="theme"
        @dirty-change="onDirtyChange"
        @load-error="onLoadError"
        @ready="onReady"
      />
    </div>
  </NModal>
</template>

<style scoped>
.excal-dialog-body {
  width: 100%;
  /* card content 已经被 contentStyle 锁成 flex:1+overflow:hidden，
   * 这里就吃满父级即可。绝不要再用 100vh 算高度——会和工具条/边距冲突。 */
  flex: 1;
  min-height: 0;
  display: flex;
}

.excal-dialog-header-extra {
  display: flex;
  align-items: center;
  gap: 8px;
}

.excal-dirty {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10.5px;
  font-weight: 700;
  letter-spacing: 0.16em;
  color: #d97706;
  margin-right: 4px;
}
</style>

<!--
  非 scoped 全局样式：处理「外层有 NModal 时 Excalidraw 内部 popover 被遮罩」的问题。

  根因：Excalidraw 0.18 内部的浮层（颜色面板、library、context menu、modal、
  tooltip 等）有些是直接 portal 到 document.body 的，它们 CSS 写死的 z-index
  在 100~1000 之间。如果外层 NModal 遮罩 z-index 是 2000，这些浮层就会被遮罩
  压在下面。

  修法：
    1) Excalidraw 的「在 .excalidraw 容器内部就近渲染」的浮层 → 通过 :deep
       提到一个高 z-index，让它们贴着 ExcalidrawDialog（z-index=zIndex prop）
       的 stacking context 走。
    2) Excalidraw 直接 portal 到 body 的浮层（.excalidraw-modal-container、
       .excalidraw .popover 之外的 .Island 弹窗等）→ 通过全局选择器把
       z-index 全部抬到 calc(var(--excal-z) + 100) 之上，确保高于外层 NModal
       遮罩（2000）。

  这套是 CSS 一次性兜底，不依赖运行时计算，也不影响其他 Modal 的层级。
-->
<style>
/* 让 ExcalidrawDialog 内部所有命中的 Excalidraw 浮层都吃这个高 z-index。
 * 4000 是经验值：高过 detail-modal 的 NModal 遮罩（默认 2000）+ 高过任何
 * 其它 NModal 嵌套（一般不会超过 3000）。 */
.excal-dialog .excalidraw,
.excal-dialog .excalidraw .Island,
.excal-dialog .excalidraw .popover,
.excal-dialog .excalidraw .Modal,
.excal-dialog .excalidraw .Modal__background,
.excal-dialog .excalidraw .Dialog,
.excal-dialog .excalidraw .Dialog__close,
.excal-dialog .excalidraw .picker,
.excal-dialog .excalidraw .ColorPicker,
.excal-dialog .excalidraw .layer-ui__wrapper,
.excal-dialog .excalidraw .App-menu_top,
.excal-dialog .excalidraw .context-menu {
  z-index: 4000 !important;
}

/* portal 到 body 的 Excalidraw 浮层（不在 .excal-dialog 子树里） */
body > .excalidraw-modal-container,
body > .excalidraw,
body > [class*="excalidraw-"][class*="-portal"],
body > .ColorPickerContainer,
body > .picker,
body > .context-menu {
  z-index: 4000 !important;
}

/* 全屏模式：覆盖 NModal 的居中 transform，铺满视口 */
.excal-dialog.excal-dialog-fullscreen {
  position: fixed !important;
  inset: 0 !important;
  width: 100vw !important;
  max-width: 100vw !important;
  height: 100vh !important;
  border-radius: 0 !important;
  margin: 0 !important;
  transform: none !important;
}
</style>
