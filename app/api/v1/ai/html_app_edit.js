/*
 * 应用制作系统注入脚本——「编辑文字」（由 html_app.py::serve_file 在响应 html 时注入页面闭合标签前，不随文件落盘）
 *
 * 入口在看板画布外框顶栏的「编辑文字」按钮（workflow/index.vue）：宿主经 postMessage
 * 发 {type:'hbte-toggle-edit'}，本脚本切换编辑态并回报 {type:'hbte-edit-state', editing, editableCount}。
 *
 * 功能边界（刻意做小）：只允许用户就地修改页面上的静态文本，不碰样式、结构与 JS 渲染的内容。
 *
 * 编辑模式 = 页面冻结态：全页 pointer-events 关闭（仅编辑块放开）+ window 捕获层拦截
 * 页面自身的键盘 / 滚轮 / 鼠标监听（翻页快捷键、点击翻页、hover 特效等一律不响应），
 * 编辑块内的打字 / 光标 / 选择照常。Enter=提交失焦，Tab=下一块，粘贴强制纯文本。
 *
 * 编辑块按「文本段」收集：遍历文本节点，把每段有意义的文字临时包进可编辑 span，
 * 退出编辑时拆掉 span 还原 DOM——带图标混排的标题、按钮/链接里的文字都能覆盖；
 * 跳过代码 / 表单控件 / 媒体 / svg 内部等必然出错的容器，以及不可见上下文
 * （翻页 PPT 的 opacity:0 隐藏页等：编辑 span 自带 pointer-events:auto，包了隐形文字会抢走可见页点击）。
 * 因此编辑模式只覆盖当前可见内容——翻页快捷键已被冻结，要改别的页就退出编辑 → 翻页 → 再进。
 *
 * 持久化：改动以替换对 {from: 原文, to: 改后} 存进页面目录 data/text-edits.json（走现有 /save 通道），
 * 页面加载时自动 fetch 并套用（按清单顺序逐条替换，多次修改自然链式生效）——agent 每次发布
 * 都会重载 iframe，纯内存改动会丢，必须落盘。之所以存「文本替换对」而不是 DOM 选择器：
 * agent 下一轮迭代会重写 HTML，选择器必失效，而替换对在文字还在时始终能命中；
 * agent 改版时也会先读这份清单做合并（见 HTML_BOARD_RULES）。
 *
 * 只读模式（分享访客）：serve 时先注入 window.__hbteReadOnly = true——加载时照常套用已保存的
 * 替换对（访客看到板主编辑后的文字），但忽略宿主的编辑开关消息、也不写回清单。
 */
