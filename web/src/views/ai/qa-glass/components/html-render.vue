<script setup lang="ts">
import {computed, onBeforeUnmount, onMounted, ref, watch} from 'vue';

const props = defineProps<{
  html: string;
  fullscreen?: boolean;
  fullWidth?: boolean;
  /** 固定高度容器内使用：iframe 填满容器、页面内部可滚动（滚动条 / 滚轮），不再自动量高。
   *  默认只纵向滚动、横向裁掉；搭配 fullscreen 时横向也放开（浏览器式双向滚动） */
  scrollable?: boolean;
  /** 全屏预览的展示策略（仅 fullscreen + scrollable 时生效）：
   *  auto = 自动检测：游戏/应用类页面（大画布 canvas、全屏裁剪布局）走「缩放适配」，普通长页面走「原尺寸滚动」（默认）
   *  fit  = 强制缩放适配：整个页面等比缩小进窗口（不放大），弹窗收缩包裹内容，完整不被裁
   *  scroll = 强制原尺寸滚动 */
  mode?: 'auto' | 'fit' | 'scroll';
  /** 缩放适配的可用视口（宽/高，px）：弹窗最大可用区由父级算好传入。
   *  scale 以此为基准而非随弹窗收缩的容器实测——弹窗收缩包裹内容时不会形成反馈回环；不传则退回容器实测 */
  fitViewport?: {w: number; h: number};
  /** 缩放铺满：允许 scale > 1（放大铺满，供原生全屏用）；默认 scale ≤ 1（收缩包裹模式不放大、不发虚） */
  fitFill?: boolean;
}>();
const emit = defineEmits<{
  (e: 'resize', height: number, width: number): void;
  /** 缩放适配模式：上报缩放后的展示尺寸，父级据此收缩弹窗包裹内容（消除留白） */
  (e: 'fitSize', width: number, height: number): void;
}>();

const container = ref<HTMLIFrameElement | null>(null);
const wrapper = ref<HTMLDivElement | null>(null);
const height = ref(120);

// ── 缩放适配（全屏预览）──────────────────────────────────────────────
// 容器实测尺寸（ResizeObserver）
const wrapW = ref(0);
const wrapH = ref(0);
// iframe 内上报的页面内容尺寸
const contentW = ref(0);
const contentH = ref(0);
// iframe 内自检为应用/游戏类（大画布 canvas / 全屏裁剪布局）
const appLike = ref(false);
// 流式页面：内容尺寸跟随视口布局（如 canvas 按 window 尺寸绘制）。
// 判定（内容 ≈ 测量时视口）一旦成立即锁存：此类页面 iframe 直接填满容器、不走 transform，
// 容器变化（原生全屏 / 窗口缩放）时游戏按新尺寸原生重排，清晰不糊
const fluidLike = ref(false);
let wrapRO: ResizeObserver | null = null;

const fitAllowed = computed(() => !!(props.fullscreen && props.scrollable));
const fitActive = computed(() => fitAllowed.value && (props.mode === 'fit' || ((props.mode ?? 'auto') === 'auto' && appLike.value)));

// 缩放基准：优先父级给定的可用视口（弹窗最大可用区）。
// 弹窗会收缩包裹内容，若以容器实测为基准，收缩→容器变小→scale 再变→无限收缩回环
const fitBaseW = computed(() => props.fitViewport?.w || wrapW.value);
const fitBaseH = computed(() => props.fitViewport?.h || wrapH.value);

// scale 上限：收缩包裹模式 ≤ 1（不放大）；铺满模式（原生全屏）不设上限，放大铺满屏幕
const scaleCap = computed(() => (props.fitFill ? Number.POSITIVE_INFINITY : 1));

// 等比缩放适配
const scale = computed(() => {
  if (!fitActive.value || !fitBaseW.value || !fitBaseH.value || !contentW.value || !contentH.value) return 1;
  return Math.round(Math.min(scaleCap.value, fitBaseW.value / contentW.value, fitBaseH.value / contentH.value) * 10000) / 10000;
});

// 缩放后的展示尺寸（弹窗收缩包裹的依据）
const displayW = computed(() => (fitActive.value && contentW.value ? Math.max(1, Math.floor(contentW.value * scale.value)) : 0));
const displayH = computed(() => (fitActive.value && contentH.value ? Math.max(1, Math.floor(contentH.value * scale.value)) : 0));

