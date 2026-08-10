<script setup lang="ts">
import {computed, defineAsyncComponent, nextTick, onBeforeUnmount, onMounted, reactive, ref, shallowRef, watch} from 'vue';
import type {Component} from 'vue';
import type {AgentArtifact} from '@/service/api';
import {getServiceBaseURL} from '@/utils/service';
import {extractExt, standardizeXlsxForPreview} from '@/utils/attachment';
import {NImage} from 'naive-ui';
import {marked} from 'marked';
import ChartRender from './chart-render.vue';
import HtmlRender from './html-render.vue';
import ExcalidrawDialog from '@/components/common/excalidraw-dialog.vue';
import ExcalidrawCanvas from '@/components/common/excalidraw-canvas.vue';

const props = defineProps<{ artifacts: AgentArtifact[]; inline?: boolean }>();

function prettySize(bytes: number | null | undefined): string {
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function fileExt(name: string): string {
  const ext = extractExt(name);
  return ext.length > 4 ? ext.slice(0, 4) : ext || '?';
}

function fileExtGroup(name: string): string {
  const ext = extractExt(name);
  if (ext === 'pdf') return 'pdf';
  if (['doc', 'docx', 'rtf', 'odt'].includes(ext)) return 'doc';
  if (['xls', 'xlsx', 'csv', 'tsv'].includes(ext)) return 'sheet';
  if (['ppt', 'pptx'].includes(ext)) return 'ppt';
  if (['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'bmp'].includes(ext)) return 'img';
  if (['zip', 'rar', '7z', 'tar', 'gz'].includes(ext)) return 'zip';
  if (['mp4', 'mov', 'avi', 'mkv', 'webm', 'mp3', 'wav', 'ogg'].includes(ext)) return 'media';
  if (['js', 'ts', 'py', 'java', 'go', 'rs', 'vue', 'jsx', 'tsx'].includes(ext)) return 'code';
  if (['txt', 'md', 'mdx', 'log'].includes(ext)) return 'text';
  return 'other';
}

function iconFor(type: string): string {
  const m: Record<string, string> = {
    md: '≡',
    pdf: '◨',
    zip: '⌘',
    xlsx: '▤',
    csv: '▤',
    json: '{·}',
    image: '◉',
    video: '▶',
    chart: '∿',
    excalidraw: '✎'
  };
  return m[type] || '◇';
}

// downloadUrl 是相对于 baseURL（如 /ai/agent/...），拼成完整 URL
function resolveUrl(url: string | null | undefined): string {
  if (!url) return '';
  if (url.startsWith('http')) return url;
  const isHttpProxy = import.meta.env.DEV && import.meta.env.VITE_HTTP_PROXY === 'Y';
  const {baseURL} = getServiceBaseURL(import.meta.env, isHttpProxy);
  return `${baseURL}${url.startsWith('/') ? url : '/' + url}`;
}

// 视频/音频走内联流式：让后端不下发 Content-Disposition: attachment，
// 浏览器才会发 Range 请求做边下边播 + 进度条拖动；
// 下载按钮保持默认 attachment 行为。
function inlineUrl(url: string | null | undefined): string {
  const u = resolveUrl(url);
  if (!u) return '';
  return `${u}${u.includes('?') ? '&' : '?'}inline=1`;
}

function fileStem(name: string): string {
  const i = name.lastIndexOf('.');
  return i > 0 ? name.slice(0, i) : name;
}

// 同 message 下与 excalidraw 配对的 SVG → 作为预览图
// 用 path（实际文件路径）匹配，而非 name（展示名称）
// 匹配规则：
// 1. standard-writing-flow.excalidraw + standard-writing-flow.svg
// 2. diagram.excalidraw + diagram.excalidraw.svg（容错）
function pairedSvg(excal: AgentArtifact): AgentArtifact | null {
  if (!excal.path) return null;

  const excalPath = excal.path.toLowerCase();
  const excalStem = fileStem(excal.path);

  return (
    props.artifacts.find(
      a =>
        a.id !== excal.id &&
        a.artifactType === 'image' &&
        a.path?.toLowerCase().endsWith('.svg') &&
        (fileStem(a.path || '') === excalStem || // standard-writing-flow.svg 匹配 standard-writing-flow.excalidraw
         a.path?.toLowerCase() === `${excalPath}.svg`) // diagram.excalidraw.svg 匹配 diagram.excalidraw
    ) || null
  );
}

// 已被 excalidraw 卡片"占用"的 SVG，不在普通文件列表中重复展示
const consumedSvgIds = computed(() => {
  const set = new Set<number>();
  for (const a of props.artifacts) {
    if (a.artifactType !== 'excalidraw') continue;
    const svg = pairedSvg(a);
    if (svg) set.add(svg.id);
  }
  return set;
});

const dialogShow = ref(false);
const dialogArtifact = ref<AgentArtifact | null>(null);

// inline excalidraw 画布 refs
const excalCanvasRefs = reactive<Record<number, any>>({});

function setExcalCanvasRef(artifactId: number, el: any) {
  if (el) {
    excalCanvasRefs[artifactId] = el;
  } else {
    delete excalCanvasRefs[artifactId];
  }
}

function onExcalReady(artifactId: number) {
  // ready 后自动居中并适配内容，加延迟确保 DOM 完全渲染
  setTimeout(() => {
    excalCanvasRefs[artifactId]?.centerView?.();
  }, 100);
}

function openEditor(a: AgentArtifact) {
  dialogArtifact.value = a;
  dialogShow.value = true;
}

// 保存后强制刷新 SVG 预览（cache-busting）
const svgVersion = ref<Record<number, number>>({});

function svgPreviewUrl(svg: AgentArtifact): string {
  const v = svgVersion.value[svg.id] || 0;
  const base = resolveUrl(svg.downloadUrl);
  if (!v) return base;
  return `${base}${base.includes('?') ? '&' : '?'}v=${v}`;
}

function onDialogSaved() {
  const a = dialogArtifact.value;
  if (!a) return;

  // 刷新配对的 SVG 预览图
  const svg = pairedSvg(a);
  if (svg) svgVersion.value[svg.id] = Date.now();

  // 刷新 inline excalidraw 画布
  if (excalCanvasRefs[a.id]) {
    excalCanvasRefs[a.id].remount?.();
  }
}

// ───── Markdown 预览 ─────────────────────────────────────────────────────
function isMarkdownArtifact(a: AgentArtifact): boolean {
  if (a.artifactType === 'md') return true;
  return ['md', 'markdown', 'mdx'].includes(extractExt(a.name || ''));
}

// ───── HTML 内联渲染 ──────────────────────────────────────────────────────
function isHtmlArtifact(a: AgentArtifact): boolean {
  if (a.artifactType === 'html') return true;
  return ['html', 'htm'].includes(extractExt(a.name || ''));
}

// inline HTML artifact 直接持有 htmlContent（fenced block 来源）
// 文件型 HTML artifact 需要先 fetch downloadUrl
const htmlCache = ref<Record<number, string>>({});
const htmlLoading = ref<Record<number, boolean>>({});

async function ensureHtmlContent(a: AgentArtifact) {
  if (htmlCache.value[a.id] !== undefined) return;
  if (!a.downloadUrl) return;
  htmlLoading.value[a.id] = true;
  try {
    const resp = await fetch(resolveUrl(a.downloadUrl), {credentials: 'omit'});
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    htmlCache.value[a.id] = await resp.text();
  } catch {
    htmlCache.value[a.id] = '<p style="color:#ef4444">加载失败</p>';
  } finally {
    htmlLoading.value[a.id] = false;
  }
}

// 自动 fetch 文件型 HTML artifact
watch(
  () => props.artifacts,
  (list) => {
    for (const a of list) {
      if (isHtmlArtifact(a) && !(a as any).htmlContent && a.downloadUrl) {
        ensureHtmlContent(a);
      }
    }
  },
  {immediate: true, deep: false}
);

const mdPreview = reactive<{
  visible: boolean;
  loading: boolean;
  error: string;
  name: string;
  raw: string;
  html: string;
  downloadUrl: string;
  copyState: 'idle' | 'done' | 'error';
}>({
  visible: false,
  loading: false,
  error: '',
  name: '',
  raw: '',
  html: '',
  downloadUrl: '',
  copyState: 'idle',
});

let mdCopyTimer: ReturnType<typeof setTimeout> | null = null;

async function openMarkdownPreview(a: AgentArtifact) {
  if (!a.downloadUrl) return;
  mdPreview.visible = true;
  mdPreview.loading = true;
  mdPreview.error = '';
  mdPreview.name = a.name;
  mdPreview.raw = '';
  mdPreview.html = '';
  mdPreview.downloadUrl = resolveUrl(a.downloadUrl);
  mdPreview.copyState = 'idle';
  try {
    const resp = await fetch(mdPreview.downloadUrl, {credentials: 'omit'});
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const text = await resp.text();
    mdPreview.raw = text;
    mdPreview.html = (await marked.parse(text, {breaks: true})) as string;
  } catch (e: any) {
    mdPreview.error = e?.message || '加载失败';
  } finally {
    mdPreview.loading = false;
  }
}

function closeMarkdownPreview() {
  mdPreview.visible = false;
}

async function copyMarkdownAll() {
  if (!mdPreview.raw) return;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(mdPreview.raw);
    } else {
      const ta = document.createElement('textarea');
      ta.value = mdPreview.raw;
      ta.style.position = 'fixed';
      ta.style.left = '-9999px';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
    }
    mdPreview.copyState = 'done';
  } catch {
    mdPreview.copyState = 'error';
  }
  if (mdCopyTimer) clearTimeout(mdCopyTimer);
  mdCopyTimer = setTimeout(() => (mdPreview.copyState = 'idle'), 1500);
}

// ───── Office / PDF 预览（vue-office）─────────────────────────────────────
type OfficeKind = 'docx' | 'xlsx' | 'pdf' | 'pptx';

function getOfficeKind(a: AgentArtifact): OfficeKind | null {
  const ext = extractExt(a.name || '');
  if (ext === 'docx') return 'docx';
  if (['xlsx', 'xls'].includes(ext)) return 'xlsx';
  if (ext === 'pdf') return 'pdf';
  if (ext === 'pptx') return 'pptx';
  // 退而求其次：用 artifactType 兜底（后端有时只给类型不给完整文件名）
  if (a.artifactType === 'pdf') return 'pdf';
  if (a.artifactType === 'xlsx') return 'xlsx';
  return null;
}

function isOfficeArtifact(a: AgentArtifact): boolean {
  return getOfficeKind(a) !== null;
}

// 按需懒加载 vue-office 组件（首屏不拉包体）
const VueOfficeDocx = defineAsyncComponent(async () => {
  await import('@vue-office/docx/lib/index.css');
  return import('@vue-office/docx');
});
const VueOfficeExcel = defineAsyncComponent(async () => {
  await import('@vue-office/excel/lib/index.css');
  return import('@vue-office/excel');
});
const VueOfficePdf = defineAsyncComponent(() => import('@vue-office/pdf'));
const VueOfficePptx = defineAsyncComponent(() => import('@vue-office/pptx'));

const filePreview = reactive<{
  visible: boolean;
  loading: boolean;
  error: string;
  name: string;
  kind: OfficeKind | null;
  downloadUrl: string;
  src: ArrayBuffer | null;
}>({
  visible: false,
  loading: false,
  error: '',
  name: '',
  kind: null,
  downloadUrl: '',
  src: null,
});

const officeComponent = shallowRef<Component | null>(null);

// ───── HTML 全屏预览 ──────────────────────────────────────────────────────
const htmlFullscreen = reactive<{
  visible: boolean;
  name: string;
  html: string;
  downloadUrl: string;
  isFullscreen: boolean;
  /** 展示模式：auto=自动检测（游戏/应用页缩放适配、普通页滚动）/ fit=缩放适配 / scroll=原尺寸滚动 */
  mode: 'auto' | 'fit' | 'scroll';
}>({
  visible: false,
  name: '',
  html: '',
  downloadUrl: '',
  isFullscreen: false,
  mode: 'auto',
});

// ── 缩放适配：弹窗随内容收缩，消除留白 ─────────────────────────────────
// HtmlRender 按「弹窗最大可用区」（htmlFsFitViewport）计算 scale 并回传缩放后尺寸，
// 弹窗收缩到正好包裹内容；scale 基准不随弹窗收缩变化，无反馈回环
const htmlFitSize = ref<{w: number; h: number} | null>(null);
const htmlFsHeadEl = ref<HTMLElement | null>(null);
const htmlFsHeaderH = ref(49); // mp-head 高度兜底（打开弹窗时实测覆盖）
const winW = ref(window.innerWidth);
const winH = ref(window.innerHeight);

function openHtmlFullscreen(a: AgentArtifact) {
  htmlFullscreen.visible = true;
  htmlFullscreen.name = a.name;
  htmlFullscreen.downloadUrl = resolveUrl(a.downloadUrl || '');
  htmlFullscreen.html = (a as any).htmlContent || htmlCache.value[a.id] || '';
  htmlFullscreen.mode = 'auto';
  htmlFitSize.value = null;
  // 实测头栏高度：弹窗收缩包裹内容时精确计算总高
  nextTick(() => {
    if (htmlFsHeadEl.value) htmlFsHeaderH.value = htmlFsHeadEl.value.offsetHeight;
  });
}

function onWinResize() {
  winW.value = window.innerWidth;
  winH.value = window.innerHeight;
}

onMounted(() => window.addEventListener('resize', onWinResize));
onBeforeUnmount(() => window.removeEventListener('resize', onWinResize));

// 弹窗 CSS 契约：宽 min(1200px, vw-48)、高 vh-80、边框 1px；body 内区 = 总区 - 边框 - 头栏
// 原生全屏：弹窗铺满视口且无边框（html-fs-modal--native-fs），可用区 = 整个视口减头栏
const htmlFsFitViewport = computed(() => {
  if (htmlFullscreen.isFullscreen) {
    return {w: winW.value, h: winH.value - htmlFsHeaderH.value};
  }
  return {
    w: Math.min(1200, winW.value - 48) - 2,
    h: winH.value - 80 - htmlFsHeaderH.value - 2,
  };
});

function onHtmlFitSize(w: number, h: number) {
  // (0, 0) = 退出缩放适配，恢复默认大窗
  htmlFitSize.value = w > 0 && h > 0 ? {w, h} : null;
}

// 弹窗收缩尺寸：内容 + 边框；头栏按钮组有最小容身宽度，更窄的内容居中显示（少量边距）
const HTML_FS_MIN_W = 480;
const htmlFsModalStyle = computed(() => {
  if (!htmlFitSize.value || htmlFullscreen.isFullscreen) return undefined;
  return {
    width: `${Math.max(htmlFitSize.value.w + 2, HTML_FS_MIN_W)}px`,
    height: `${htmlFitSize.value.h + htmlFsHeaderH.value + 2}px`,
  };
});

// 切换展示模式时清除旧尺寸：scroll 模式不上报即恢复默认大窗；fit/auto 会重新上报
watch(() => htmlFullscreen.mode, () => {
  htmlFitSize.value = null;
});

function closeHtmlFullscreen() {
  htmlFullscreen.visible = false;
  htmlFullscreen.isFullscreen = false;
}

function toggleHtmlFullscreen() {
  htmlFullscreen.isFullscreen = !htmlFullscreen.isFullscreen;
}

async function openFilePreview(a: AgentArtifact) {
  const kind = getOfficeKind(a);
  if (!kind || !a.downloadUrl) return;
  filePreview.visible = true;
  filePreview.loading = true;
  filePreview.error = '';
  filePreview.name = a.name;
  filePreview.kind = kind;
  filePreview.downloadUrl = resolveUrl(a.downloadUrl);
  filePreview.src = null;
  officeComponent.value =
    kind === 'docx' ? VueOfficeDocx
    : kind === 'xlsx' ? VueOfficeExcel
    : kind === 'pdf' ? VueOfficePdf
    : VueOfficePptx;
  try {
    const resp = await fetch(filePreview.downloadUrl, {credentials: 'omit'});
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const buf = await resp.arrayBuffer();
    // ClosedXML 式 xlsx（inlineStr + x: 命名空间前缀 → vue-office 解析空白）经 SheetJS 重写为标准格式；常规文件原样直出
    filePreview.src = extractExt(filePreview.name) === 'xlsx' ? await standardizeXlsxForPreview(buf) : buf;
  } catch (e: any) {
    filePreview.error = e?.message || '加载失败';
  } finally {
    filePreview.loading = false;
  }
}

function closeFilePreview() {
  filePreview.visible = false;
  filePreview.src = null;
  officeComponent.value = null;
}
</script>

<template>
  <div v-if="artifacts.length" class="artifact-list" :class="{ 'artifact-list--inline': inline }">
    <template v-for="a in artifacts" :key="a.id">
      <!-- Chart -->
      <div v-if="a.artifactType === 'chart' && a.chartSpec" class="artifact-chart">
        <div v-if="!inline" class="af-head">
          <span class="af-icon">{{ iconFor(a.artifactType) }}</span>
          <span class="af-tag">CHART</span>
          <span class="af-name">{{ a.name }}</span>
        </div>
        <ChartRender :spec="a.chartSpec as any" :no-title="inline"/>
        <div v-if="inline" class="ai-figcaption">
          <span class="ai-figcaption-text" :title="a.description || a.name">{{ a.description || a.name }}</span>
        </div>
      </div>

      <!-- HTML 内联渲染 -->
      <div v-if="isHtmlArtifact(a) && inline" class="artifact-html">
        <button type="button" class="html-fs-btn" :title="a.name" @click="openHtmlFullscreen(a)">⛶</button>
        <!-- fenced block 来源：htmlContent 直接可用 -->
        <template v-if="(a as any).htmlContent">
          <HtmlRender :html="(a as any).htmlContent"/>
        </template>
        <!-- 文件来源：需要 fetch -->
        <template v-else>
          <div v-if="htmlLoading[a.id] || htmlCache[a.id] === undefined" class="als-html-body">
            <div class="als-page">
              <div class="als-bone als-nav"/>
              <div class="als-grid">
                <div class="als-col">
                  <div class="als-bone als-line lf"/>
                  <div class="als-bone als-line l8"/>
                  <div class="als-bone als-line lf"/>
                  <div class="als-bone als-line l6"/>
                </div>
                <div class="als-bone als-side"/>
              </div>
              <div class="als-bone als-foot"/>
            </div>
          </div>
          <HtmlRender v-else :html="htmlCache[a.id]"/>
        </template>
        <div class="ai-figcaption">
          <span class="ai-figcaption-text" :title="a.description || a.name">{{ a.description || a.name }}</span>
          <a v-if="a.downloadUrl" :href="resolveUrl(a.downloadUrl)" :download="a.name" target="_blank" rel="noopener" class="ai-figcaption-link">↓ 下载</a>
        </div>
      </div>

      <!-- Excalidraw：SVG 预览 + 打开弹窗编辑 -->
      <div v-else-if="a.artifactType === 'excalidraw'" class="artifact-excal">
        <div v-if="!inline" class="af-head">
          <span class="af-icon">{{ iconFor(a.artifactType) }}</span>
          <span class="af-tag">EXCALIDRAW</span>
          <span class="af-name">{{ a.name }}</span>
          <div class="af-head-actions">
            <button type="button" class="af-btn" @click="openEditor(a)">编辑</button>
            <a
              :href="resolveUrl(a.downloadUrl)"
              :download="a.name"
              target="_blank"
              rel="noopener"
              class="af-btn af-btn-link"
            >下载 .excalidraw</a>
            <a
              v-if="pairedSvg(a)"
              :href="resolveUrl(pairedSvg(a)!.downloadUrl)"
              :download="pairedSvg(a)!.name"
              target="_blank"
              rel="noopener"
              class="af-btn af-btn-link"
            >下载 .svg</a>
          </div>
        </div>

        <!-- 非 inline 模式：显示 SVG 预览 -->
        <template v-if="!inline">
          <a
            v-if="pairedSvg(a)"
            :href="resolveUrl(pairedSvg(a)!.downloadUrl)"
            target="_blank"
            rel="noopener"
            class="af-svg-preview"
            @click.prevent="openEditor(a)"
          >
            <img :src="svgPreviewUrl(pairedSvg(a)!)" :alt="a.name" loading="lazy"/>
          </a>
          <div v-else class="af-no-preview">未生成 SVG 预览</div>
          <div v-if="a.description" class="af-desc">{{ a.description }}</div>
        </template>

        <!-- inline 模式：直接渲染 excalidraw 画布 -->
        <template v-else>
          <div class="af-excal-inline" @click="openEditor(a)">
            <ExcalidrawCanvas
              :ref="(el: any) => setExcalCanvasRef(a.id, el)"
              :source-url="resolveUrl(a.downloadUrl)"
              :read-only="true"
              theme="light"
              class="af-excal-canvas"
              @ready="onExcalReady(a.id)"
            />
          </div>
          <div class="ai-figcaption">
            <span class="ai-figcaption-text" :title="a.description || a.name">{{ a.description || a.name }}</span>
          </div>
        </template>
      </div>

      <!-- Video：原生 <video> 播放 + 下载按钮 -->
      <div
        v-else-if="a.artifactType === 'video' && a.downloadUrl"
        class="artifact-video"
      >
        <video
          :src="inlineUrl(a.downloadUrl)"
          class="artifact-video-player"
          controls
          preload="metadata"
          playsinline
        />
        <div v-if="!inline" class="av-caption">
          <div class="av-caption-text">
            <span class="av-name">{{ a.name }}</span>
            <span v-if="a.description" class="av-desc">{{ a.description }}</span>
            <span v-if="a.size" class="av-size">{{ prettySize(a.size) }}</span>
          </div>
          <a
            :href="resolveUrl(a.downloadUrl)"
            :download="a.name"
            class="av-download"
            target="_blank"
            rel="noopener"
            @click.stop
          >下载 ↓</a>
        </div>
        <div v-if="inline" class="ai-figcaption ai-figcaption--video">
          <span class="ai-figcaption-text" :title="a.description || a.name">{{ a.description || a.name }}</span>
          <a
            :href="resolveUrl(a.downloadUrl)"
            :download="a.name"
            class="ai-figcaption-link"
            target="_blank"
            rel="noopener"
            @click.stop
          >↓ 下载</a>
        </div>
      </div>

      <!-- Image：点击弹窗放大，单独的下载按钮 -->
      <div
        v-else-if="a.artifactType === 'image' && a.downloadUrl && !consumedSvgIds.has(a.id)"
        class="artifact-image"
      >
        <NImage
          :src="resolveUrl(a.downloadUrl)"
          :alt="a.name"
          object-fit="contain"
          class="artifact-image-inner"
          :img-props="{ loading: 'lazy', style: 'max-height:480px;max-width:100%;display:block;margin:0 auto;' }"
        />
        <div v-if="!inline" class="ai-caption">
          <div class="ai-caption-text">
            <span class="ai-name">{{ a.name }}</span>
            <span v-if="a.description" class="ai-desc">{{ a.description }}</span>
          </div>
          <a
            :href="resolveUrl(a.downloadUrl)"
            :download="a.name"
            class="ai-download"
            target="_blank"
            rel="noopener"
            @click.stop
          >下载 ↓</a>
        </div>
        <div v-if="inline" class="ai-figcaption">
          <span class="ai-figcaption-text" :title="a.description || a.name">{{ a.description || a.name }}</span>
        </div>
      </div>

      <!-- Office / PDF：点击弹窗预览（vue-office） -->
      <template v-else-if="isOfficeArtifact(a) && a.downloadUrl">
        <button v-if="!inline" type="button" class="artifact-file artifact-md" @click="openFilePreview(a)">
          <span class="af-icon">{{ iconFor(a.artifactType) }}</span>
          <div class="af-body">
            <div class="af-row">
              <span class="af-name">{{ a.name }}</span>
              <span class="af-type">{{ (getOfficeKind(a) || a.artifactType).toUpperCase() }}</span>
            </div>
            <div class="af-meta">
              <span v-if="a.description">{{ a.description }}</span>
              <span v-if="a.size" class="af-size">{{ prettySize(a.size) }}</span>
            </div>
          </div>
          <span class="af-action">预览 ↗</span>
        </button>
        <button v-else type="button" class="artifact-file-inline" @click="openFilePreview(a)">
          <span :class="'afi-ext-' + fileExtGroup(a.name)" class="afi-ext">{{ fileExt(a.name) }}</span>
          <span class="afi-name" :title="a.name">{{ a.name }}</span>
          <span v-if="a.description" class="afi-desc" :title="a.description">{{ a.description }}</span>
          <span class="afi-spacer"/>
          <span v-if="a.size" class="afi-size">{{ prettySize(a.size) }}</span>
          <span class="afi-action">↗ 预览</span>
        </button>
      </template>

      <!-- Markdown：点击弹窗预览 + 复制全文 -->
      <template v-else-if="isMarkdownArtifact(a) && a.downloadUrl">
        <button v-if="!inline" type="button" class="artifact-file artifact-md" @click="openMarkdownPreview(a)">
          <span class="af-icon">{{ iconFor('md') }}</span>
          <div class="af-body">
            <div class="af-row">
              <span class="af-name">{{ a.name }}</span>
              <span class="af-type">MD</span>
            </div>
            <div class="af-meta">
              <span v-if="a.description">{{ a.description }}</span>
              <span v-if="a.size" class="af-size">{{ prettySize(a.size) }}</span>
            </div>
          </div>
          <span class="af-action">预览 ↗</span>
        </button>
        <button v-else type="button" class="artifact-file-inline" @click="openMarkdownPreview(a)">
          <span class="afi-ext afi-ext-text">md</span>
          <span class="afi-name" :title="a.name">{{ a.name }}</span>
          <span v-if="a.description" class="afi-desc" :title="a.description">{{ a.description }}</span>
          <span class="afi-spacer"/>
          <span v-if="a.size" class="afi-size">{{ prettySize(a.size) }}</span>
          <span class="afi-action">↗ 预览</span>
        </button>
      </template>

      <!-- File -->
      <template v-else-if="!consumedSvgIds.has(a.id) && a.artifactType !== 'chart'">
        <a
          v-if="!inline"
          :href="resolveUrl(a.downloadUrl || '')"
          :download="a.name"
          target="_blank"
          rel="noopener"
          class="artifact-file"
        >
          <span class="af-icon">{{ iconFor(a.artifactType) }}</span>
          <div class="af-body">
            <div class="af-row">
              <span class="af-name">{{ a.name }}</span>
              <span class="af-type">{{ a.artifactType.toUpperCase() }}</span>
            </div>
            <div class="af-meta">
              <span v-if="a.description">{{ a.description }}</span>
              <span v-if="a.size" class="af-size">{{ prettySize(a.size) }}</span>
            </div>
          </div>
          <span v-if="a.downloadUrl" class="af-action">下载 ↓</span>
        </a>
        <a
          v-else
          :href="resolveUrl(a.downloadUrl || '')"
          :download="a.name"
          target="_blank"
          rel="noopener"
          class="artifact-file-inline"
        >
          <span :class="'afi-ext-' + fileExtGroup(a.name)" class="afi-ext">{{ fileExt(a.name) }}</span>
          <span class="afi-name" :title="a.name">{{ a.name }}</span>
          <span v-if="a.description" class="afi-desc" :title="a.description">{{ a.description }}</span>
          <span class="afi-spacer"/>
          <span v-if="a.size" class="afi-size">{{ prettySize(a.size) }}</span>
          <span v-if="a.downloadUrl" class="afi-action">↓ 下载</span>
        </a>
      </template>
    </template>

    <ExcalidrawDialog
      v-if="dialogArtifact"
      v-model:show="dialogShow"
      :artifact-id="dialogArtifact.id"
      :source-url="resolveUrl(dialogArtifact.downloadUrl)"
      :title="dialogArtifact.name"
      @saved="onDialogSaved"
    />

    <!-- Markdown 预览弹窗 -->
    <Teleport to="body">
      <div v-if="mdPreview.visible" class="md-preview-mask" @click.self="closeMarkdownPreview">
        <div class="md-preview-modal">
          <header class="mp-head">
            <span class="mp-tag">MD·PREVIEW</span>
            <span :title="mdPreview.name" class="mp-name">{{ mdPreview.name }}</span>
            <span class="mp-line"/>
            <button
              type="button"
              class="mp-act"
              :class="{ 'is-done': mdPreview.copyState === 'done', 'is-error': mdPreview.copyState === 'error' }"
              :disabled="!mdPreview.raw || mdPreview.loading"
              title="复制全文"
              @click="copyMarkdownAll"
            >
              <span class="mp-act-icon">⧉</span>
              <span>{{ mdPreview.copyState === 'done' ? '已复制' : (mdPreview.copyState === 'error' ? '失败' : '复制全文') }}</span>
            </button>
            <a
              v-if="mdPreview.downloadUrl"
              :href="mdPreview.downloadUrl"
              :download="mdPreview.name"
              target="_blank"
              rel="noopener"
              class="mp-act"
              title="下载源文件"
            >
              <span class="mp-act-icon">↧</span>
              <span>下载</span>
            </a>
            <button class="mp-close" @click="closeMarkdownPreview">×</button>
          </header>

          <div class="mp-body">
            <div v-if="mdPreview.loading" class="mp-state">载入中…</div>
            <div v-else-if="mdPreview.error" class="mp-state mp-state-err">加载失败：{{ mdPreview.error }}</div>
            <!-- eslint-disable-next-line vue/no-v-html -->
            <div v-else class="mp-content" v-html="mdPreview.html"/>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Office / PDF 预览弹窗（vue-office） -->
    <Teleport to="body">
      <div v-if="filePreview.visible" class="md-preview-mask file-preview-mask" @click.self="closeFilePreview">
        <div class="md-preview-modal file-preview-modal">
          <header class="mp-head">
            <span class="mp-tag">{{ (filePreview.kind || 'FILE').toUpperCase() }}·PREVIEW</span>
            <span :title="filePreview.name" class="mp-name">{{ filePreview.name }}</span>
            <span class="mp-line"/>
            <a
              v-if="filePreview.downloadUrl"
              :href="filePreview.downloadUrl"
              :download="filePreview.name"
              target="_blank"
              rel="noopener"
              class="mp-act"
              title="下载源文件"
            >
              <span class="mp-act-icon">↧</span>
              <span>下载</span>
            </a>
            <button class="mp-close" @click="closeFilePreview">×</button>
          </header>

          <div class="mp-body file-preview-body">
            <div v-if="filePreview.loading" class="mp-state">载入中…</div>
            <div v-else-if="filePreview.error" class="mp-state mp-state-err">加载失败：{{ filePreview.error }}</div>
            <component
              :is="officeComponent"
              v-else-if="filePreview.src && officeComponent"
              :src="filePreview.src"
              :options="filePreview.kind === 'xlsx' && extractExt(filePreview.name) === 'xls' ? { xls: true } : undefined"
              class="vo-viewer"
            />
          </div>
        </div>
      </div>
    </Teleport>

    <!-- HTML 全屏预览弹窗 -->
    <Teleport to="body">
      <div v-if="htmlFullscreen.visible" class="md-preview-mask html-fs-mask" @click.self="closeHtmlFullscreen">
        <div class="md-preview-modal html-fs-modal" :class="{'html-fs-modal--native-fs': htmlFullscreen.isFullscreen}" :style="htmlFsModalStyle">
          <header ref="htmlFsHeadEl" class="mp-head">
            <span class="mp-tag">HTML·PREVIEW</span>
            <span :title="htmlFullscreen.name" class="mp-name">{{ htmlFullscreen.name }}</span>
            <span class="mp-line"/>
            <div class="mp-seg" title="展示模式：自动检测 / 缩放适配（游戏类页面完整显示）/ 原尺寸滚动">
              <button type="button" class="mp-seg-btn" :class="{active: htmlFullscreen.mode === 'auto'}" @click="htmlFullscreen.mode = 'auto'">自动</button>
              <button type="button" class="mp-seg-btn" :class="{active: htmlFullscreen.mode === 'fit'}" title="等比缩放页面适配窗口，游戏/应用类页面不裁切" @click="htmlFullscreen.mode = 'fit'">缩放适配</button>
              <button type="button" class="mp-seg-btn" :class="{active: htmlFullscreen.mode === 'scroll'}" title="原尺寸渲染，窗口内滚动浏览" @click="htmlFullscreen.mode = 'scroll'">原尺寸</button>
            </div>
            <a
              v-if="htmlFullscreen.downloadUrl"
              :href="htmlFullscreen.downloadUrl"
              :download="htmlFullscreen.name"
              target="_blank"
              rel="noopener"
              class="mp-act"
              title="下载源文件"
            >
              <span class="mp-act-icon">↧</span>
              <span>下载</span>
            </a>
            <button class="mp-act" :title="htmlFullscreen.isFullscreen ? '退出全屏' : '全屏'" @click="toggleHtmlFullscreen">
              <span class="mp-act-icon">{{ htmlFullscreen.isFullscreen ? '⛶' : '⛶' }}</span>
              <span>{{ htmlFullscreen.isFullscreen ? '退出全屏' : '全屏' }}</span>
            </button>
            <button class="mp-close" @click="closeHtmlFullscreen">×</button>
          </header>
          <!--
            展示模式交给 HtmlRender：auto 自动检测（游戏/应用页等比缩放适配、普通长页原尺寸滚动），也可头部手动切换；
            缩放适配时弹窗随内容收缩包裹（fit-viewport 给定 scale 基准、fit-size 回传展示尺寸），无留白
          -->
          <div class="html-fs-body">
            <HtmlRender
              :html="htmlFullscreen.html"
              :fullscreen="true"
              scrollable
              :mode="htmlFullscreen.mode"
              :fit-viewport="htmlFsFitViewport"
              :fit-fill="htmlFullscreen.isFullscreen"
              @fit-size="onHtmlFitSize"
            />
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.artifact-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 12px;
}