(function () {
  'use strict';
  if (window.__boardTextEditLoaded) return;
  window.__boardTextEditLoaded = true;

  var STORE = 'data/text-edits.json';
  var MAX_TEXT = 4000; // 单段可编辑文本上限（超长段多为整页容器 / 数据堆，不允许编辑防误伤布局）
  var edits = [];      // [{from, to}]，from 为进入编辑时该段的实际文本
  var editMode = false;
  var lastCount = 0;   // 最近一次进入编辑模式收进的编辑块数（回报宿主用）
  var saveTimer = null;

  var CSS = [
    // 冻结页面交互：除编辑块外全部不响应指针（hover 特效 / 点击处理一并失效）；
    // pointer-events 可继承，故编辑块自身必须显式放开
    'html.hbte-mode body *:not(.hbte-editable){pointer-events:none}',
    'html.hbte-mode .hbte-editable{pointer-events:auto}',
    // caret-color 必须显式给：渐变文字标题普遍用 background-clip:text + color:transparent，
    // 光标颜色默认跟随 color → 透明光标看得见编辑却找不到光标位置
    'html.hbte-mode .hbte-editable{outline:1px dashed rgba(37,99,235,.65);outline-offset:2px;border-radius:2px;cursor:text;caret-color:#2563eb}',
    'html.hbte-mode .hbte-editable:focus{outline:2px solid #2563eb;background:rgba(37,99,235,.06)}'
  ].join('');

  // 这些容器内的文字一律不碰：代码 / 表单控件 / 媒体 / svg 内部（svg 里塞 HTML span 会直接坏）
  var SKIP_SELECTOR = 'script,style,noscript,template,textarea,select,input,option,' +
    'iframe,canvas,video,audio,object,embed,svg,code,pre';

  // 不可见上下文不包（尊重页面自身的隐藏设计）：翻页 PPT 的隐藏页靠 opacity:0 + pointer-events:none
  // 让点击穿过，本脚本包出的编辑 span 自带 pointer-events:auto，若不跳过会把隐形页文字全部激活、
  // 层叠抢走可见页的点击（用户点可见文字却聚焦到隐形块上）。祖先链任一环节不可见即跳过
  function isVisibleChain(el) {
    while (el) {
      var cs = getComputedStyle(el);
      if (cs.display === 'none') return false;
      if (cs.visibility === 'hidden' || cs.visibility === 'collapse') return false;
      if (parseFloat(cs.opacity) === 0) return false;
      el = el.parentElement;
    }
    return true;
  }

  function appToken() {
    var parts = location.pathname.split('/');
    var i = parts.indexOf('html-app');
    return i >= 0 ? (parts[i + 1] || '') : '';
  }

  function persist() {
    if (window.__hbteReadOnly) return; // 分享访客只读：套用改动可以，写回清单不行
    var token = appToken();
    if (!token) return;
    fetch('/api/v1/ai/html-app/' + token + '/save', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({path: STORE, data: edits})
    }).catch(function () {});
  }

  function schedulePersist() {
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(persist, 400);
  }

  // ── 加载时套用已保存的替换（按清单顺序逐条，链式修改自然生效）──────────

  function applyEdits() {
    if (!edits.length || !document.body) return;
    var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
    var nodes = [];
    var n;
    while ((n = walker.nextNode())) nodes.push(n);
    nodes.forEach(function (node) {
      var text = node.nodeValue;
      if (!text) return;
      for (var i = 0; i < edits.length; i++) {
        var e = edits[i];
        if (e.from && text.indexOf(e.from) !== -1) {
          text = text.split(e.from).join(e.to);
        }
      }
      if (text !== node.nodeValue) node.nodeValue = text;
    });
  }

  // ── 编辑块收集：按文本段包 span（退出时拆掉还原）──────────────────────

  function shouldEdit(node) {
    var text = node.nodeValue;
    if (!text || !text.trim()) return false;
    if (text.length > MAX_TEXT) return false;
    var p = node.parentElement;
    if (!p) return false;
    if (p.closest(SKIP_SELECTOR)) return false;
    if (p.closest('.hbte-editable')) return false;
    if (p.closest('[contenteditable="true"]')) return false; // 页面自带的编辑器不碰
    if (!isVisibleChain(p)) return false; // 隐藏页 / 不可见块的文字不包，防隐形编辑块抢点击
    return true;
  }

  function onEditableBlur(ev) {
    if (!editMode) return;
    var el = ev.currentTarget;
    var orig = el.__hbteOrig;
    var now = el.textContent;
    if (now === orig) {
      // 改回原文 = 撤销该处替换
      for (var j = edits.length - 1; j >= 0; j--) {
        if (edits[j].from === orig) edits.splice(j, 1);
      }
      schedulePersist();
      return;
    }
    var hit = false;
    for (var i = 0; i < edits.length; i++) {
      if (edits[i].from === orig) { edits[i].to = now; hit = true; break; }
    }
    if (!hit) edits.push({from: orig, to: now});
    schedulePersist();
  }

  function enterEdit() {
    // 先取快照再改 DOM（包 span 会动树）
    var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
    var nodes = [];
    var n;
    while ((n = walker.nextNode())) nodes.push(n);
    var made = [];
    nodes.forEach(function (node) {
      if (!shouldEdit(node)) return;
      var span = document.createElement('span');
      span.className = 'hbte-editable';
      node.parentNode.insertBefore(span, node);
      span.appendChild(node);
      span.setAttribute('contenteditable', 'true');
      span.setAttribute('spellcheck', 'false');
      span.__hbteOrig = span.textContent;
      span.addEventListener('blur', onEditableBlur);
      made.push(span);
    });
    lastCount = made.length;
    if (!made.length) return 0;
    editMode = true;
    document.documentElement.classList.add('hbte-mode');
    return made.length;
  }

  function exitEdit() {
    // 先让正在编辑的块失焦提交，再拆 span 还原 DOM（normalize 把还原的文本段重新合并）
    if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
    document.querySelectorAll('.hbte-editable').forEach(function (span) {
      var parent = span.parentNode;
      if (!parent) return;
      while (span.firstChild) parent.insertBefore(span.firstChild, span);
      parent.removeChild(span);
      if (parent.normalize) parent.normalize();
    });
    editMode = false;
    document.documentElement.classList.remove('hbte-mode');
    persist();
  }

  // ── 页面冻结：编辑模式下拦截页面自身的输入处理 ─────────────────────────

  function focusSibling(cur, dir) {
    var list = Array.prototype.slice.call(document.querySelectorAll('.hbte-editable'));
    if (!list.length) return;
    var i = list.indexOf(cur);
    var next = list[(i + dir + list.length) % list.length];
    if (next && next.focus) next.focus();
  }

  function onGuardEvent(ev) {
    if (!editMode) return;
    var t = ev.target;
    var inEditable = t && t.nodeType === 1 && t.closest ? t.closest('.hbte-editable') : null;
    var type = ev.type;
    if (type === 'keydown') {
      if (inEditable) {
        if (ev.key === 'Enter') { // 单段文本编辑：Enter=提交，不允许插换行/拆结构
          ev.preventDefault();
          ev.stopPropagation();
          inEditable.blur();
          return;
        }
        if (ev.key === 'Tab') { // Tab 在编辑块间移动
          ev.preventDefault();
          ev.stopPropagation();
          focusSibling(inEditable, ev.shiftKey ? -1 : 1);
          return;
        }
        ev.stopPropagation(); // 打字/光标的默认行为保留，页面快捷键收不到
      } else {
        ev.stopPropagation();
        if (!ev.ctrlKey && !ev.metaKey && !ev.altKey) ev.preventDefault(); // 空格/方向键滚页等一律冻住
      }
      return;
    }
    if (type === 'paste') {
      if (inEditable && ev.clipboardData) { // 强制纯文本粘贴，防把外部 HTML 结构带进来
        ev.preventDefault();
        ev.stopPropagation();
        var plain = ev.clipboardData.getData('text/plain');
        if (plain) document.execCommand('insertText', false, plain);
      }
      return;
    }
    // keyup / keypress / 鼠标 / 滚轮 / 触摸：一律不给页面自身的监听看到；
    // 不设 preventDefault——编辑块内选择/光标/滚动等浏览器默认行为不受影响
    ev.stopPropagation();
  }

  ['click', 'dblclick', 'mousedown', 'mouseup', 'pointerdown', 'pointerup',
   'mousemove', 'mouseover', 'mouseout', 'contextmenu',
   'keydown', 'keyup', 'keypress', 'paste', 'wheel',
   'touchstart', 'touchend', 'touchmove'].forEach(function (t) {
    window.addEventListener(t, onGuardEvent, true);
  });

  // ── 宿主通信（外框顶栏「编辑文字」按钮驱动）─────────────────────────────

  function reply(ev) {
    if (ev.source && ev.source.postMessage) {
      ev.source.postMessage({type: 'hbte-edit-state', editing: editMode, editableCount: lastCount}, ev.origin || '*');
    }
  }

  window.addEventListener('message', function (ev) {
    var d = ev.data;
    if (!d || typeof d !== 'object' || d.type !== 'hbte-toggle-edit') return;
    if (window.__hbteReadOnly) return; // 分享访客：编辑开关一律忽略（只读套用已保存改动）
    if (editMode) {
      exitEdit();
    } else if (enterEdit() === 0) {
      alert('这个页面上没有可编辑的静态文本（程序动态渲染的文字请在应用自己的编辑入口里改）');
    }
    reply(ev);
  });

  // ── 启动 ───────────────────────────────────────────────────────────────────

  function init() {
    if (!document.body) {
      // 注入位置异常（无 body）时退化为 DOM 就绪后再套用
      document.addEventListener('DOMContentLoaded', init);
      return;
    }
    var style = document.createElement('style');
    style.textContent = CSS;
    document.head.appendChild(style);
    fetch(STORE).then(function (r) {
      return r.ok ? r.json() : null;
    }).then(function (data) {
      if (Object.prototype.toString.call(data) !== '[object Array]') return;
      edits = data.filter(function (e) {
        return e && typeof e.from === 'string' && typeof e.to === 'string' && e.from;
      });
      applyEdits();
    }).catch(function () {});
  }

  init();
})();
