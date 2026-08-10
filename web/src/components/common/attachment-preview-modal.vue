<script setup lang="ts">
import {computed, onBeforeUnmount, reactive, watch} from 'vue';
import {marked} from 'marked';
import {downloadBlob, downloadText, extractExt, getOfficeKind, isCsvFile, isHtmlFile, isImageFile, isMarkdownFile, isOfficePreviewable, isVideoFile, sanitizeFilename, standardizeXlsxForPreview, type OfficeKind} from '@/utils/attachment';
import {VueOfficeDocx, VueOfficeExcel, VueOfficePdf, VueOfficePptx} from '@/components/common/office-viewers';
import HtmlRender from '@/views/ai/qa-glass/components/html-render.vue';

/**
 * 附件统一预览弹层（qa-glass 对话页 / 工作流画板共享）。
 * 按文件名扩展自动分流：markdown 渲染 / Office（vue-office）/ 视频播放 / 图片放大 / HTML 页面（HtmlRender）。
 * Teleport 到 body：调用方即便处于 transform 上下文（如工作流悬浮对话窗）内，弹层也能全屏展开。
 * 用法：`<AttachmentPreviewModal :att="previewAtt" @close="previewAtt = null" />`，att = {name, src}（src 为完整 URL）。
 */
type Attachment = {name: string; src: string};

const props = defineProps<{att: Attachment | null}>();
const emit = defineEmits<{(e: 'close'): void}>();

const kind = computed<'markdown' | 'office' | 'csv' | 'video' | 'image' | 'html' | 'other'>(() => {
  const name = props.att?.name || '';
  if (!name) return 'other';
  if (isMarkdownFile(name)) return 'markdown';
  if (isOfficePreviewable(name)) return 'office';
  if (isCsvFile(name)) return 'csv';
  if (isHtmlFile(name)) return 'html';
  if (isVideoFile(name)) return 'video';
  if (isImageFile(name)) return 'image';
  return 'other';
});

const officeKind = computed<OfficeKind | null>(() => (props.att ? getOfficeKind(props.att.name) : null));

const tagLabel = computed(() => {
  if (kind.value === 'markdown') return 'MD·PREVIEW';
  if (kind.value === 'office') return `${(officeKind.value || 'FILE').toUpperCase()}·PREVIEW`;
  if (kind.value === 'csv') return 'CSV·PREVIEW';
  if (kind.value === 'html') return 'HTML·PREVIEW';
  if (kind.value === 'video') return 'VIDEO·PREVIEW';
  if (kind.value === 'image') return 'IMAGE·PREVIEW';
  return 'FILE';
});

// ── markdown 渲染 ────────────────────────────────────────────────────────
const md = reactive<{loading: boolean; error: string; raw: string; html: string; copyState: 'idle' | 'done' | 'error'}>({
  loading: false,
  error: '',
  raw: '',
  html: '',
  copyState: 'idle'
});
let mdCopyTimer: ReturnType<typeof setTimeout> | null = null;

async function copyMarkdownAll() {
  if (!md.raw) return;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(md.raw);
    } else {
      const ta = document.createElement('textarea');
      ta.value = md.raw;
      ta.style.position = 'fixed';
      ta.style.left = '-9999px';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
    }
    md.copyState = 'done';
  } catch {
    md.copyState = 'error';
  }
  if (mdCopyTimer) clearTimeout(mdCopyTimer);
  mdCopyTimer = setTimeout(() => {
    md.copyState = 'idle';
  }, 1500);
}

function downloadMarkdown() {
  if (!md.raw) return;
  const base = sanitizeFilename((props.att?.name || '').replace(/\.(md|markdown|mdx)$/i, '')) || 'attachment';
  downloadText(base.endsWith('.md') ? base : `${base}.md`, md.raw, 'text/markdown;charset=utf-8');
}

// ── Office 文档（vue-office）────────────────────────────────────────────────
const OFFICE_MIME: Record<OfficeKind, string> = {
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  pdf: 'application/pdf',
  pptx: 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
};
const office = reactive<{loading: boolean; error: string; src: ArrayBuffer | null}>({
  loading: false,
  error: '',
  src: null
});

