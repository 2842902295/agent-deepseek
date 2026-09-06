/**
 * 工具卡格式化公共函数
 *
 * 原实现在 index.vue 内（服务旧「执行痕迹」折叠区）；过程时间线组件
 * （process-timeline.vue）与主页面共用，抽到此处。逻辑一字未改。
 */

export type ResultNode =
  | { kind: 'text'; value: string }
  | { kind: 'kv'; pairs: Array<{ key: string; value: string }> }
  | { kind: 'list'; items: Array<{ label: string; line: string }> };

/** 工具入参 → 键值对列表（参数展开区用） */
export function argsToParams(args: Record<string, unknown>): Array<{ key: string; value: string }> {
  return Object.entries(args).map(([k, v]) => ({
    key: k,
    value: typeof v === 'string' ? v : prettyVal(v)
  }));
}

// 完整递归展开，无 JSON 符号，多行 OK（用在 kv 值列）
export function prettyVal(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  if (Array.isArray(v)) {
    if (!v.length) return '(空)';
    if (v.every(x => typeof x !== 'object' || x === null)) {
      return v.map(x => prettyVal(x)).join(', ');
    }
    return v.map((item, i) => `${i + 1}. ${prettyLine(item)}`).join('\n');
  }
  if (typeof v === 'object') {
    const entries = Object.entries(v as Record<string, unknown>);
    if (!entries.length) return '(空)';
    return entries.map(([k, val]) => {
      const str = prettyVal(val);
      return str.includes('\n')
        ? `${k}:\n  ${str.split('\n').join('\n  ')}`
        : `${k}: ${str}`;
    }).join('\n');
  }
  return String(v);
}

// 紧凑单行摘要（用在 list 每行）
export function prettyLine(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'string') return v.length > 80 ? v.slice(0, 80) + '…' : v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  if (Array.isArray(v)) return v.length ? v.map(x => prettyLine(x)).join(', ') : '(空)';
  if (typeof v === 'object') {
    return Object.entries(v as Record<string, unknown>)
      .map(([k, val]) => `${k}: ${prettyLine(val)}`)
      .join('  ');
  }
  return String(v);
}

/** 工具结果文本 → 结构化节点（JSON 对象→键值对；数组→列表；其余→纯文本） */
export function parseResultContent(text: string): ResultNode {
  if (!text?.trim()) return { kind: 'text', value: '(无返回内容)' };
  try {
    const parsed = JSON.parse(text);
    if (typeof parsed === 'string') return { kind: 'text', value: parsed };
    if (Array.isArray(parsed)) {
      if (!parsed.length) return { kind: 'text', value: '(空列表)' };
      return {
        kind: 'list',
        items: parsed.slice(0, 30).map((item, i) => ({
          label: `${i + 1}`,
          line: prettyLine(item)
        }))
      };
    }
    if (typeof parsed === 'object') {
      return {
        kind: 'kv',
        pairs: Object.entries(parsed as Record<string, unknown>).map(([k, v]) => ({
          key: k,
          value: prettyVal(v)
        }))
      };
    }
    return { kind: 'text', value: String(parsed) };
  } catch {
    return { kind: 'text', value: text };
  }
}

/** 工具入参单行摘要（折叠行用） */
export function argsSummary(args: Record<string, unknown>): string {
  const entries = Object.entries(args);
  if (!entries.length) return '(无参数)';
  const first = entries.find(([, v]) => typeof v === 'string') || entries[0];
  const val = prettyLine(first[1]);
  return val.length > 60 ? val.slice(0, 60) + '…' : val;
}

/** 工具结果单行摘要（折叠行用） */
export function resultSummary(text: string): string {
  if (!text?.trim()) return '(无返回内容)';
  try {
    const parsed = JSON.parse(text);
    const s = prettyLine(parsed);
    return s.length > 60 ? s.slice(0, 60) + '…' : s;
  } catch {
    return text.length > 60 ? text.slice(0, 60) + '…' : text;
  }
}
