/**
 * 把 markdown 字符串拆成"按段编辑"的块列表，再拼回去。
 *
 * 设计取舍（与需求对齐）：
 *  - 块粒度："看到一段就当一块"——标题、段落、整段列表、整段引用、围栏代码、分隔线、表格各自一块
 *  - 渲染态用 marked 渲染单块 → 看不见 md 符号
 *  - 编辑态：渲染好的 div 自身可 contenteditable，编辑完用 turndown 把 HTML 转回 md
 *  - 块之间永远用单空行拼接，避免历史里多空行被反复"规范化"导致 diff 噪音
 */

import TurndownService from 'turndown';

export type MdBlockType =
  | 'heading'
  | 'paragraph'
  | 'list'
  | 'quote'
  | 'code'
  | 'hr'
  | 'table'
  | 'artifact';

export interface MdBlock {
  /** 稳定 id：同次解析内 stable，跨次解析后会重新分配（仅用于 v-for key） */
  id: string;
  type: MdBlockType;
  /** 该块原始 markdown 文本（不含块间空行） */
  source: string;
  /** artifact 块的产物 ID */
  artifactId?: number;
}

let _seq = 0;
function nextId(): string {
  _seq = (_seq + 1) >>> 0;
  return `b${_seq}`;
}

function isFenceLine(line: string): string | null {
  const m = /^(\s{0,3})(`{3,}|~{3,})(.*)$/.exec(line);
  return m ? m[2] : null;
}

function classify(lines: string[]): MdBlockType {
  const first = lines[0] || '';

  if (/^\[artifact:-?\d+\]\s*$/.test(first)) return 'artifact';
  if (/^(\s{0,3})(-{3,}|_{3,}|\*{3,})\s*$/.test(first)) return 'hr';
  if (/^\s{0,3}#{1,6}\s+/.test(first)) return 'heading';
  if (/^\s{0,3}>/.test(first)) return 'quote';
  if (/^\s{0,3}([-+*]|\d+[.)])\s+/.test(first)) return 'list';

  if (lines.length >= 2 && /\|/.test(first) && /^\s*\|?\s*:?-{2,}/.test(lines[1])) {
    return 'table';
  }

  return 'paragraph';
}

export function parseBlocks(md: string): MdBlock[] {
  const text = (md || '').replace(/\r\n/g, '\n');
  if (!text.trim()) return [];

  const lines = text.split('\n');
  const blocks: MdBlock[] = [];

  let buf: string[] = [];
  const flush = () => {
    while (buf.length && buf[0].trim() === '') buf.shift();
    while (buf.length && buf[buf.length - 1].trim() === '') buf.pop();
    if (!buf.length) return;
    const type = classify(buf);
    const source = buf.join('\n');
    const block: MdBlock = {id: nextId(), type, source};
    if (type === 'artifact') {
      const m = /^\[artifact:(-?\d+)\]/.exec(source);
      if (m) block.artifactId = Number(m[1]);
    }
    blocks.push(block);
    buf = [];
  };

  let i = 0;
  while (i < lines.length) {
    const line = lines[i];

    const fence = isFenceLine(line);
    if (fence) {
      flush();
      const codeLines = [line];
      i++;
      while (i < lines.length) {
        codeLines.push(lines[i]);
        if (isFenceLine(lines[i]) === fence) {
          i++;
          break;
        }
        i++;
      }
      blocks.push({id: nextId(), type: 'code', source: codeLines.join('\n')});
      continue;
    }

    if (line.trim() === '') {
      flush();
      i++;
      continue;
    }

    buf.push(line);
    i++;
  }
  flush();

  return blocks;
}

export function stringifyBlocks(blocks: MdBlock[]): string {
  return blocks.map((b) => b.source).join('\n\n');
}

// ── HTML → Markdown（仅处理单块） ─────────────────────────────────
const turndown = new TurndownService({
  headingStyle: 'atx',
  bulletListMarker: '-',
  codeBlockStyle: 'fenced',
  emDelimiter: '*',
});

// contenteditable 在按 Enter 时插入的 <div>/<br> 处理：
// 把孤立的 <div> 当成换行（段落中 shift+enter）
turndown.addRule('div-as-br', {
  filter: (node) => node.nodeName === 'DIV',
  replacement: (content) => `${content}\n`,
});

/** 把渲染态 div 的 innerHTML 反转回 md 源码。空白结果返回空串。 */
export function htmlToMarkdown(html: string): string {
  if (!html) return '';
  // contenteditable 常会留下 &nbsp;，先归一化
  const cleaned = html.replace(/ /g, ' ');
  const md = turndown.turndown(cleaned);
  return md.replace(/\n{3,}/g, '\n\n').trimEnd();
}

