/** 技能图标渲染（卡片 / 详情页共用）。
 *
 * DB（agent_skill.icon）存两种形态，都直接入库：
 * - agent 生成的单个 `<svg>` 元素源码（线条风小图标）
 * - 普通图片的 base64 data URI（用户给的图片原样当图标）
 * 均通过 v-html 注入，故渲染前做白名单清洗，拒绝 script / on* 事件 / javascript: 伪协议。
 */

/** 兜底图标：四芒星（技能=能力火花），currentColor 继承容器主题色 */
export const FALLBACK_SKILL_ICON =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3z"/></svg>';

/** 只接受常见位图 data URI（svg+xml 走源码分支），防 javascript: 等伪装 */
const DATA_IMG_RE = /^data:image\/(png|jpe?g|gif|webp);base64,[A-Za-z0-9+/=]+$/i;

function sanitizeSvg(raw: string): string {
  if (!raw.startsWith('<svg') || /<script|javascript:|\son\w+\s*=/i.test(raw)) return '';
  return ensureViewBox(raw);
}

/** 缺 viewBox 的 svg 补上（按 width/height 推导）：否则 CSS 定宽高时浏览器不缩放而按原坐标裁切，
 *  图标只显示左上角一截（「少半截」）。已有存量数据在渲染层统一归一，无需迁移。 */
function ensureViewBox(raw: string): string {
  const end = raw.indexOf('>');
  const tag = end > 0 ? raw.slice(0, end) : raw;
  if (/viewBox\s*=/i.test(tag)) return raw;
  const w = /width\s*=\s*["']?([\d.]+)/i.exec(tag)?.[1];
  const h = /height\s*=\s*["']?([\d.]+)/i.exec(tag)?.[1];
  if (!w || !h) return raw;
  return raw.replace(/^<svg/i, `<svg viewBox="0 0 ${w} ${h}"`);
}

/** 返回可 v-html 的片段：图片 data URI → <img>；合法 svg → 原样；否则兜底星 */
export function skillIconHtml(icon: string | null | undefined): string {
  return customIconHtml(icon) || FALLBACK_SKILL_ICON;
}

/** 仅当 icon 是 svg 源码/图片 data URI 时返回可 v-html 片段，否则返回 ''
 *（调用方自行回落其它渲染，如 iconify SvgIcon —— 橱窗新旧图标混存场景用） */
export function customIconHtml(icon: string | null | undefined): string {
  const raw = (icon || '').trim();
  if (DATA_IMG_RE.test(raw)) return `<img src="${raw}" alt="" draggable="false" />`;
  return sanitizeSvg(raw);
}
