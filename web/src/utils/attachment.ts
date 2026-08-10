/**
 * 附件 / 文件通用工具：类型判断、格式化、下载。
 * 由 qa-glass 对话页与工作流画板共享（attachment-preview-modal.vue 亦基于此）。
 */

/** 文件字节数 → 人类可读尺寸 */
export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

/**
 * 剥掉展示名尾部的「描述性括号后缀」：agent 登记产物常写成 "player-concept.png（新国风插画）"，
 * 带着括号尾去 split('.') 取扩展名会得到 "png（新国风插画）" → 所有类型判断全落空。
 * 支持全角 / 半角括号、多段连缀；只用于扩展名解析，展示名与下载名不受影响。
 */
export function stripNameTail(name: string): string {
  return String(name || '')
    .replace(/(?:\s*[（(][^（）()]*[）)])+\s*$/, '')
    .trim();
}

/** 提取扩展名（小写、不带点；无名 / 无扩展名返回空串）——扩展名解析的唯一真相 */
export function extractExt(name: string): string {
  const base = stripNameTail(name);
  const i = base.lastIndexOf('.');
  if (i <= 0 || i === base.length - 1) return '';
  return base.slice(i + 1).toLowerCase();
}

/** 扩展名短标签（>4 字符截断，用于彩色徽章） */
export function fileExt(name: string): string {
  const ext = extractExt(name);
  return ext.length > 4 ? ext.slice(0, 4) : ext || '?';
}

/** 扩展名分组（驱动徽章配色：af-ext-{group}） */
export function fileExtGroup(name: string): string {
  const ext = extractExt(name);
  if (['pdf'].includes(ext)) return 'pdf';
  if (['doc', 'docx', 'rtf', 'odt'].includes(ext)) return 'doc';
  if (['xls', 'xlsx', 'csv', 'tsv'].includes(ext)) return 'sheet';
  if (['ppt', 'pptx'].includes(ext)) return 'ppt';
  if (['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'bmp'].includes(ext)) return 'img';
  if (['zip', 'rar', '7z', 'tar', 'gz'].includes(ext)) return 'zip';
  if (['mp4', 'avi', 'mov', 'mkv', 'mp3', 'wav', 'flac'].includes(ext)) return 'media';
  if (['py', 'js', 'ts', 'java', 'go', 'rs', 'c', 'cpp', 'h'].includes(ext)) return 'code';
  if (['txt', 'md', 'json', 'xml', 'yaml', 'yml', 'toml'].includes(ext)) return 'text';
  return 'other';
}

export function isImageFile(name: string): boolean {
  return ['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'bmp'].includes(extractExt(name));
}

export function isMarkdownFile(name: string): boolean {
  return ['md', 'markdown', 'mdx'].includes(extractExt(name));
}

export function isCsvFile(name: string): boolean {
  return ['csv', 'tsv'].includes(extractExt(name));
}

export function isHtmlFile(name: string): boolean {
  return ['html', 'htm'].includes(extractExt(name));
}

export type OfficeKind = 'docx' | 'xlsx' | 'pdf' | 'pptx';

export function getOfficeKind(name: string): OfficeKind | null {
  const ext = extractExt(name);
  if (ext === 'docx') return 'docx';
  if (['xlsx', 'xls'].includes(ext)) return 'xlsx';
  if (ext === 'pdf') return 'pdf';
  if (ext === 'pptx') return 'pptx';
  return null;
}

export function isOfficePreviewable(name: string): boolean {
  return getOfficeKind(name) !== null;
}

export function isVideoFile(name: string): boolean {
  return ['mp4', 'webm', 'mov', 'ogg', 'mkv', 'avi'].includes(extractExt(name));
}

export function isAudioFile(name: string): boolean {
  return ['mp3', 'wav', 'flac', 'aac', 'm4a'].includes(extractExt(name));
}

/** 文件名清洗：去掉非法字符与多余空白，限长 80 */
export function sanitizeFilename(name: string): string {
  return name
    .replace(/[\\/:*?"<>|]/g, '_')
    .replace(/\s+/g, '_')
    .slice(0, 80);
}

/** 触发浏览器下载一个 Blob */
export function downloadBlob(filename: string, blob: Blob) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** 触发浏览器下载一段文本 */
export function downloadText(filename: string, text: string, mime = 'text/plain;charset=utf-8') {
  downloadBlob(filename, new Blob([text], {type: mime}));
}

/**
 * xlsx 预览兼容处理（针对 ClosedXML 等 .NET 库生成的文件）：
 * 这类文件的 XML 元素全带命名空间前缀（<x:c>/<x:row>…），文本全为内联字符串（t="inlineStr"，
 * 包内没有 sharedStrings.xml）；而 @vue-office/excel 内置的 exceljs 解析器按无前缀标签名精确匹配
 * （node.name === 'c'）→ 整表解析为空，预览一片空白。
 * 判定：zip 内条目名不压缩存储，直接在字节序列里搜 'sharedStrings.xml' 即可——Excel / WPS 正常
 * 保存的文件恒含此条目；缺失即上述异常文件，用 SheetJS 读后重写为标准 xlsx（sharedStrings +
 * 无前缀）再交给 vue-office。解析失败原样返回兜底（不阻断既有行为）。
 * 注：xlsx 走动态 import 独立 chunk，仅命中异常文件时才拉包，不影响常规文件与首屏。
 */
export async function standardizeXlsxForPreview(buf: ArrayBuffer): Promise<ArrayBuffer> {
  if (bufferContainsAscii(buf, 'sharedStrings.xml')) return buf;
  try {
    const XLSX = await import('xlsx');
    const wb = XLSX.read(new Uint8Array(buf), {type: 'array'});
    return XLSX.write(wb, {type: 'array', bookType: 'xlsx', bookSST: true}) as ArrayBuffer;
  } catch {
    return buf;
  }
}

/** 字节序列是否含 ASCII 子串（首字符跳跃朴素匹配，zip 条目名探测用） */
function bufferContainsAscii(buf: ArrayBuffer, needle: string): boolean {
  const bytes = new Uint8Array(buf);
  const n = needle.length;
  if (!n || n > bytes.length) return false;
  const first = needle.charCodeAt(0);
  const end = bytes.length - n;
  outer: for (let i = 0; i <= end; i++) {
    if (bytes[i] !== first) continue;
    for (let j = 1; j < n; j++) {
      if (bytes[i + j] !== needle.charCodeAt(j)) continue outer;
    }
    return true;
  }
  return false;
}