function downloadOffice() {
  const buf = office.src;
  if (!buf || !officeKind.value || !props.att) return;
  downloadBlob(props.att.name, new Blob([buf], {type: OFFICE_MIME[officeKind.value]}));
}

// ── CSV / TSV（纯文本表格）────────────────────────────────────────────────
const csv = reactive<{loading: boolean; error: string; rows: string[][]}>({
  loading: false,
  error: '',
  rows: []
});

// ── HTML 页面（HtmlRender 渲染，同 qa-glass）───────────────────────────────
const page = reactive<{loading: boolean; error: string; html: string}>({
  loading: false,
  error: '',
  html: ''
});

/** 简易 CSV 解析：支持双引号包裹字段（含转义 ""）、\r\n / \n 换行；tsv 用 \t 分隔 */
function parseDelimited(text: string, delimiter: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = '';
  let inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        field += ch;
      }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === delimiter) {
      row.push(field);
      field = '';
    } else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && text[i + 1] === '\n') i++;
      row.push(field);
      field = '';
      rows.push(row);
      row = [];
    } else {
      field += ch;
    }
  }
  // 最后一个字段 / 行（文件末尾无换行时）
  if (field || row.length) {
    row.push(field);
    rows.push(row);
  }
  return rows.filter(r => r.some(c => c.trim() !== ''));
}