// 缩放适配：按内容尺寸渲染 + transform 等比缩放 + wrapper 内居中
// 全屏 / 可滚动：height:100% 填满容器（容器给固定高度），消除上下留白
// 其他：用内部测量高度
const iframeStyle = computed(() => {
  if (fitActive.value) {
    // 流式页面：iframe 直接填满容器（内容随视口重排），无需 transform 缩放
    if (fluidLike.value) return {height: '100%', width: '100%'};
    return {
      position: 'absolute' as const,
      width: contentW.value ? `${contentW.value}px` : '100%',
      height: contentH.value ? `${contentH.value}px` : '100%',
      transform: `scale(${scale.value})`,
      transformOrigin: 'top left',
      left: `${Math.max(0, (wrapW.value - contentW.value * scale.value) / 2)}px`,
      top: `${Math.max(0, (wrapH.value - contentH.value * scale.value) / 2)}px`
    };
  }
  if (props.fullscreen || props.scrollable) {
    return {height: '100%', width: '100%'};
  }
  return {height: height.value + 'px'};
});

function buildResizeScript(refHeight: number, fit: boolean): string {
  return `
<script>
(function(){
  var REF=${refHeight},FIT=${fit ? 'true' : 'false'},lastH=0,lastW=0,timer=null;
  function measure(){
    var b=document.body;
    if(!b)return{h:0,w:0};
    var h=0,w=0;
    for(var i=0;i<b.children.length;i++){
      var el=b.children[i];
      if(window.getComputedStyle(el).display==='none')continue;
      var r=el.getBoundingClientRect();
      if(FIT){
        /* 缩放适配：居中布局可能产生负偏移，完整宽高要算上两侧溢出 */
        h=Math.max(h,r.bottom-Math.min(r.top,0));
        w=Math.max(w,r.right-Math.min(r.left,0));
      }else{
        h=Math.max(h,r.bottom);
        w=Math.max(w,r.right);
      }
    }
    h=Math.max(h,b.scrollHeight);
    w=Math.max(w,b.scrollWidth);
    return{h:Math.ceil(h),w:Math.ceil(w)};
  }
  function report(){
    var m=measure();
    if(lastH===0){m.h=Math.max(m.h,REF);}
    if(FIT){
      /* 缩放适配模式：iframe 尺寸由父级控制，不会反哺内容尺寸，持续双向上报不产生循环 */
      lastH=m.h;lastW=m.w;
      window.parent.postMessage({type:'html-render-resize',height:m.h,width:m.w},'*');
      return;
    }
    if(m.h<=lastH&&m.w<=lastW)return;
    lastH=m.h;lastW=m.w;
    window.parent.postMessage({type:'html-render-resize',height:m.h,width:m.w},'*');
  }
  function debounced(){clearTimeout(timer);timer=setTimeout(report,60);}
  if(typeof ResizeObserver!=='undefined'){
    var ro=new ResizeObserver(debounced);
    ro.observe(document.body);
  }
  window.addEventListener('load',report);
  report();

  /* 应用/游戏类页面检测：大画布 canvas、全屏裁剪布局、覆盖视口的 fixed/absolute 容器
     → 提示父级改用「缩放适配」，避免固定尺寸内容被弹窗视口裁掉。
     单屏判定兜底：内嵌大画布图表的长文档（报告页）不算游戏，仍走滚动 */
  function detectApp(){
    var b=document.body;
    if(!b)return false;
    var de=document.documentElement;
    var vw=window.innerWidth,vh=window.innerHeight;
    var oneScreen=Math.max(b.scrollHeight,de.scrollHeight)<=vh*1.5;
    var cs=document.querySelectorAll('canvas');
    for(var i=0;i<cs.length;i++){
      var c=cs[i],cr=c.getBoundingClientRect();
      if(((c.width>=320&&c.height>=200)||(cr.width>=320&&cr.height>=200))&&oneScreen)return true;
    }
    var hidden=window.getComputedStyle(b).overflow==='hidden'||window.getComputedStyle(de).overflow==='hidden';
    for(var j=0;j<b.children.length;j++){
      var el2=b.children[j],r2=el2.getBoundingClientRect();
      var cover=r2.width>=vw*0.8&&r2.height>=vh*0.6;
      if(!cover)continue;
      if(hidden)return true;
      var pos=window.getComputedStyle(el2).position;
      if((pos==='fixed'||pos==='absolute')&&oneScreen)return true;
    }
    return false;
  }
  function reportApp(){
    try{window.parent.postMessage({type:'html-render-app',app:!!detectApp()},'*');}catch(e){}
  }
  var appTimer=null;
  function scheduleApp(){clearTimeout(appTimer);appTimer=setTimeout(reportApp,300);}
  window.addEventListener('load',scheduleApp);
  if(typeof MutationObserver!=='undefined'&&document.body){
    var mo=new MutationObserver(scheduleApp);
    mo.observe(document.body,{childList:true,subtree:true});
  }
  reportApp();

  /* 阻止方向键 / 空格等触发 iframe 内默认滚动，让游戏类内容能独占按键 */
  var BLOCK=new Set(['ArrowUp','ArrowDown','ArrowLeft','ArrowRight',' ','PageUp','PageDown','Home','End']);
  document.addEventListener('keydown',function(e){
    if(!BLOCK.has(e.key))return;
    var t=e.target;
    if(t&&(t.tagName==='INPUT'||t.tagName==='TEXTAREA'||t.tagName==='SELECT'||t.isContentEditable))return;
    e.preventDefault();
  });
})();
<\/script>`;
}

