import { toPng } from 'html-to-image';
import dayjs from 'dayjs';

/**
 * 对话分享图导出工具
 *
 * 实现思路：
 * 1. 把 `.conversation` 节点深克隆到屏外容器，真实页面 DOM 全程不被改动（无视觉抖动、不打断滚动）
 * 2. 把祖先节点（:root / body / .qa-shell）上的 CSS 自定义属性搬到导出容器上
 *    —— 克隆体脱离了原 DOM 树，原本继承的 var(--x) 会全部失效，必须显式迁移
 * 3. 注入一份导出专用样式：隐藏纯交互控件（截断/复制/导出按钮）、停止入场动画、
 *    取消问题文本的 4 行截断，并把毛玻璃降级为纯色
 *    —— html-to-image 基于 SVG foreignObject，不支持 backdrop-filter
 * 4. 同步 canvas 位图（Excalidraw / 图表）、把 iframe（HTML 产物）替换为占位说明
 *    —— iframe 受浏览器同源安全限制无法栅格化
 * 5. 渲染为 PNG 后弹出预览浮层：下载 / 复制到剪贴板 / 长按保存
 */

export interface ExportConversationOptions {
  /** 截取目标（.conversation 节点） */
  node: HTMLElement;
  /** 会话标题 */
  title: string;
  /** 品牌名（头部展示） */
  brandName: string;
  /** 助理名（尾部徽章），可选 */
  assistantName?: string;
  /** 对话轮数（用户消息数） */
  exchangeCount: number;
}

const STYLE_ID = 'qa-export-image-styles';
/** 输出画布最大像素数，防止超长对话 × 高分辨率导致内存爆炸 */
const MAX_CANVAS_PIXELS = 32_000_000;
/** 单张图片等待解码的上限（ms），超时跳过，不阻断整体导出 */
const IMG_DECODE_TIMEOUT = 3000;

const AURORA = 'linear-gradient(110deg, #1e40af 0%, #2563eb 35%, #0ea5e9 70%, #0891b2 100%)';

/* ───────────────────────── 注入样式（仅一次） ───────────────────────── */

