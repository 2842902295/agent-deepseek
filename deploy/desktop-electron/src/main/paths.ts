// 后端载荷（暂存应用树）路径单一出口 —— 开发/打包路径差异（头号坑）的防线。
// - 打包态：extraResources 把暂存树整体放到 resources/backend（见 electron-builder.yml）
// - 开发态：env CESI_BACKEND_DIR 指向 desktop_dist 下暂存树（pnp dev 冒烟用）
import * as crypto from "crypto";
import { app } from "electron";
import * as path from "path";

export function backendRoot(): string {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, "backend");
  }
  const dir = process.env.CESI_BACKEND_DIR;
  if (!dir) {
    throw new Error("开发模式需设置环境变量 CESI_BACKEND_DIR 指向暂存应用树（desktop_dist/<应用名>）");
  }
  return dir;
}

export function envFilePath(): string {
  return path.join(backendRoot(), ".env");
}
// 可写数据根：安装目录可能落在 Program Files 等写保护位置（2026-09 实测：
// userData 建不出来 → 单实例锁拿不到 → 启动即静默退出，窗口都不出现）。
// 因此壳自身的写面（userData / electron.log / backend.log）一律放 %APPDATA%，
// 子目录取安装路径哈希 → 单实例锁仍按安装目录隔离、多副本可共存
// （保留 launcher.pyw 按安装路径 crc32 互斥体的语义）。后端自身的写面
// （日志/.agent_workspace/redis）仍在载荷目录内，不可写时由编排层探针拦截。
export function dataRoot(): string {
  const hash = crypto.createHash("md5").update(backendRoot().toLowerCase()).digest("hex").slice(0, 12);
  return path.join(app.getPath("appData"), "cesi-desktop-shell", hash);
}
export function logDir(): string {
  return path.join(dataRoot(), "logs");
}
export function electronLogPath(): string {
  return path.join(logDir(), "electron.log");
}
export function backendLogPath(): string {
  return path.join(logDir(), "backend.log");
}
export function loadingHtmlPath(): string {
  return path.join(backendRoot(), "loading.html");
}
export function appIconPath(): string {
  return path.join(backendRoot(), "favicon.ico");
}
export function buildInfoPath(): string {
  return path.join(backendRoot(), "build-info.json");
}
export function redisDir(): string {
  return path.join(backendRoot(), "redis");
}
export function redisServerExe(): string {
  return path.join(redisDir(), "redis-server.exe");
}
export function redisCliExe(): string {
  return path.join(redisDir(), "redis-cli.exe");
}
export function redisConfPath(): string {
  return path.join(redisDir(), "redis.windows.conf");
}
export function pythonExe(): string {
  return path.join(backendRoot(), "runtime", "python.exe");
}
export function runPyPath(): string {
  return path.join(backendRoot(), "run.py");
}
export function userDataDir(): string {
  return path.join(dataRoot(), "userData");
}