// 样式注入：reset + 基础排版，融入对话区配色
// overflow:hidden 阻断 iframe 自身滚动高度反馈死循环
const BASE_STYLE = `
<style>
*,*::before,*::after{box-sizing:border-box}
html,body{margin:0;padding:0;width:100%}
body{
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Hiragino Sans GB',system-ui,sans-serif;
  font-size:14px;line-height:1.6;color:#1e293b;
  background:#ffffff;padding:16px 20px;
}
a{color:#3b82f6;text-decoration:none}
a:hover{text-decoration:underline}
img{max-width:100%;height:auto}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{border:1px solid #e2e8f0;padding:6px 10px;text-align:left}
th{background:#f8fafc;font-weight:600;color:#475569}
tr:hover>td{background:#f8fafc}
h1,h2,h3,h4{margin:.8em 0 .4em;font-weight:600;line-height:1.3}
h1{font-size:1.4em}h2{font-size:1.2em}h3{font-size:1.05em}
p{margin:.4em 0 .7em}
code{background:#f1f5f9;border-radius:3px;padding:1px 5px;font-size:12px;font-family:'JetBrains Mono',monospace}
pre{background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:12px 14px;overflow-x:auto}
pre code{background:none;padding:0;font-size:12px}
ul,ol{padding-left:1.5em;margin:.4em 0 .7em}
blockquote{margin:.4em 0;padding:.4em .8em;border-left:3px solid #cbd5e1;color:#64748b}
hr{border:none;border-top:1px solid #e2e8f0;margin:12px 0}
details{margin:.3em 0}
summary{cursor:pointer;user-select:none;color:#64748b;font-size:12px;list-style:none;display:inline-flex;align-items:center;gap:4px}
summary::-webkit-details-marker{display:none}
summary::before{content:'▶';font-size:10px;transition:transform .15s;display:inline-block}
details[open]>summary::before{transform:rotate(90deg)}
details[open]>summary{color:#475569}
</style>`;

function buildSrcdoc(rawHtml: string): string {
  const fit = fitActive.value;
  const fullWidthStyle = props.fullWidth
    ? `<style>*{max-width:none!important}body,body>div,body>[class]{width:100%!important;margin-left:0!important;margin-right:0!important}</style>`
    : '';
  // 可滚动模式：卡片场景只留纵向滚动、横向裁掉；全屏预览（fullscreen）横纵都放开。
  // 缩放适配模式：裁掉文档自身滚动，整体缩放交给外层 transform。
  // 滚动条是 iframe 内部文档的（外层 CSS 摸不到），在此注入细条圆角半透明样式，同画板 .text-area 的滚动条语言
  const scrollStyle = props.scrollable
    ? `<style>${props.fullscreen ? '' : 'html,body{overflow-x:hidden!important}'}
${fit ? 'html,body{overflow:hidden!important}' : ''}
html{scrollbar-width:thin;scrollbar-color:rgba(30,64,175,.3) transparent}
::-webkit-scrollbar{width:6px;height:6px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:rgba(30,64,175,.24);border-radius:3px}
::-webkit-scrollbar-thumb:hover{background:rgba(30,64,175,.45)}</style>`
    : '';
  const hasDoctype = /<!doctype\s+html/i.test(rawHtml);
  const hasHtmlTag = /<html[\s>]/i.test(rawHtml);
  if (hasDoctype || hasHtmlTag) {
    // 缩放适配模式不需要最小高度兜底（REF=0），避免小尺寸页面首帧测量失真
    const extra = fullWidthStyle + scrollStyle + buildResizeScript(fit ? 0 : 500, fit);
    const injected = rawHtml.replace(/<\/body>/i, `${extra}</body>`);
    return injected !== rawHtml ? injected : rawHtml + extra;
  }
  return `<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">${BASE_STYLE}${scrollStyle}</head><body>${rawHtml}${buildResizeScript(0, fit)}</body></html>`;
}

// srcdoc 依赖 fitActive / fullWidth / scrollable，模式切换时重建（iframe 重载一次）
const srcdoc = computed(() => buildSrcdoc(props.html));