const EXPORT_CSS = `
/* ── 屏外宿主：只负责把整棵导出树移出视口，本身不参与截图 ── */
.qa-export-host {
  position: fixed;
  left: -100000px;
  top: 0;
  z-index: -1;
  width: 860px;
  pointer-events: none;
}

/* ── 导出卡片：真正的截图目标，必须保持 static 定位 ── */
.qa-export-card {
  color: #0f172a;
  background:
    radial-gradient(900px 460px at 100% 0%, rgba(30, 64, 175, 0.075), transparent 62%),
    radial-gradient(780px 460px at 0% 100%, rgba(8, 145, 178, 0.06), transparent 62%),
    #f5f7fb;
  font-family: 'Plus Jakarta Sans', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', system-ui, sans-serif;
  -webkit-font-smoothing: antialiased;
}

/* 克隆体内停止一切动画/过渡：入场动画（如 .exchange 的 rise）会在重新挂载时
   从头播放，截图瞬间内容还是 opacity:0，必须全部冻结 */
.qa-export-card *,
.qa-export-card *::before,
.qa-export-card *::after {
  animation: none !important;
  transition: none !important;
}

.qa-export-card .conversation {
  max-width: none !important;
  width: 100% !important;
  margin: 0 !important;
  padding: 8px 56px 4px !important;
}

/* 隐藏只服务于交互的控件 */
.qa-export-card .answer-actions,
.qa-export-card .answer-truncate,
.qa-export-card .q-truncate,
.qa-export-card .code-copy-btn,
.qa-export-card .loading-line {
  display: none !important;
}

/* 问题文本在页面上被截断为 4 行，分享图里应展示完整内容 */
.qa-export-card .q-text {
  display: block !important;
  -webkit-line-clamp: none !important;
  -webkit-box-orient: horizontal !important;
  overflow: visible !important;
  cursor: default !important;
}

/* ── 头部 ── */
.qa-export-header {
  padding: 42px 56px 24px;
}
.qa-export-header-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}
.qa-export-brand {
  display: inline-flex;
  align-items: center;
  gap: 9px;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.02em;
  color: #1e40af;
}
.qa-export-brand-dot {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 7px;
  background: ${AURORA};
  color: #fff;
  font-size: 12px;
  font-weight: 800;
  box-shadow: 0 4px 10px -3px rgba(30, 64, 175, 0.5);
}
.qa-export-date {
  font-family: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace;
  font-size: 11px;
  letter-spacing: 0.04em;
  color: #94a3b8;
}
.qa-export-title {
  margin: 0;
  font-size: 24px;
  font-weight: 700;
  line-height: 1.4;
  letter-spacing: -0.01em;
  color: #0f172a;
  word-break: break-word;
}
.qa-export-title-bar {
  margin-top: 18px;
  width: 64px;
  height: 3px;
  border-radius: 999px;
  background: ${AURORA};
}

/* ── 尾部 ── */
.qa-export-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 14px;
  padding: 20px 56px 38px;
  border-top: 1px solid rgba(30, 64, 175, 0.08);
}
.qa-export-footer-left {
  font-family: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace;
  font-size: 11px;
  letter-spacing: 0.03em;
  color: #94a3b8;
}
.qa-export-footer-right {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 11.5px;
  font-weight: 600;
  color: #64748b;
}
.qa-export-footer-badge {
  padding: 3px 10px;
  border-radius: 999px;
  background: ${AURORA};
  color: #fff;
  font-size: 10.5px;
  font-weight: 700;
  letter-spacing: 0.05em;
}

/* ── iframe 占位（HTML 产物无法被栅格化） ── */
.qa-export-iframe-note {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 12px 0;
  padding: 14px 16px;
  border: 1px dashed rgba(30, 64, 175, 0.28);
  border-radius: 10px;
  background: rgba(30, 64, 175, 0.04);
  font-size: 12.5px;
  color: #64748b;
}

/* ── 预览浮层 ── */
.qa-export-overlay {
  position: fixed;
  inset: 0;
  z-index: 9999;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgba(15, 23, 42, 0.5);
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
  animation: qa-export-fade 0.2s ease-out;
}
.qa-export-panel {
  display: flex;
  flex-direction: column;
  width: min(680px, 100%);
  max-height: calc(100vh - 48px);
  overflow: hidden;
  border: 1px solid rgba(30, 64, 175, 0.1);
  border-radius: 18px;
  background: #ffffff;
  box-shadow: 0 32px 80px -20px rgba(15, 23, 42, 0.4);
  animation: qa-export-pop 0.24s cubic-bezier(0.2, 0.9, 0.3, 1);
}
.qa-export-panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 16px 20px;
  border-bottom: 1px solid rgba(30, 64, 175, 0.08);
}
.qa-export-panel-title {
  font-size: 15px;
  font-weight: 700;
  color: #0f172a;
}
.qa-export-panel-sub {
  margin-top: 2px;
  font-size: 11.5px;
  color: #94a3b8;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.qa-export-close {
  flex-shrink: 0;
  width: 30px;
  height: 30px;
  border: 1px solid rgba(30, 64, 175, 0.1);
  border-radius: 9px;
  background: #fff;
  color: #64748b;
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
  transition: all 0.15s;
}
.qa-export-close:hover {
  background: rgba(30, 64, 175, 0.06);
  color: #1e40af;
}
.qa-export-preview {
  display: flex;
  justify-content: center;
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 18px;
  background: #eef2f8;
}
.qa-export-preview img {
  align-self: flex-start;
  max-width: 100%;
  border-radius: 8px;
  box-shadow: 0 8px 28px -8px rgba(15, 23, 42, 0.25);
}
.qa-export-panel-foot {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  padding: 14px 20px;
  border-top: 1px solid rgba(30, 64, 175, 0.08);
}
.qa-export-hint {
  margin-right: auto;
  font-size: 11.5px;
  color: #94a3b8;
}
.qa-export-btn {
  padding: 8px 16px;
  border: 1px solid transparent;
  border-radius: 10px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.18s;
}
.qa-export-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.qa-export-btn--ghost {
  background: #fff;
  border-color: rgba(30, 64, 175, 0.16);
  color: #1e40af;
}
.qa-export-btn--ghost:hover:not(:disabled) {
  background: rgba(30, 64, 175, 0.06);
  transform: translateY(-1px);
}
.qa-export-btn--primary {
  background: ${AURORA};
  color: #fff;
  box-shadow: 0 4px 14px -2px rgba(30, 64, 175, 0.45);
}
.qa-export-btn--primary:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: 0 8px 20px -4px rgba(30, 64, 175, 0.5);
}
@keyframes qa-export-fade {
  from { opacity: 0; }
  to { opacity: 1; }
}
@keyframes qa-export-pop {
  from { opacity: 0; transform: translateY(10px) scale(0.98); }
  to { opacity: 1; transform: none; }
}
@media (max-width: 640px) {
  .qa-export-overlay { padding: 12px; }
  .qa-export-preview { padding: 12px; }
  .qa-export-panel-foot { flex-wrap: wrap; }
  .qa-export-hint { width: 100%; margin: 0 0 4px; }
}
`;

function ensureStyles(): void {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = EXPORT_CSS;
  document.head.appendChild(style);
}

/* ───────────────────────── 克隆体预处理 ───────────────────────── */

/**
 * 把来源样式表上的 CSS 自定义属性复制到导出容器。
 * 按 :root → body → .qa-shell 的顺序覆盖，模拟原先的继承关系。
 */
