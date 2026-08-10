/**
 * 由视口坐标反推 input / textarea 内的光标位置 —— 「点哪编辑哪」。
 *
 * 背景：工作流卡字段在展示态是 readonly + pointer-events:none（class .view），事件穿透到卡片外壳，
 * 整卡可拖拽；因此点击坐标落在卡上而不是字段上。进入编辑态时用本函数把点击坐标反推成字符位置，
 * 光标直接落在用户点的那个字上，而不是粗暴全选。
 *
 * 实现 = 镜像元素 + 二分搜索：隐藏 mirror div 复制字段的排版样式与内容宽度，铺排全文，
 * 在候选位置插入零宽标记（U+200B）测量其坐标；阅读顺序（先上后下、先左后右）随字符索引单调，
 * 二分定位到点击的行列，再与相邻候选比距离取近者。
 * 不用 document.caretRangeFromPoint / caretPositionFromPoint——主流浏览器进不了表单控件的影子女本。
 *
 * 缩放兼容：vue-flow 视口 transform 会缩放 getBoundingClientRect 但不缩放 offset 度量，
 * 先用 rect.width / offsetWidth 比例把点击坐标换算进元素本地坐标系。
 */

export interface CaretHit {
  /** 光标字符索引（0 .. value.length） */
  index: number;
  /** 光标所在行顶边（内容坐标系 px，不含字段自身 scrollTop）——供调用方把光标滚进可视区 */
  top: number;
}

/** 影响换行与字位的排版属性（镜像必须与字段逐项一致，否则换行位置不同反推就偏） */
const TEXT_PROPS = [
  'fontFamily',
  'fontSize',
  'fontStyle',
  'fontWeight',
  'letterSpacing',
  'lineHeight',
  'textTransform',
  'wordSpacing',
  'textIndent',
  'tabSize',
  'direction',
] as const;

export function caretFromPoint(el: HTMLInputElement | HTMLTextAreaElement, clientX: number, clientY: number): CaretHit | null {
  const value = el.value;
  if (!value) return {index: 0, top: 0};
  const rect = el.getBoundingClientRect();
  if (!rect.width || !rect.height || !el.offsetWidth) return null;

  const cs = getComputedStyle(el);
  const isTA = el instanceof HTMLTextAreaElement;
  // 点击坐标 → 元素内容坐标系：缩放到本地 → 减 border/padding → 补滚动偏移（镜像渲染全文，不随字段内滚动）
  const scale = rect.width / el.offsetWidth;
  const padL = parseFloat(cs.paddingLeft) || 0;
  const padT = parseFloat(cs.paddingTop) || 0;
  const padR = parseFloat(cs.paddingRight) || 0;
  const borderL = parseFloat(cs.borderLeftWidth) || 0;
  const borderT = parseFloat(cs.borderTopWidth) || 0;
  const scrollX = isTA ? el.scrollLeft : 0;
  const scrollY = isTA ? el.scrollTop : 0;
  const x = (clientX - rect.left) / scale - borderL - padL + scrollX;
  const y = (clientY - rect.top) / scale - borderT - padT + scrollY;

  // 镜像元素：屏外隐藏，按字段内容盒宽度铺排全文（input 单行不换行，宽度放开）
  const mirror = document.createElement('div');
  const ms = mirror.style;
  ms.position = 'absolute';
  ms.left = '-9999px';
  ms.top = '0';
  ms.visibility = 'hidden';
  ms.display = 'block';
  ms.whiteSpace = isTA ? cs.whiteSpace || 'pre-wrap' : 'pre';
  ms.wordBreak = cs.wordBreak;
  ms.overflowWrap = cs.overflowWrap;
  if (isTA) ms.width = `${Math.max(1, el.clientWidth - padL - padR)}px`;
  for (const p of TEXT_PROPS) ms[p] = cs[p];

  // 零宽空格标记：位置即光标位，不影响周围排版
  const marker = document.createElement('span');
  marker.textContent = '\u200b';

  const measure = (i: number) => {
    mirror.textContent = value.slice(0, i);
    mirror.appendChild(marker);
    if (i < value.length) mirror.appendChild(document.createTextNode(value.slice(i)));
    const mr = mirror.getBoundingClientRect();
    const kr = marker.getBoundingClientRect();
    return {mx: kr.left - mr.left, top: kr.top - mr.top, cy: kr.top - mr.top + kr.height / 2};
  };

  document.body.appendChild(mirror);
  try {
    const fontSize = parseFloat(cs.fontSize) || 13;
    const lh = parseFloat(cs.lineHeight);
    const lineH = Number.isFinite(lh) && lh > 0 ? lh : fontSize * 1.2;
    // cmp：-1 = 标记在阅读顺序上位于点击点之前（索引偏小），1 = 之后
    const cmp = (i: number): number => {
      const p = measure(i);
      if (p.cy < y - lineH / 2) return -1;
      if (p.cy > y + lineH / 2) return 1;
      return p.mx < x ? -1 : 1; // 同一行：横向比较
    };
    let lo = 0;
    let hi = value.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (cmp(mid) < 0) lo = mid + 1;
      else hi = mid;
    }
    // lo = 插入点；与 lo-1 比实际距离取近者，避免行列阈值处差一位
    const pLo = measure(lo);
    let best = lo;
    let bestP = pLo;
    if (lo > 0) {
      const pPrev = measure(lo - 1);
      if ((pPrev.mx - x) ** 2 + (pPrev.cy - y) ** 2 < (pLo.mx - x) ** 2 + (pLo.cy - y) ** 2) {
        best = lo - 1;
        bestP = pPrev;
      }
    }
    return {index: best, top: bestP.top};
  } finally {
    mirror.remove();
  }
}