function onMessage(e: MessageEvent) {
  if (!container.value || e.source !== container.value.contentWindow) return;
  const data = e.data;
  if (!data) return;
  if (data.type === 'html-render-resize' && typeof data.height === 'number') {
    contentW.value = typeof data.width === 'number' ? Math.max(0, data.width) : 0;
    contentH.value = data.height;
    // 缩放适配模式下 iframe 尺寸由 scale 计算，不走自动量高
    if (fitActive.value) {
      // 流式判定（锁存）：内容 ≈ 视口 → 页面随视口布局。首帧可能偏小（未加载完），
      // 后续测量追平视口时锁存为 true；固定尺寸页面恒不等于视口，不会误判
      if (!fluidLike.value) {
        const bw = fitBaseW.value;
        const bh = fitBaseH.value;
        fluidLike.value = bw > 0 && bh > 0
          && Math.abs(contentW.value - bw) <= Math.max(8, bw * 0.03)
          && Math.abs(contentH.value - bh) <= Math.max(8, bh * 0.03);
      }
    } else {
      const next = Math.max(80, data.height);
      height.value = next;
      emit('resize', next, contentW.value || 0);
    }
  } else if (data.type === 'html-render-app') {
    appLike.value = !!data.app;
  }
}

onMounted(() => {
  window.addEventListener('message', onMessage);
  if (typeof ResizeObserver !== 'undefined' && wrapper.value) {
    wrapRO = new ResizeObserver(entries => {
      for (const en of entries) {
        wrapW.value = Math.round(en.contentRect.width);
        wrapH.value = Math.round(en.contentRect.height);
      }
    });
    wrapRO.observe(wrapper.value);
  }
});

onBeforeUnmount(() => {
  window.removeEventListener('message', onMessage);
  wrapRO?.disconnect();
});

watch(() => props.html, () => {
  height.value = 120;
  contentW.value = 0;
  contentH.value = 0;
  appLike.value = false;
  fluidLike.value = false;
});

// 模式切换时丢弃旧的内容尺寸，避免用滚动模式的测量值（如超长页面高度）初始化缩放
watch(fitActive, active => {
  contentW.value = 0;
  contentH.value = 0;
  // 退出缩放适配（如 auto 检测翻转为普通页）→ 通知父级恢复默认大窗
  if (!active) emit('fitSize', 0, 0);
});

// 缩放后的展示尺寸变化 → 上报父级收缩弹窗（包裹内容、消除留白）
watch([displayW, displayH], ([w, h]) => {
  if (fitActive.value && w > 0 && h > 0) emit('fitSize', w, h);
});
</script>

<template>
  <div ref="wrapper" class="html-render-wrap" :class="{'html-render-wrap--fit': fitActive}">
    <iframe
      ref="container"
      class="html-render"
      :class="{ 'html-render--fs': fullscreen, 'html-render--scroll': scrollable && !fitActive, 'html-render--scroll-xy': scrollable && fullscreen && !fitActive }"
      :srcdoc="srcdoc"
      :style="iframeStyle"
      sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
      referrerpolicy="no-referrer"
      :scrolling="scrollable && !fitActive ? 'auto' : 'no'"
    />
  </div>
</template>

<style scoped>
/* 包裹层：默认透明无感（宽高 100% 传递）；缩放适配时提供定位上下文并裁掉未缩放溢出 */
.html-render-wrap {
  display: block;
  width: 100%;
  height: 100%;
}

.html-render-wrap--fit {
  position: relative;
  overflow: hidden;
}

/* 缩放适配模式下 iframe 尺寸/位移由 scale 计算，弹窗收缩/全屏切换时平滑过渡 */
.html-render-wrap--fit .html-render {
  transition:
    width 0.18s ease,
    height 0.18s ease,
    transform 0.18s ease,
    left 0.18s ease,
    top 0.18s ease;
}

.html-render {
  display: block;
  width: 100%;
  border: none;
  border-radius: 14px;
  background: #ffffff;
  transition: height 0.15s ease;
  overflow: hidden;
}

.html-render--fs {
  border-radius: 0;
  transition: none;
}

/* 可滚动模式：基类的 overflow:hidden 会禁掉 iframe 滚动条（scrolling 属性被 CSS overflow 覆盖），这里只放纵向、横向裁掉 */
.html-render--scroll {
  overflow-x: hidden;
  overflow-y: auto;
  transition: none;
}

/* 全屏预览的可滚动模式：横向也放开（iframe 视口层，内层文档同步不再裁 x） */
.html-render--scroll-xy {
  overflow-x: auto;
}
</style>