function copyCustomProperties(sources: Array<CSSStyleDeclaration | null>, target: HTMLElement): void {
  for (const src of sources) {
    if (!src) continue;
    // CSSStyleDeclaration 可通过下标遍历，自定义属性也包含在内
    for (let i = 0; i < src.length; i += 1) {
      const name = src[i];
      if (name.startsWith('--')) target.style.setProperty(name, src.getPropertyValue(name));
    }
  }
}

/** 克隆体里的 canvas（Excalidraw / 图表）是空白位图，从原节点逐个拷贝 */
function syncCanvases(original: HTMLElement, clone: HTMLElement): void {
  const origList = Array.from(original.querySelectorAll('canvas'));
  const cloneList = Array.from(clone.querySelectorAll('canvas'));
  origList.forEach((orig, i) => {
    const dup = cloneList[i];
    if (!dup || orig.width === 0 || orig.height === 0) return;
    try {
      dup.width = orig.width;
      dup.height = orig.height;
      dup.getContext('2d')?.drawImage(orig, 0, 0);
    } catch {
      // 跨域污染等场景静默跳过
    }
  });
}

/** iframe（HTML 产物）无法被栅格化，替换为说明占位 */
function replaceIframes(root: HTMLElement): void {
  root.querySelectorAll('iframe').forEach(iframe => {
    const note = document.createElement('div');
    note.className = 'qa-export-iframe-note';
    const mark = document.createElement('span');
    mark.textContent = '⧉';
    const text = document.createElement('span');
    text.textContent = '内嵌网页内容（HTML 产物）未包含在分享图中，请在原对话里查看';
    note.append(mark, text);
    iframe.replaceWith(note);
  });
}

/** 屏外克隆体中的 lazy 图片不会自动加载，强制 eager 并等待解码 */
async function prepareImages(root: HTMLElement): Promise<void> {
  const imgs = Array.from(root.querySelectorAll('img'));
  await Promise.all(
    imgs.map(async img => {
      try {
        img.loading = 'eager';
        img.decoding = 'sync';
        if (img.complete && img.naturalWidth > 0) return;
        await Promise.race([
          // catch 兜底：race 超时后 decode 才拒绝会成为未处理的 rejection
          img.decode().catch(() => undefined),
          new Promise(resolve => {
            setTimeout(resolve, IMG_DECODE_TIMEOUT);
          })
        ]);
      } catch {
        // 单张图片失败不阻断整体导出
      }
    })
  );
}

/* ───────────────────────── 头部 / 尾部分享卡片装饰 ───────────────────────── */

function buildHeader(opts: ExportConversationOptions): HTMLElement {
  const header = document.createElement('header');
  header.className = 'qa-export-header';

  const top = document.createElement('div');
  top.className = 'qa-export-header-top';

  const brandEl = document.createElement('div');
  brandEl.className = 'qa-export-brand';
  const dot = document.createElement('span');
  dot.className = 'qa-export-brand-dot';
  dot.textContent = '✦';
  const brandText = document.createElement('span');
  brandText.textContent = opts.brandName || 'AI 助理';
  brandEl.append(dot, brandText);

  const date = document.createElement('div');
  date.className = 'qa-export-date';
  date.textContent = dayjs().format('YYYY-MM-DD HH:mm');

  top.append(brandEl, date);

  const title = document.createElement('h1');
  title.className = 'qa-export-title';
  title.textContent = opts.title || '新对话';

  const bar = document.createElement('div');
  bar.className = 'qa-export-title-bar';

  header.append(top, title, bar);
  return header;
}

function buildFooter(opts: ExportConversationOptions): HTMLElement {
  const footer = document.createElement('footer');
  footer.className = 'qa-export-footer';

  const left = document.createElement('div');
  left.className = 'qa-export-footer-left';
  left.textContent = `${opts.exchangeCount} 轮问答 · ${dayjs().format('YYYY-MM-DD HH:mm')} 分享自对话`;

  const right = document.createElement('div');
  right.className = 'qa-export-footer-right';
  const badge = document.createElement('span');
  badge.className = 'qa-export-footer-badge';
  badge.textContent = opts.assistantName || 'AI 助理';
  const tail = document.createElement('span');
  tail.textContent = '由 AI 生成';
  right.append(badge, tail);

  footer.append(left, right);
  return footer;
}

/* ───────────────────────── 预览浮层 ───────────────────────── */

