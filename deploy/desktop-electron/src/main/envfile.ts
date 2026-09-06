// 安装目录 .env 读写（对齐 launcher.pyw 的 read_port / update_env_port 语义）。
// 换端口回写额外同步 PUBLIC_BASE_URL —— 修复现状只回写 DESKTOP_PORT 的遗漏。
import * as fs from "fs";
import { log } from "./logger";
import { envFilePath } from "./paths";

export function readEnvKey(key: string): string | null {
  try {
    const lines = fs.readFileSync(envFilePath(), "utf-8").split(/\r?\n/);
    for (const line of lines) {
      const s = line.trim();
      if (s.startsWith(key + "=")) {
        return s.slice(key.length + 1).trim();
      }
    }
  } catch {
    // .env 缺失/读不了走默认值
  }
  return null;
}

/** 从 .env 读 DESKTOP_PORT，缺失/异常回落 9999（对齐 launcher.read_port） */
export function readDesktopPort(): number {
  const v = readEnvKey("DESKTOP_PORT");
  if (v !== null && /^\d+$/.test(v)) {
    return parseInt(v, 10);
  }
  return 9999;
}

/** 把新端口回写 .env：DESKTOP_PORT 与 PUBLIC_BASE_URL 同步（下次启动沿用，URL 稳定） */
export function updateEnvPort(port: number): void {
  try {
    const lines = fs.readFileSync(envFilePath(), "utf-8").split(/\r?\n/);
    let sawPort = false;
    let sawBase = false;
    for (let i = 0; i < lines.length; i++) {
      const s = lines[i].trim();
      if (s.startsWith("DESKTOP_PORT=")) {
        lines[i] = `DESKTOP_PORT=${port}`;
        sawPort = true;
      } else if (s.startsWith("PUBLIC_BASE_URL=")) {
        lines[i] = `PUBLIC_BASE_URL=http://localhost:${port}`;
        sawBase = true;
      }
    }
    if (!sawPort) lines.push(`DESKTOP_PORT=${port}`);
    if (!sawBase) lines.push(`PUBLIC_BASE_URL=http://localhost:${port}`);
    fs.writeFileSync(envFilePath(), lines.join("\n") + "\n", "utf-8");
  } catch (e) {
    log(`updateEnvPort failed: ${String(e)}`);
  }
}
