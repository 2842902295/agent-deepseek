// 简易追加日志（对齐 launcher.pyw 的 _log：带时间戳追加）。
// 日志目录在 %APPDATA% 可写数据根下（见 paths.ts::dataRoot），主进程日志与 backend.log 同目录。
import * as fs from "fs";
import * as path from "path";

let logFile: string | null = null;

export function initLogger(file: string): void {
  logFile = file;
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true });
  } catch {
    // 日志目录创建失败不致命
  }
}

export function log(line: string): void {
  const stamped = `${new Date().toISOString().replace("T", " ").slice(0, 19)} ${line}`;
  console.log(stamped);
  if (!logFile) return;
  try {
    fs.appendFileSync(logFile, stamped + "\n", "utf-8");
  } catch {
    // 日志写失败不影响主流程
  }
}