function downloadCsv() {
  if (!csv.rows.length || !props.att) return;
  const text = csv.rows.map(r => r.map(c => (/[",\n]/.test(c) ? `"${c.replace(/"/g, '""')}"` : c)).join(',')).join('\n');
  downloadText(props.att.name, text, 'text/csv;charset=utf-8');
}

// ── 打开 / 关闭 ───────────────────────────────────────────────────────────
function close() {
  emit('close');
}

// att 变化即按类型加载内容（att=null 时重置状态）；并发切换时丢弃过期响应
watch(
  () => props.att,
  async att => {
    md.loading = false;
    md.error = '';
    md.raw = '';
    md.html = '';
    md.copyState = 'idle';
    office.loading = false;
    office.error = '';
    office.src = null;
    csv.loading = false;
    csv.error = '';
    csv.rows = [];
    page.loading = false;
    page.error = '';
    page.html = '';
    if (!att) return;
    if (isMarkdownFile(att.name)) {
      md.loading = true;
      try {
        const resp = await fetch(att.src, {credentials: 'include'});
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const text = await resp.text();
        if (props.att !== att) return;
        md.raw = text;
        md.html = (await marked.parse(text)) as string;
      } catch (e: any) {
        if (props.att === att) md.error = e?.message || '加载失败';
      } finally {
        if (props.att === att) md.loading = false;
      }
    } else if (isCsvFile(att.name)) {
      csv.loading = true;
      try {
        const resp = await fetch(att.src, {credentials: 'include'});
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const text = await resp.text();
        if (props.att !== att) return;
        const delimiter = extractExt(att.name) === 'tsv' ? '\t' : ',';
        csv.rows = parseDelimited(text, delimiter);
      } catch (e: any) {
        if (props.att === att) csv.error = e?.message || '加载失败';
      } finally {
        if (props.att === att) csv.loading = false;
      }
    } else if (isHtmlFile(att.name)) {
      page.loading = true;
      try {
        const resp = await fetch(att.src, {credentials: 'include'});
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const text = await resp.text();
        if (props.att !== att) return;
        page.html = text;
      } catch (e: any) {
        if (props.att === att) page.error = e?.message || '加载失败';
      } finally {
        if (props.att === att) page.loading = false;
      }
    } else if (isOfficePreviewable(att.name)) {
      office.loading = true;
      try {
        const resp = await fetch(att.src, {credentials: 'include'});
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const buf = await resp.arrayBuffer();
        if (props.att !== att) return;
        // ClosedXML 式 xlsx（inlineStr + x: 命名空间前缀 → vue-office 解析空白）经 SheetJS 重写为标准格式；常规文件原样直出
        office.src = extractExt(att.name) === 'xlsx' ? await standardizeXlsxForPreview(buf) : buf;
        if (props.att !== att) return;
      } catch (e: any) {
        if (props.att === att) office.error = e?.message || '加载失败';
      } finally {
        if (props.att === att) office.loading = false;
      }
    }
  },
  {immediate: true}
);

// Esc 关闭
function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') close();
}
watch(
  () => props.att,
  att => {
    if (att) document.addEventListener('keydown', onKeydown);
    else document.removeEventListener('keydown', onKeydown);
  },
  {immediate: true}
);

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeydown);
  if (mdCopyTimer) clearTimeout(mdCopyTimer);
});
</script>

<template>
  <Teleport to="body">
    <div v-if="att" class="apm-mask" @click.self="close">
      <!-- 图片：全屏放大（暗幕 + 大图 + 文件名 + 下载） -->
      <template v-if="kind === 'image'">
        <img :src="att.src" :alt="att.name" class="apm-img" @click.stop />
        <span class="apm-img-name">
          {{ att.name }}
          <a class="apm-img-dl" :href="att.src" :download="att.name" title="下载" @click.stop>↧</a>
        </span>
        <button type="button" class="apm-img-close" title="关闭 (Esc)" @click="close">✕</button>
      </template>

      <!-- markdown / Office / 视频 / 兜底：带标题栏的模态卡 -->
      <div v-else class="apm-modal" :class="`apm-${kind}`" @click.stop>
        <header class="apm-head">
          <span class="apm-tag">{{ tagLabel }}</span>
          <span class="apm-name" :title="att.name">{{ att.name }}</span>
          <span class="apm-line" />
          <template v-if="kind === 'markdown'">
            <button
              type="button"
              class="apm-act"
              :class="{'is-done': md.copyState === 'done', 'is-error': md.copyState === 'error'}"
              :disabled="!md.raw || md.loading"
              title="复制全文"
              @click="copyMarkdownAll"
            >
              <span class="apm-act-icon">⧉</span>
              <span>{{ md.copyState === 'done' ? '已复制' : md.copyState === 'error' ? '失败' : '复制全文' }}</span>
            </button>
            <button type="button" class="apm-act" :disabled="!md.raw || md.loading" title="下载" @click="downloadMarkdown">
              <span class="apm-act-icon">↧</span>
              <span>下载</span>
            </button>
          </template>
          <button v-else-if="kind === 'office' && office.src" type="button" class="apm-act" title="下载原文件" @click="downloadOffice">
            <span class="apm-act-icon">↧</span>
            <span>下载</span>
          </button>
          <button v-else-if="kind === 'csv' && csv.rows.length" type="button" class="apm-act" title="下载" @click="downloadCsv">
            <span class="apm-act-icon">↧</span>
            <span>下载</span>
          </button>
          <button v-else-if="kind === 'html'" type="button" class="apm-act" title="下载">
            <a class="apm-act-link" :href="att.src" :download="att.name"><span class="apm-act-icon">↧</span> 下载</a>
          </button>
          <button v-else-if="kind === 'other'" type="button" class="apm-act" title="下载" @click="close">
            <a class="apm-act-link" :href="att.src" :download="att.name"><span class="apm-act-icon">↧</span> 下载</a>
          </button>
          <button class="apm-close" title="关闭 (Esc)" @click="close">×</button>
        </header>

        <div class="apm-body" :class="{'apm-body-office': kind === 'office', 'apm-body-video': kind === 'video', 'apm-body-csv': kind === 'csv'}">
          <!-- markdown -->
          <template v-if="kind === 'markdown'">
            <div v-if="md.loading" class="apm-state">载入中…</div>
            <div v-else-if="md.error" class="apm-state apm-state-err">加载失败：{{ md.error }}</div>
            <!-- eslint-disable-next-line vue/no-v-html -->
            <div v-else class="apm-md" v-html="md.html" />
          </template>

          <!-- CSV / TSV 表格 -->
          <template v-else-if="kind === 'csv'">
            <div v-if="csv.loading" class="apm-state">载入中…</div>
            <div v-else-if="csv.error" class="apm-state apm-state-err">加载失败：{{ csv.error }}</div>
            <div v-else-if="csv.rows.length" class="apm-csv-wrap">
              <table class="apm-csv">
                <thead>
                  <tr>
                    <th v-for="(cell, ci) in csv.rows[0]" :key="ci">{{ cell }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(row, ri) in csv.rows.slice(1)" :key="ri">
                    <td v-for="(cell, ci) in row" :key="ci">{{ cell }}</td>
                  </tr>
                </tbody>
              </table>
              <div class="apm-csv-meta">{{ csv.rows.length - 1 }} 行 × {{ csv.rows[0]?.length || 0 }} 列</div>
            </div>
            <div v-else class="apm-state">文件为空</div>
          </template>

          <!-- HTML 页面（HtmlRender，同 qa-glass） -->
          <template v-else-if="kind === 'html'">
            <div v-if="page.loading" class="apm-state">载入中…</div>
            <div v-else-if="page.error" class="apm-state apm-state-err">加载失败：{{ page.error }}</div>
            <HtmlRender v-else-if="page.html" :html="page.html" />
          </template>

          <!-- Office / PDF -->
          <template v-else-if="kind === 'office'">
            <div v-if="office.loading" class="apm-state">载入中…</div>
            <div v-else-if="office.error" class="apm-state apm-state-err">加载失败：{{ office.error }}</div>
            <template v-else-if="office.src">
              <!--
                ignoreFonts: true —— 关键修复（掉字/闪烁）
                docx 内嵌字体（fontTable 的 embedFontRefs）会被 docx-preview 作为异步 task 加载，
                首屏先用系统字体渲染（正常），随后 @font-face 注入并 refreshTabStops 重排（闪烁）；
                而内嵌字体多为子集，缺少的字形（如个别汉字）在切换后显示为空白 → 掉字。
                ignoreFonts 跳过内嵌字体加载，统一走系统字体，文本完整且不再闪烁。
              -->
              <VueOfficeDocx v-if="officeKind === 'docx'" :src="office.src" :options="{ignoreFonts: true}" class="apm-viewer" />
              <VueOfficeExcel v-else-if="officeKind === 'xlsx'" :src="office.src" :options="{xls: extractExt(att.name) === 'xls'}" class="apm-viewer" />
              <VueOfficePdf v-else-if="officeKind === 'pdf'" :src="office.src" class="apm-viewer" />
              <VueOfficePptx v-else-if="officeKind === 'pptx'" :src="office.src" class="apm-viewer" />
            </template>
          </template>

          <!-- 视频 -->
          <template v-else-if="kind === 'video'">
            <video :src="att.src" class="apm-player" controls autoplay controlslist="nodownload" />
          </template>

          <!-- 兜底：不支持预览的类型给下载 -->
          <template v-else>
            <div class="apm-state">
              该类型暂不支持预览，请
              <a class="apm-fallback-dl" :href="att.src" :download="att.name">下载查看</a>
            </div>
          </template>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
/* ── 遮罩与模态卡（移植自 qa-glass，色值字面量自包含） ── */
.apm-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.42);
  z-index: 230;
  display: flex;
  align-items: center;
  justify-content: center;
  animation: apm-rise 0.22s ease-out;
  font-family: 'Plus Jakarta Sans', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', system-ui, sans-serif;
}
@keyframes apm-rise {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.apm-modal {
  width: min(880px, calc(100vw - 48px));
  max-height: calc(100vh - 80px);
  background: rgba(255, 255, 255, 0.98);
  backdrop-filter: blur(40px) saturate(200%);
  -webkit-backdrop-filter: blur(40px) saturate(200%);
  border: 1px solid rgba(30, 64, 175, 0.12);
  border-radius: 18px;
  box-shadow: 0 24px 64px -20px rgba(30, 64, 175, 0.28);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.apm-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border-bottom: 1px solid rgba(30, 64, 175, 0.1);
  background: #eaf0f9;
  flex-shrink: 0;
}
.apm-tag {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.06em;
  color: #1e40af;
}
.apm-name {
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  color: #334155;
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.apm-line {
  flex: 1;
  height: 1px;
  background: linear-gradient(to right, transparent, rgba(30, 64, 175, 0.1), transparent);
}
.apm-act {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  font-family: inherit;
  font-size: 11px;
  letter-spacing: 0.005em;
  font-weight: 600;
  color: #334155;
  background: rgba(255, 255, 255, 0.62);
  border: 1px solid rgba(30, 64, 175, 0.1);
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.18s;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.95), inset 0 0 0 1px rgba(255, 255, 255, 0.4);
}
.apm-act:hover:not(:disabled) {
  color: #1e40af;
  border-color: #1e40af;
}
.apm-act:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.apm-act.is-done {
  color: #16a34a;
  border-color: #16a34a;
}
.apm-act.is-error {
  color: #dc2626;
  border-color: #dc2626;
}
.apm-act-icon {
  font-size: 12px;
  line-height: 1;
}
.apm-act-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: inherit;
  text-decoration: none;
}
.apm-close {
  background: none;
  border: none;
  font-size: 22px;
  line-height: 1;
  color: #64748b;
  cursor: pointer;
  margin-left: 2px;
}
.apm-close:hover {
  color: #0f172a;
}

.apm-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 18px 24px 24px;
  background: #f5f7fb;
}
.apm-state {
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  color: #64748b;
  text-align: center;
  padding: 60px 0;
}
.apm-state-err {
  color: #b91c1c;
}
.apm-fallback-dl {
  color: #1e40af;
  font-weight: 600;
}

/* ── markdown 排版（自有简洁样式，QA 蓝系） ── */
.apm-md {
  font-size: 14.5px;
  line-height: 1.85;
  color: #0f172a;
  word-break: break-word;
}
.apm-md :deep(h1),
.apm-md :deep(h2),
.apm-md :deep(h3),
.apm-md :deep(h4) {
  margin: 20px 0 10px;
  font-weight: 700;
  line-height: 1.4;
  color: #0f172a;
}
.apm-md :deep(h1) {
  font-size: 22px;
  padding-bottom: 8px;
  border-bottom: 1px solid rgba(30, 64, 175, 0.12);
}
.apm-md :deep(h2) {
  font-size: 18px;
  padding-bottom: 6px;
  border-bottom: 1px solid rgba(30, 64, 175, 0.08);
}
.apm-md :deep(h3) {
  font-size: 16px;
}
.apm-md :deep(h4) {
  font-size: 15px;
}
.apm-md :deep(p) {
  margin: 0 0 12px;
}
.apm-md :deep(strong) {
  font-weight: 700;
  color: #0f172a;
}
.apm-md :deep(ul),
.apm-md :deep(ol) {
  margin: 0 0 12px;
  padding-left: 22px;
}
.apm-md :deep(li) {
  margin: 4px 0;
}
.apm-md :deep(li::marker) {
  color: #1e40af;
}
.apm-md :deep(code) {
  font-family: 'JetBrains Mono', monospace;
  font-size: 13px;
  background: rgba(30, 64, 175, 0.07);
  color: #1e40af;
  padding: 2px 6px;
  border-radius: 5px;
}
.apm-md :deep(pre) {
  margin: 0 0 14px;
  padding: 14px 16px;
  background: #0f172a;
  border-radius: 10px;
  overflow-x: auto;
}
.apm-md :deep(pre code) {
  background: transparent;
  color: #e2e8f0;
  padding: 0;
  font-size: 12.5px;
  line-height: 1.7;
}
.apm-md :deep(table) {
  width: 100%;
  margin: 0 0 14px;
  border-collapse: collapse;
  font-size: 13px;
}
.apm-md :deep(th),
.apm-md :deep(td) {
  border: 1px solid rgba(30, 64, 175, 0.12);
  padding: 8px 12px;
  text-align: left;
}
.apm-md :deep(th) {
  background: rgba(30, 64, 175, 0.06);
  font-weight: 700;
}
.apm-md :deep(blockquote) {
  margin: 0 0 14px;
  padding: 8px 14px;
  border-left: 3px solid #1e40af;
  background: rgba(30, 64, 175, 0.05);
  border-radius: 0 8px 8px 0;
  color: #475569;
}
.apm-md :deep(a) {
  color: #1e40af;
  text-decoration: underline;
  text-underline-offset: 2px;
}
.apm-md :deep(hr) {
  border: none;
  height: 1px;
  background: rgba(30, 64, 175, 0.12);
  margin: 18px 0;
}
.apm-md :deep(img) {
  max-width: 100%;
  border-radius: 8px;
}

/* ── CSV 表格 ── */
.apm-modal.apm-csv {
  width: min(1100px, calc(100vw - 48px));
  height: calc(100vh - 80px);
  max-height: calc(100vh - 80px);
}

/* ── HTML 页面：宽幅展示 ── */
.apm-modal.apm-html {
  width: min(1100px, calc(100vw - 48px));
}
.apm-body-csv {
  padding: 0;
  background: #fff;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
.apm-csv-wrap {
  height: 100%;
  overflow: auto;
}
.apm-csv {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
  white-space: nowrap;
}
.apm-csv th,
.apm-csv td {
  padding: 7px 14px;
  border: 1px solid rgba(30, 64, 175, 0.1);
  text-align: left;
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
}
.apm-csv th {
  position: sticky;
  top: 0;
  z-index: 1;
  background: #eaf0f9;
  font-weight: 700;
  color: #1e3a8a;
  font-size: 12px;
  letter-spacing: 0.01em;
}
.apm-csv td {
  color: #334155;
}
.apm-csv tbody tr:nth-child(even) {
  background: rgba(30, 64, 175, 0.025);
}
.apm-csv tbody tr:hover {
  background: rgba(30, 64, 175, 0.06);
}
.apm-csv-meta {
  padding: 8px 14px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  color: #94a3b8;
  border-top: 1px solid rgba(30, 64, 175, 0.08);
}

/* ── Office 模态（vue-office） ── */
.apm-modal.apm-office {
  width: min(1200px, calc(100vw - 48px));
  height: calc(100vh - 80px);
  max-height: calc(100vh - 80px);
}
.apm-body-office {
  padding: 0;
  background: #f5f5f7;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
.apm-viewer {
  flex: 1;
  width: 100%;
  height: 100%;
  min-height: 0;
  overflow: auto;
  background: #f5f5f7;
}
/* Excel 容器内表格：去掉 vue-office 默认背景色，避免和 modal 撞色 */
.apm-body-office :deep(.vue-office-excel-content) {
  background: #ffffff;
}
/* PDF 居中显示 */
.apm-body-office :deep(.vue-office-pdf) {
  background: #f5f5f7;
}

/* ── 视频模态（暗色） ── */
.apm-modal.apm-video {
  width: min(960px, calc(100vw - 48px));
  background: #0a0a0a;
  border: 1px solid rgba(255, 255, 255, 0.1);
  box-shadow: 0 -2px 0 #1e40af, 0 24px 60px -16px rgba(0, 0, 0, 0.7);
  border-radius: 12px;
}
.apm-modal.apm-video .apm-head {
  background: #111;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}
.apm-modal.apm-video .apm-tag,
.apm-modal.apm-video .apm-name,
.apm-modal.apm-video .apm-close {
  color: #e2e8f0;
}
.apm-modal.apm-video .apm-line {
  background: rgba(255, 255, 255, 0.12);
}
.apm-body-video {
  padding: 0;
  background: #000;
  display: flex;
  align-items: center;
  justify-content: center;
}
.apm-player {
  max-width: 100%;
  max-height: calc(100vh - 160px);
  width: 100%;
  outline: none;
}

/* ── 图片放大（暗幕 + 大图 + 名称胶囊 + 关闭） ── */
.apm-img {
  max-width: calc(100vw - 80px);
  max-height: calc(100vh - 140px);
  border-radius: 8px;
  box-shadow: 0 24px 80px -20px rgba(0, 0, 0, 0.8);
}
.apm-img-name {
  position: fixed;
  left: 50%;
  bottom: 26px;
  transform: translateX(-50%);
  display: inline-flex;
  align-items: center;
  gap: 10px;
  max-width: calc(100vw - 120px);
  padding: 6px 14px;
  background: rgba(0, 0, 0, 0.45);
  border-radius: 999px;
  color: rgba(255, 255, 255, 0.85);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.apm-img-dl {
  color: #fff;
  text-decoration: none;
  font-size: 14px;
  opacity: 0.8;
  transition: opacity 0.15s;
}
.apm-img-dl:hover {
  opacity: 1;
}
.apm-img-close {
  position: fixed;
  top: 18px;
  right: 22px;
  width: 36px;
  height: 36px;
  border: none;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.45);
  color: rgba(255, 255, 255, 0.85);
  font-size: 15px;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.apm-img-close:hover {
  background: rgba(0, 0, 0, 0.7);
  color: #fff;
}
</style>