.artifact-chart {
  border: 1px solid rgba(30, 64, 175, 0.12);
  border-left: 2px solid var(--accent, #1e40af);
  background: var(--surface, rgba(255, 255, 255, 0.42));
  backdrop-filter: blur(40px) saturate(200%);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.95), inset 0 0 0 1px rgba(255, 255, 255, 0.4), 0 4px 14px -2px rgba(30, 64, 175, 0.08);
  padding: 12px 14px;
  border-radius: 10px;
}

.artifact-chart .af-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  padding-bottom: 8px;
  border-bottom: 1px solid rgba(30, 64, 175, 0.1);
}

.artifact-excal {
  border: 1px solid rgba(30, 64, 175, 0.12);
  border-left: 2px solid var(--accent, #1e40af);
  background: var(--surface, rgba(255, 255, 255, 0.42));
  backdrop-filter: blur(40px) saturate(200%);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.95), inset 0 0 0 1px rgba(255, 255, 255, 0.4), 0 4px 14px -2px rgba(30, 64, 175, 0.08);
  border-radius: 10px;
}

.artifact-excal .af-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border-bottom: 1px solid rgba(30, 64, 175, 0.1);
}

.artifact-excal .af-head-actions {
  margin-left: auto;
  display: flex;
  gap: 6px;
}