function buildFileName(title: string): string {
  const safe = (title || '对话').replace(/[\\/:*?"<>|\s]+/g, '_').slice(0, 40);
  return `${safe}_分享图_${dayjs().format('YYYYMMDD_HHmmss')}.png`;
}

function showPreview(dataUrl: string, title: string): void {
  const overlay = document.createElement('div');
  overlay.className = 'qa-export-overlay';

  const panel = document.createElement('div');
  panel.className = 'qa-export-panel';

  // 头部
  const head = document.createElement('div');
  head.className = 'qa-export-panel-head';
  const headText = document.createElement('div');
  const headTitle = document.createElement('div');
  headTitle.className = 'qa-export-panel-title';
  headTitle.textContent = '对话分享图';
  const headSub = document.createElement('div');
  headSub.className = 'qa-export-panel-sub';
  headSub.textContent = title || '新对话';
  headText.append(headTitle, headSub);
  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'qa-export-close';
  closeBtn.setAttribute('aria-label', '关闭');
  closeBtn.textContent = '×';
  head.append(headText, closeBtn);

  // 图片预览
  const preview = document.createElement('div');
  preview.className = 'qa-export-preview';
  const img = document.createElement('img');
  img.src = dataUrl;
  img.alt = '对话分享图预览';
  preview.appendChild(img);

  // 操作区
  const foot = document.createElement('div');
  foot.className = 'qa-export-panel-foot';
  const hint = document.createElement('span');
  hint.className = 'qa-export-hint';
  hint.textContent = '提示：可直接右键 / 长按图片保存';

  const copyBtn = document.createElement('button');
  copyBtn.type = 'button';
  copyBtn.className = 'qa-export-btn qa-export-btn--ghost';
  copyBtn.textContent = '复制图片';

  const downloadBtn = document.createElement('button');
  downloadBtn.type = 'button';
  downloadBtn.className = 'qa-export-btn qa-export-btn--primary';
  downloadBtn.textContent = '下载图片';

  foot.append(hint, copyBtn, downloadBtn);
  panel.append(head, preview, foot);
  overlay.appendChild(panel);

  const close = () => {
    document.removeEventListener('keydown', onKey);
    overlay.remove();
  };
  const onKey = (e: KeyboardEvent) => {
    if (e.key === 'Escape') close();
  };
  document.addEventListener('keydown', onKey);
  overlay.addEventListener('click', e => {
    if (e.target === overlay) close();
  });
  closeBtn.addEventListener('click', close);

  downloadBtn.addEventListener('click', () => {
    const a = document.createElement('a');
    a.href = dataUrl;
    a.download = buildFileName(title);
    a.click();
  });

  // 复制到剪贴板：能力检测，不支持则隐藏
  const canClipboard = typeof ClipboardItem !== 'undefined' && !!navigator.clipboard?.write;
  if (!canClipboard) {
    copyBtn.style.display = 'none';
  } else {
    copyBtn.addEventListener('click', async () => {
      try {
        copyBtn.disabled = true;
        copyBtn.textContent = '复制中…';
        const blob = await (await fetch(dataUrl)).blob();
        await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
        copyBtn.textContent = '已复制 ✓';
      } catch {
        copyBtn.textContent = '复制失败';
      } finally {
        copyBtn.disabled = false;
        setTimeout(() => {
          copyBtn.textContent = '复制图片';
        }, 1600);
      }
    });
  }

  document.body.appendChild(overlay);
}

/* ───────────────────────── 主入口 ───────────────────────── */

function nextFrame(): Promise<void> {
  return new Promise(resolve => {
    requestAnimationFrame(() => resolve());
  });
}

export async function exportConversationAsImage(opts: ExportConversationOptions): Promise<void> {
  ensureStyles();

  const shell = opts.node.closest<HTMLElement>('.qa-shell');
  const clone = opts.node.cloneNode(true) as HTMLElement;

  syncCanvases(opts.node, clone);
  replaceIframes(clone);

  // host 负责屏外定位（position:fixed 会被 html-to-image 原样内联进截图根节点，
  // 导致内容被推出画布；所以定位放在不参与截图的宿主上）
  const host = document.createElement('div');
  host.className = 'qa-export-host';
  const card = document.createElement('div');
  card.className = 'qa-export-card';
  copyCustomProperties(
    [getComputedStyle(document.documentElement), getComputedStyle(document.body), shell ? getComputedStyle(shell) : null],
    card
  );
  card.append(buildHeader(opts), clone, buildFooter(opts));
  host.appendChild(card);
  document.body.appendChild(host);

  try {
    await prepareImages(card);
    await nextFrame();

    const rect = card.getBoundingClientRect();
    const pixelRatio = Math.min(2, Math.max(1, MAX_CANVAS_PIXELS / Math.max(1, rect.width * rect.height)));

    let dataUrl: string;
    try {
      dataUrl = await toPng(card, { pixelRatio, backgroundColor: '#f5f7fb' });
    } catch {
      // 字体内联失败（跨域样式表等）时降级：不嵌入 webfont，用系统字体兜底
      dataUrl = await toPng(card, { pixelRatio, backgroundColor: '#f5f7fb', skipFonts: true });
    }

    showPreview(dataUrl, opts.title);
  } finally {
    host.remove();
  }
}
