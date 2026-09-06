/*
 * 应用制作系统注入脚本——「互动桥」window.AppBridge
 * （由 html_app.py::serve_file 在响应 html 时注入页面闭合标签前，不随文件落盘；无条件注入所有页面）
 *
 * 目的：让应用页面与 agent 双向互动，而不必手写 fetch / SSE / postMessage 样板。
 *   - 行数据读写：loadRows / putRows / delRows（封装 /rows 通道，自动解信封、token 从 URL 取）
 *   - 变更订阅：onChange（EventSource /events，300ms 合并去抖，断线原生重连）
 *   - 反向呼叫 agent：sendToChat（postMessage 给宿主 → 宿主经对话通道触发一个 agent 回合）
 *   - 身份：whoami（当前是板主还是访客）
 *
 * 设计约束：
 *   - 首屏正确性由「启动先 loadRows」保证（见 HTML_BOARD_RULES 第 3 条）；onChange 只是增量通道，
 *     不做兜底轮询——断线期间的事件丢失，靠下一次变更或页面重开补齐。
 *   - 回声不去重：自己 putRows 后本地可立即渲染，随后收到自身变更回声再幂等刷新一次，可接受。
 *   - sendToChat 在分享访客页（无宿主对话通道）会 1500ms 超时静默降级 {ok:false,reason:'no-channel'}；
 *     内置 3s 冷却防页面定时器刷爆回合（reason:'cooldown'）；宿主忙时回 {ok:false,reason:'busy'}。
 *   - 本脚本内容不得含闭合 script 标签字样（裸拼注入，见 html_app.py 注入注释）。
 */