.artifact-excal .af-btn {
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: 11px;
  font-weight: 600;
  color: var(--accent, #1e40af);
  background: rgba(30, 64, 175, 0.08);
  border: 1px solid transparent;
  padding: 4px 12px;
  border-radius: 11px;
  text-decoration: none;
  cursor: pointer;
  transition: background 0.15s;
}

.artifact-excal .af-btn:hover {
  background: rgba(30, 64, 175, 0.12);
}

.artifact-excal .af-btn-link {
  background: transparent;
  color: #475569;
}

.artifact-excal .af-btn-link:hover {
  color: var(--accent, #1e40af);
  background: rgba(30, 64, 175, 0.06);
}

.artifact-excal .af-svg-preview {
  display: block;
  padding: 12px;
  text-align: center;
}

.artifact-excal .af-svg-preview img {
  max-width: 100%;
  max-height: 480px;
  height: auto;
  margin: 0 auto;
}

.artifact-excal .af-no-preview {
  padding: 24px;
  text-align: center;
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: 11px;
  color: #94a3b8;
}

.artifact-excal .af-desc {
  padding: 8px 14px;
  border-top: 1px solid rgba(30, 64, 175, 0.1);
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: 11px;
  color: #64748b;
}

.artifact-file {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 10px 14px;
  border: 1px solid rgba(30, 64, 175, 0.12);
  border-left: 2px solid #0891b2;
  background: var(--surface, rgba(255, 255, 255, 0.42));
  backdrop-filter: blur(40px) saturate(200%);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.95), inset 0 0 0 1px rgba(255, 255, 255, 0.4), 0 4px 14px -2px rgba(30, 64, 175, 0.08);
  cursor: pointer;
  border-radius: 10px;
  text-decoration: none;
  color: inherit;
  transition: border-color 0.15s, background 0.15s;
}

.artifact-image {
  display: block;
  border: 1px solid rgba(30, 64, 175, 0.12);
  border-left: 2px solid var(--accent, #1e40af);
  background: var(--surface, rgba(255, 255, 255, 0.42));
  backdrop-filter: blur(40px) saturate(200%);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.95), inset 0 0 0 1px rgba(255, 255, 255, 0.4), 0 4px 14px -2px rgba(30, 64, 175, 0.08);
  padding: 8px;
  border-radius: 10px;
  text-decoration: none;
  color: inherit;
  max-width: 100%;
}

.artifact-video {
  display: block;
  border: 1px solid rgba(30, 64, 175, 0.12);
  border-left: 2px solid var(--accent, #1e40af);
  background: #0f172a;
  padding: 0;
  border-radius: 10px;
  box-shadow: 0 4px 14px -2px rgba(30, 64, 175, 0.15);
  color: inherit;
  max-width: 100%;
  overflow: hidden;
}

.artifact-video-player {
  display: block;
  width: 100%;
  max-height: 540px;
  background: #000;
  outline: none;
}

.artifact-video .av-caption {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 14px;
  background: var(--surface, rgba(255, 255, 255, 0.42));
  backdrop-filter: blur(40px) saturate(200%);
  border-top: 1px solid rgba(30, 64, 175, 0.1);
}

.artifact-video .av-caption-text {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
  flex: 1;
}

.artifact-video .av-name {
  font-family: var(--font-body, 'Plus Jakarta Sans', system-ui, sans-serif);
  font-size: 13px;
  font-weight: 500;
  color: #0f172a;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.artifact-video .av-desc {
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: 10.5px;
  color: #64748b;
  letter-spacing: 0.04em;
  word-break: break-word;
}

.artifact-video .av-size {
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: 10px;
  color: #94a3b8;
  letter-spacing: 0.04em;
}

.artifact-video .av-download {
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: 11px;
  font-weight: 600;
  color: var(--accent, #1e40af);
  text-decoration: none;
  flex-shrink: 0;
  padding: 2px 8px;
  border: 1px solid rgba(30, 64, 175, 0.15);
  border-radius: 10px;
  align-self: center;
  transition: border-color 0.15s, background 0.15s;
}

.artifact-video .av-download:hover {
  border-color: var(--accent, #1e40af);
  background: rgba(30, 64, 175, 0.08);
}

.artifact-image-inner {
  display: block;
  max-width: 100%;
}

.artifact-image :deep(.n-image img) {
  cursor: zoom-in;
  border-radius: 10px;
}

.artifact-image .ai-caption {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid rgba(30, 64, 175, 0.1);
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.artifact-image .ai-caption-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}

.artifact-image .ai-name {
  font-family: var(--font-body, 'Plus Jakarta Sans', system-ui, sans-serif);
  font-size: 13px;
  font-weight: 500;
  color: #0f172a;
}

.artifact-image .ai-desc {
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: 10.5px;
  color: #64748b;
  letter-spacing: 0.04em;
}

.artifact-image .ai-download {
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: 11px;
  font-weight: 600;
  color: var(--accent, #1e40af);
  text-decoration: none;
  flex-shrink: 0;
  padding: 2px 8px;
  border: 1px solid rgba(30, 64, 175, 0.15);
  border-radius: 10px;
  transition: border-color 0.15s, background 0.15s;
}

.artifact-image .ai-download:hover {
  border-color: var(--accent, #1e40af);
  background: rgba(30, 64, 175, 0.08);
}

.artifact-file:hover {
  border-color: var(--accent, #1e40af);
  background: rgba(30, 64, 175, 0.06);
}

.af-icon {
  font-family: var(--font-body, 'Plus Jakarta Sans', system-ui, sans-serif);
  font-size: 22px;
  color: #1e40af;
  min-width: 28px;
  text-align: center;
}

.af-tag {
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: 10px;
  font-weight: 700;
  color: #64748b;
}

.af-body {
  flex: 1;
  min-width: 0;
}

.af-row {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.af-name {
  font-family: var(--font-body, 'Plus Jakarta Sans', system-ui, sans-serif);
  font-size: 14px;
  font-weight: 500;
  color: #0f172a;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.af-type {
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: 10px;
  font-weight: 600;
  color: var(--accent, #1e40af);
  background: rgba(30, 64, 175, 0.08);
  padding: 2px 8px;
  border-radius: 10px;
}

.af-meta {
  display: flex;
  gap: 10px;
  margin-top: 4px;
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: 10.5px;
  color: #64748b;
  letter-spacing: 0.04em;
}

.af-size {
  color: #94a3b8;
}

.af-action {
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: 11px;
  font-weight: 600;
  color: var(--accent, #1e40af);
  flex-shrink: 0;
}

.artifact-md {
  appearance: none;
  font-family: inherit;
  text-align: left;
  width: 100%;
  border-left-color: #1e40af;
}

.artifact-md:hover {
  border-color: var(--accent, #1e40af);
  background: rgba(30, 64, 175, 0.06);
}

/* ── inline 模式：图注式，融入正文 ───────────────────────────────── */
.artifact-list--inline {
  margin-top: 4px;
  margin-bottom: 4px;
  gap: 12px;
}

/* 去掉所有卡片边框和背景 */
.artifact-list--inline .artifact-chart,
.artifact-list--inline .artifact-excal,
.artifact-list--inline .artifact-image,
.artifact-list--inline .artifact-video {
  background: transparent;
  border: none;
  border-radius: 0;
  padding: 0;
}

/* inline 模式 excalidraw SVG 预览 */
.af-svg-preview-inline {
  display: block;
  text-align: center;
  cursor: pointer;
  transition: opacity 0.15s;
}

.af-svg-preview-inline:hover {
  opacity: 0.92;
}

.af-svg-preview-inline img {
  max-width: 100%;
  max-height: 480px;
  height: auto;
  margin: 0 auto;
}

.af-no-preview-inline {
  padding: 18px;
  text-align: center;
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: 11px;
  color: #cbd5e1;
  background: #f8fafc;
  border: 1px solid rgba(30, 64, 175, 0.1);
  border-radius: 10px;
}

/* inline 模式 excalidraw 画布 */
.af-excal-inline {
  width: 100%;
  height: 500px;
  border: 1px solid rgba(30, 64, 175, 0.12);
  border-radius: 12px;
  overflow: hidden;
  background: rgba(255, 255, 255, 0.42);
  backdrop-filter: blur(20px);
  cursor: pointer;
  transition: border-color 0.15s;
  position: relative;
}

.af-excal-inline:hover {
  border-color: #1e40af;
}

.af-excal-inline::after {
  content: '点击编辑';
  position: absolute;
  top: 12px;
  right: 12px;
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: 11px;
  color: rgba(30, 64, 175, 0.6);
  background: rgba(255, 255, 255, 0.7);
  backdrop-filter: blur(20px);
  padding: 4px 10px;
  border-radius: 10px;
  opacity: 0;
  transition: opacity 0.15s;
  pointer-events: none;
  backdrop-filter: blur(4px);
  border: 1px solid rgba(30, 64, 175, 0.2);
  z-index: 10;
}

.af-excal-inline:hover::after {
  opacity: 1;
}

/* 覆盖层：拦截点击但允许滚轮穿透 */
.af-excal-inline::before {
  content: '';
  position: absolute;
  inset: 0;
  z-index: 5;
  background: transparent;
}

.af-excal-canvas {
  width: 100%;
  height: 100%;
  pointer-events: none;
}

/* 隐藏 inline 模式下的 Excalidraw 工具栏 */
.af-excal-inline :deep(.App-toolbar),
.af-excal-inline :deep(.App-bottom-bar) {
  display: none !important;
}

.artifact-list--inline .artifact-video {
  background: #0f172a;
}

/* 图注：居中，描述 + 下载紧排 */
.ai-figcaption {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  margin-top: 7px;
  padding-top: 6px;
  border-top: 1px solid rgba(30, 64, 175, 0.08);
}

.ai-figcaption-text {
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: 11px;
  color: #94a3b8;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 320px;
}

.ai-figcaption-link {
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: 11px;
  color: #94a3b8;
  text-decoration: none;
  flex-shrink: 0;
  background: none;
  border: none;
  cursor: pointer;
  padding: 0;
  transition: color 0.15s;
}

.ai-figcaption-link:hover {
  color: #334155;
}

.ai-figcaption--video {
  background: #0f172a;
  border-top-color: rgba(255, 255, 255, 0.08);
  padding: 6px 10px 8px;
  margin-top: 0;
}

.ai-figcaption--video .ai-figcaption-text,
.ai-figcaption--video .ai-figcaption-link {
  color: rgba(148, 163, 184, 0.7);
}

.ai-figcaption--video .ai-figcaption-link:hover {
  color: #94a3b8;
}

/* 文件类 inline：参照 q-att-file 样式，但更融入正文 */
.artifact-file-inline {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px 4px 5px;
  background: transparent;
  border: 1px solid rgba(30, 64, 175, 0.12);
  border-radius: 10px;
  font-size: 12px;
  text-decoration: none;
  color: inherit;
  cursor: pointer;
  font-family: inherit;
  appearance: none;
  width: 100%;
  min-width: 0;
  transition: border-color 0.15s;
}

.artifact-file-inline:hover {
  border-color: var(--accent, #1e40af);
}

/* ext 标签 */
.afi-ext {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 28px;
  height: 18px;
  padding: 0 4px;
  border-radius: 10px;
  font-size: 9px;
  font-weight: 700;
  color: #fff;
  background: #94a3b8;
  flex-shrink: 0;
}

.afi-ext-pdf  { background: #dc2626; }
.afi-ext-doc  { background: #2563eb; }
.afi-ext-sheet { background: #16a34a; }
.afi-ext-ppt  { background: #ea580c; }
.afi-ext-img  { background: #7c3aed; }
.afi-ext-zip  { background: #854d0e; }
.afi-ext-media { background: #0891b2; }
.afi-ext-code { background: #475569; }
.afi-ext-text { background: #64748b; }
.afi-ext-other { background: #94a3b8; }

.afi-name {
  font-size: 12px;
  color: var(--ink-2, #334155);
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
  flex-shrink: 1;
}

.afi-desc {
  font-size: 11px;
  color: var(--ink-4, #94a3b8);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}

.afi-spacer {
  flex: 1;
}

.afi-size {
  color: var(--ink-4, #94a3b8);
  font-size: 10px;
  flex-shrink: 0;
}

.afi-action {
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.06em;
  color: var(--ink-3, #64748b);
  flex-shrink: 0;
}

.artifact-file-inline:hover .afi-action {
  color: var(--accent, #1e40af);
}

/* HTML artifact */
.artifact-html {
  border: 1px solid rgba(30, 64, 175, 0.12);
  border-radius: 14px;
  overflow: hidden;
  background: var(--surface, rgba(255, 255, 255, 0.42));
  backdrop-filter: blur(40px) saturate(200%);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.95), 0 4px 14px -2px rgba(30, 64, 175, 0.08);
  position: relative;
}

.artifact-html .html-fs-btn {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(255, 255, 255, 0.7);
  backdrop-filter: blur(20px);
  border: 1px solid rgba(30, 64, 175, 0.12);
  border-radius: 12px;
  font-size: 18px;
  color: #64748b;
  cursor: pointer;
  z-index: 10;
  opacity: 0;
  transition: opacity 0.2s, color 0.15s, border-color 0.15s, background 0.15s;
  backdrop-filter: blur(4px);
}

.artifact-html:hover .html-fs-btn {
  opacity: 1;
}

.artifact-html .html-fs-btn:hover {
  color: var(--accent, #1e40af);
  border-color: var(--accent, #1e40af);
  background: rgba(255, 255, 255, 0.9);
}

.artifact-html .af-head {
  border-bottom: 1px solid var(--rule, #e2e8f0);
}

.artifact-list--inline .artifact-html {
  border: 1px solid rgba(30, 64, 175, 0.12);
  border-radius: 14px;
  margin: 2px 0;
}
</style>

<!-- 不加 scoped：Teleport 出去的弹窗能命中样式 -->
<style>
.md-preview-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.35);
  backdrop-filter: blur(8px) saturate(120%);
  -webkit-backdrop-filter: blur(8px) saturate(120%);
  z-index: 2200;
  display: flex;
  align-items: center;
  justify-content: center;
  animation: md-preview-rise 0.22s ease-out;
}

@keyframes md-preview-rise {
  from { opacity: 0; transform: translateY(12px) scale(0.97); }
  to   { opacity: 1; transform: translateY(0) scale(1); }
}

.md-preview-modal {
  width: min(880px, calc(100vw - 48px));
  max-height: calc(100vh - 80px);
  background: var(--surface-strong, rgba(255, 255, 255, 0.62));
  backdrop-filter: blur(40px) saturate(200%);
  -webkit-backdrop-filter: blur(40px) saturate(200%);
  border: 1px solid var(--border-strong, rgba(30, 64, 175, 0.18));
  border-radius: 18px;
  box-shadow:
    inset 0 1px 0 var(--highlight, rgba(255, 255, 255, 0.95)),
    inset 0 0 0 1px rgba(255, 255, 255, 0.4),
    0 24px 60px -16px var(--border-glow, rgba(30, 64, 175, 0.25));
  display: flex;
  flex-direction: column;
}

/* Office / PDF 预览弹窗：更宽更高，body 区交给 vue-office 自己滚动 */
.file-preview-modal {
  width: min(1200px, calc(100vw - 48px));
  height: calc(100vh - 80px);
  max-height: calc(100vh - 80px);
}

.file-preview-modal .file-preview-body {
  padding: 0;
  background: var(--paper, #f5f7fb);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  flex: 1;
  box-shadow: inset 0 2px 6px rgba(30, 64, 175, 0.04);
}

.file-preview-modal .vo-viewer {
  flex: 1;
  width: 100%;
  height: 100%;
  min-height: 0;
  overflow: auto;
  background: var(--paper, #f5f7fb);
}

.file-preview-modal .vue-office-excel-content {
  background: #ffffff;
}

.file-preview-modal .vue-office-pdf {
  background: var(--paper, #f5f7fb);
}

.md-preview-modal .mp-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--rule, rgba(30, 64, 175, 0.1));
  background: rgba(255, 255, 255, 0.18);
  flex-shrink: 0;
}

.md-preview-modal .mp-tag {
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: 11px;
  font-weight: 700;
  color: var(--accent, #1e40af);
  background: var(--accent-soft, rgba(30, 64, 175, 0.08));
  padding: 2px 8px;
  border-radius: 6px;
}

.md-preview-modal .mp-name {
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: 12px;
  color: var(--ink-2, #334155);
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.md-preview-modal .mp-line {
  flex: 1;
  height: 1px;
  background: linear-gradient(to right, transparent, rgba(30, 64, 175, 0.12), transparent);
}

/* 展示模式分段开关（HTML 预览：自动 / 缩放适配 / 原尺寸），同 mp-act 设计语言 */
.md-preview-modal .mp-seg {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 2px;
  background: var(--surface, rgba(255, 255, 255, 0.42));
  border: 1px solid var(--border, rgba(30, 64, 175, 0.1));
  border-radius: 11px;
  flex-shrink: 0;
}

.md-preview-modal .mp-seg-btn {
  appearance: none;
  border: none;
  background: transparent;
  padding: 2px 9px;
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: 11px;
  color: var(--ink-3, #64748b);
  border-radius: 9px;
  cursor: pointer;
  white-space: nowrap;
  transition: color 0.15s, background 0.15s;
}

.md-preview-modal .mp-seg-btn:hover {
  color: var(--accent, #1e40af);
}

.md-preview-modal .mp-seg-btn.active {
  color: var(--accent, #1e40af);
  background: rgba(30, 64, 175, 0.08);
  font-weight: 600;
}

.md-preview-modal .mp-act {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: 11px;
  color: var(--ink-2, #334155);
  background: var(--surface, rgba(255, 255, 255, 0.42));
  border: 1px solid var(--border, rgba(30, 64, 175, 0.1));
  border-radius: 11px;
  cursor: pointer;
  text-decoration: none;
  transition: color 0.15s, border-color 0.15s, background 0.15s, box-shadow 0.15s;
}

.md-preview-modal .mp-act:hover:not(:disabled) {
  color: var(--accent, #1e40af);
  border-color: var(--accent, #1e40af);
  background: rgba(255, 255, 255, 0.72);
  box-shadow: 0 2px 8px -2px rgba(30, 64, 175, 0.18);
}

.md-preview-modal .mp-act:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.md-preview-modal .mp-act.is-done {
  color: #16a34a;
  border-color: #16a34a;
}

.md-preview-modal .mp-act.is-error {
  color: #dc2626;
  border-color: #dc2626;
}

.md-preview-modal .mp-act-icon {
  font-size: 12px;
  line-height: 1;
}

.md-preview-modal .mp-close {
  background: none;
  border: none;
  font-size: 22px;
  line-height: 1;
  color: var(--ink-3, #64748b);
  cursor: pointer;
  margin-left: 2px;
  width: 30px;
  height: 30px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  transition: background 0.15s, color 0.15s;
}

.md-preview-modal .mp-close:hover {
  color: var(--ink, #0f172a);
  background: rgba(30, 64, 175, 0.06);
}

.md-preview-modal .mp-body {
  flex: 1;
  overflow-y: auto;
  padding: 18px 26px 26px;
  background: rgba(255, 255, 255, 0.22);
}

.md-preview-modal .mp-state {
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: 12px;
  color: var(--ink-4, #94a3b8);
  text-align: center;
  padding: 60px 0;
}

.md-preview-modal .mp-state-err {
  color: #b91c1c;
}

/* Markdown rendering inside preview */
.md-preview-modal .mp-content {
  font-family: var(--font-body, 'Plus Jakarta Sans', system-ui, sans-serif);
  font-size: 14.5px;
  line-height: 1.85;
  color: var(--ink, #0f172a);
}

.md-preview-modal .mp-content h1,
.md-preview-modal .mp-content h2,
.md-preview-modal .mp-content h3,
.md-preview-modal .mp-content h4 {
  font-family: var(--font-body, 'Plus Jakarta Sans', system-ui, sans-serif);
  font-weight: 500;
  margin: 22px 0 10px;
  color: var(--ink, #0f172a);
  letter-spacing: -0.015em;
  line-height: 1.3;
}

.md-preview-modal .mp-content h1 { font-size: 24px; font-weight: 400; }
.md-preview-modal .mp-content h2 { font-size: 19px; }
.md-preview-modal .mp-content h3 { font-size: 16px; }
.md-preview-modal .mp-content h4 {
  font-size: 13px;
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  color: #475569;
  font-weight: 600;
}

.md-preview-modal .mp-content p { margin: 10px 0; }
.md-preview-modal .mp-content strong { color: var(--ink, #0f172a); font-weight: 600; }
.md-preview-modal .mp-content em { font-style: italic; color: var(--accent, #1e40af); }
.md-preview-modal .mp-content ul,
.md-preview-modal .mp-content ol { padding-left: 24px; margin: 12px 0; }
.md-preview-modal .mp-content li { margin: 5px 0; }
.md-preview-modal .mp-content li::marker { color: var(--accent, #1e40af); font-weight: 600; }
.md-preview-modal .mp-content a { color: var(--accent, #1e40af); text-decoration: underline; }
.md-preview-modal .mp-content blockquote {
  border-left: 3px solid #1e40af;
  background: rgba(30, 64, 175, 0.06);
  margin: 12px 0;
  padding: 8px 14px;
  color: #334155;
  border-radius: 10px;
  font-style: italic;
}

.md-preview-modal .mp-content code {
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: 12.5px;
  background: rgba(255, 255, 255, 0.5);
  color: #0f4c5c;
  padding: 1px 6px;
  border-radius: 8px;
  border: 1px solid rgba(30, 64, 175, 0.1);
}

.md-preview-modal .mp-content pre {
  background: #0f172a;
  color: #e2e8f0;
  padding: 16px 20px;
  border-radius: 12px;
  margin: 14px 0;
  border: 1px solid #1e293b;
  max-width: 100%;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
}

.md-preview-modal .mp-content pre code {
  background: transparent;
  border: none;
  color: inherit;
  padding: 0;
  font-size: 12.5px;
  line-height: 1.7;
  display: block;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
}

.md-preview-modal .mp-content table {
  width: 100%;
  border-collapse: collapse;
  margin: 16px 0;
  font-size: 13px;
  border-top: 2px solid rgba(30, 64, 175, 0.15);
  border-bottom: 2px solid rgba(30, 64, 175, 0.15);
}

.md-preview-modal .mp-content thead { background: rgba(30, 64, 175, 0.04); }

.md-preview-modal .mp-content th {
  padding: 10px 14px;
  text-align: left;
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: 11px;
  font-weight: 600;
  color: #0f172a;
  border-bottom: 1px solid rgba(30, 64, 175, 0.15);
}

.md-preview-modal .mp-content td {
  padding: 10px 14px;
  border-bottom: 1px solid rgba(30, 64, 175, 0.1);
  color: #334155;
  vertical-align: top;
}

.md-preview-modal .mp-content img {
  max-width: 100%;
  height: auto;
  display: block;
  margin: 12px 0;
}

/* 代码块 + 复制按钮（marked renderer 注入的结构） */
.md-preview-modal .mp-content .code-block {
  position: relative;
  margin: 14px 0;
  max-width: 100%;
  min-width: 0;
}

.md-preview-modal .mp-content .code-block > pre { margin: 0; max-width: 100%; }

.md-preview-modal .mp-content .code-block-lang {
  position: absolute;
  top: 8px;
  left: 14px;
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: 11px;
  color: rgba(226, 232, 240, 0.45);
  pointer-events: none;
  z-index: 1;
}

.md-preview-modal .mp-content .code-copy-btn {
  position: absolute;
  top: 6px;
  right: 8px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: 11px;
  line-height: 1;
  color: rgba(226, 232, 240, 0.7);
  background: rgba(15, 23, 42, 0.6);
  border: 1px solid rgba(226, 232, 240, 0.18);
  border-radius: 11px;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s, color 0.15s, border-color 0.15s, background 0.15s;
  z-index: 2;
}

.md-preview-modal .mp-content .code-block:hover .code-copy-btn,
.md-preview-modal .mp-content .code-copy-btn:focus-visible { opacity: 1; }

.md-preview-modal .mp-content .code-copy-btn:hover {
  color: #fff;
  border-color: rgba(226, 232, 240, 0.4);
  background: rgba(15, 23, 42, 0.85);
}

.md-preview-modal .mp-content .code-copy-btn.is-done {
  opacity: 1;
  color: #4ade80;
  border-color: rgba(74, 222, 128, 0.45);
  border-radius: 11px;
}

.md-preview-modal .mp-content .code-copy-btn.is-error {
  opacity: 1;
  color: #f87171;
  border-color: rgba(248, 113, 113, 0.45);
}

.md-preview-modal .mp-content .ccb-icon { font-size: 12px; line-height: 1; }

/* HTML 全屏预览弹窗 */
.html-fs-modal {
  width: min(1200px, calc(100vw - 48px));
  height: calc(100vh - 80px);
  max-height: calc(100vh - 80px);
  /* 缩放适配时弹窗随内容收缩包裹，平滑过渡（遮罩 flex 居中，收缩过程保持居中） */
  transition: width 0.18s ease, height 0.18s ease;
}

.html-fs-modal--native-fs {
  width: 100vw;
  height: 100vh;
  max-height: 100vh;
  border-radius: 0;
  border: none;
  box-shadow: none;
}

.html-fs-body {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  padding: 0;
  background: var(--paper, #f5f7fb);
  position: relative;
  box-shadow: inset 0 2px 6px rgba(30, 64, 175, 0.04);
}

.html-fs-body .html-render {
  display: block;
  width: 100%;
  border-radius: 0;
}

/* 滚动条在 iframe 视口层时（外层文档），补与内层注入一致的细条样式 */
.html-fs-body .html-render::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}
.html-fs-body .html-render::-webkit-scrollbar-track {
  background: transparent;
}
.html-fs-body .html-render::-webkit-scrollbar-thumb {
  background: rgba(30, 64, 175, 0.24);
  border-radius: 3px;
}
.html-fs-body .html-render::-webkit-scrollbar-thumb:hover {
  background: rgba(30, 64, 175, 0.45);
}
</style>