(function () {
  'use strict';
  if (window.__hbteAppBridgeLoaded) return;
  window.__hbteAppBridgeLoaded = true;

  var SEND_COOLDOWN_MS = 3000;   // sendToChat 冷却（防刷爆 agent 回合）
  var SEND_ACK_TIMEOUT_MS = 1500; // 等宿主 ack 超时 → no-channel（分享页无宿主监听的合法形态）
  var CHANGE_DEBOUNCE_MS = 300;   // onChange 合并去抖窗口

  function appToken() {
    var parts = location.pathname.split('/');
    var i = parts.indexOf('html-app');
    return i >= 0 ? (parts[i + 1] || '') : '';
  }

  var BASE = '/api/v1/ai/html-app/' + appToken();

  // 解平台信封：{code,msg,data}，code!=='0000' 抛错（携带 msg 供页面提示）
  function unwrap(resp) {
    return resp.json().then(function (env) {
      if (!env || env.code !== '0000') {
        var e = new Error((env && env.msg) || '请求失败');
        e.code = env && env.code;
        throw e;
      }
      return env.data;
    });
  }

  function getJSON(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return unwrap(r);
    });
  }

  function postJSON(url, body) {
    return fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body)
    }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return unwrap(r);
    });
  }

  function qs(params) {
    var out = [];
    for (var k in params) {
      if (params[k] === undefined || params[k] === null) continue;
      out.push(encodeURIComponent(k) + '=' + encodeURIComponent(params[k]));
    }
    return out.length ? ('?' + out.join('&')) : '';
  }

  // ── 行数据读写 ──────────────────────────────────────────────────────────────

  function whoami() {
    return getJSON(BASE + '/whoami');
  }

  // loadRows(tbl, {prefix, limit, offset}) → {rows:[{key,data,updatedBy,updatedAt}], more}
  function loadRows(tbl, opts) {
    opts = opts || {};
    return getJSON(BASE + '/rows' + qs({tbl: tbl, prefix: opts.prefix, limit: opts.limit, offset: opts.offset}));
  }

  // putRows(tbl, rows) → {written}；rows = [{key, data}]（key 为字符串）
  function putRows(tbl, rows) {
    return postJSON(BASE + '/rows/put', {tbl: tbl, rows: rows});
  }

  // delRows(tbl, keys) → {deleted}；keys = [字符串]
  function delRows(tbl, keys) {
    return postJSON(BASE + '/rows/del', {tbl: tbl, keys: keys});
  }

  // ── SSE 变更订阅 ────────────────────────────────────────────────────────────
  // onChange(cb) → unsubscribe。cb 收到合并后的 {type:'rows', tbls:[...], rev, by}。
  // 300ms 去抖：窗口内多个事件合并（tbls 取并集、rev 取最大、by 取最后一个）。
  // 断线靠 EventSource 原生重连，不做兜底轮询。
  function onChange(cb) {
    if (typeof cb !== 'function') return function () {};
    var es;
    try {
      es = new EventSource(BASE + '/events');
    } catch (e) {
      return function () {};
    }
    var pending = null; // {tbls:Set-like obj, rev, by}
    var timer = null;

    function flush() {
      timer = null;
      if (!pending) return;
      var tbls = [];
      for (var k in pending.tbls) { if (pending.tbls[k]) tbls.push(k); }
      var ev = {type: 'rows', tbls: tbls, rev: pending.rev, by: pending.by};
      pending = null;
      try { cb(ev); } catch (e) { /* 页面回调异常不影响桥 */ }
    }

    function merge(ev) {
      if (!pending) {
        pending = {tbls: {}, rev: ev.rev || 0, by: ev.by || 'page'};
      }
      var list = ev.tbls || [];
      for (var i = 0; i < list.length; i++) pending.tbls[list[i]] = true;
      if (typeof ev.rev === 'number' && ev.rev > pending.rev) pending.rev = ev.rev;
      if (ev.by) pending.by = ev.by;
      if (!timer) timer = setTimeout(flush, CHANGE_DEBOUNCE_MS);
    }

    // 命名事件 rows（后端 event: rows）
    es.addEventListener('rows', function (e) {
      var ev;
      try { ev = JSON.parse(e.data); } catch (err) { return; }
      if (ev && ev.type === 'rows') merge(ev);
    });
    // hello 首帧与未知事件忽略（通道就绪信号，无需回调）
    es.onerror = function () { /* EventSource 自动重连，无需处理 */ };

    return function () {
      if (timer) clearTimeout(timer);
      try { es.close(); } catch (e) {}
    };
  }

  // ── 反向呼叫 agent ──────────────────────────────────────────────────────────
  // sendToChat(text) → Promise<{ok, reason?}>。宿主（画布外框）监听 hbte-app-chat，
  // 经对话通道把 text 作为一条用户消息触发一个 agent 回合，并回 hbte-app-chat-ack。
  var sendSeq = 0;
  var lastSendAt = 0;

  function sendToChat(text) {
    text = (text == null ? '' : String(text)).slice(0, 2000);
    if (!text.trim()) return Promise.resolve({ok: false, reason: 'empty'});
    var now = Date.now();
    if (now - lastSendAt < SEND_COOLDOWN_MS) {
      return Promise.resolve({ok: false, reason: 'cooldown'});
    }
    // 无宿主（顶层窗口即页面自身，例如直接打开托管 URL）→ 静默降级
    if (!window.parent || window.parent === window) {
      return Promise.resolve({ok: false, reason: 'no-channel'});
    }
    lastSendAt = now;
    var id = 'ac' + (++sendSeq) + '_' + now;

    return new Promise(function (resolve) {
      var settled = false;
      function done(result) {
        if (settled) return;
        settled = true;
        window.removeEventListener('message', onMsg);
        clearTimeout(timer);
        resolve(result);
      }
      function onMsg(ev) {
        // 只认父窗口回发、且 id 匹配的 ack
        if (ev.source !== window.parent) return;
        var d = ev.data;
        if (!d || typeof d !== 'object' || d.type !== 'hbte-app-chat-ack' || d.id !== id) return;
        done({ok: !!d.ok, reason: d.reason || (d.ok ? undefined : 'rejected')});
      }
      var timer = setTimeout(function () { done({ok: false, reason: 'no-channel'}); }, SEND_ACK_TIMEOUT_MS);
      window.addEventListener('message', onMsg);
      // dev 跨源 / prod 同源两态，消息只含用户可见文本 → targetOrigin '*' 可接受（宿主侧有 ev.source 校验）
      try {
        window.parent.postMessage({type: 'hbte-app-chat', id: id, text: text}, '*');
      } catch (e) {
        done({ok: false, reason: 'no-channel'});
      }
    });
  }

  window.AppBridge = {
    token: appToken(),
    whoami: whoami,
    loadRows: loadRows,
    putRows: putRows,
    delRows: delRows,
    onChange: onChange,
    sendToChat: sendToChat
  };
})();
