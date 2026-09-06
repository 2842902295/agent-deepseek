// 启动编排：逐条对齐 deploy/desktop/launcher.pyw 语义。
// 读端口 → ATTACH 或换口 → 秒显 loading → redis → backend → 就绪切应用页。
import * as fs from "fs";
import * as path from "path";
import { app, BrowserWindow, dialog, shell } from "electron";
import { readDesktopPort, updateEnvPort } from "./envfile";
import { log } from "./logger";
import { backendLogPath, backendRoot, loadingHtmlPath } from "./paths";
import { findFreePort, pidByPort, portAlive, portFree } from "./ports";
import { APP_WAIT_SECONDS, registerAttachedPid, startBackend, startRedisIfNeeded, waitBackendReady } from "./services";

/**
 * 安装目录可写性探针：后端日志 / .agent_workspace / redis / 换口回写 .env 都要写载荷目录，
 * 装在 Program Files 等写保护位置时普通用户进程全部失败。不可写 → 弹窗指引重装后退出，
 * 绝不静默（2026-09 实测事故：用户装进 Program Files，点启动零反应）。
 * ATTACH 路径不写载荷目录，不受此限。
 */
function ensureInstallDirWritable(): boolean {
  const probe = path.join(backendRoot(), ".electron_write_test");
  try {
    fs.writeFileSync(probe, "1");
    fs.unlinkSync(probe);
    return true;
  } catch (e) {
    log(`install dir not writable: ${String(e)}`);
    dialog.showErrorBox(
      "安装目录不可写",
      "应用被安装到了受系统保护的目录（如 Program Files），当前用户无权写入，后端服务无法运行。\n\n" +
        "请在 Windows 设置 → 应用中卸载本程序后重新安装：\n" +
        "安装时保持默认位置（当前用户目录下的 Programs 文件夹），或选择任意当前用户可写的目录。\n\n" +
        `当前安装目录：${backendRoot()}`
    );
    return false;
  }
}

export async function bootstrap(win: BrowserWindow): Promise<void> {
  const port = readDesktopPort();

  if (await portAlive(port)) {
    // ATTACH：服务已在跑（上次未正常关窗 / 手动起的）→ 直接进应用并接管生命周期
    log(`attach: port=${port} already alive`);
    const pid = await pidByPort(port);
    if (pid !== null) registerAttachedPid(pid);
    await loadApp(win, port);
    return;
  }

  if (!ensureInstallDirWritable()) {
    app.quit();
    return;
  }

  let finalPort = port;
  if (!(await portFree(finalPort))) {
    // 首选端口被其他程序占用 → 自动换空闲端口并回写 .env（不强制占端口）
    finalPort = await findFreePort(finalPort + 1);
    updateEnvPort(finalPort);
    log(`port ${port} occupied by another program, switched to ${finalPort}`);
  } else {
    log(`start: preferred port=${finalPort}`);
  }

  // 秒显：先载本地 loading 页（页面自带 JS 探测端口、就绪原地跳转，零改动复用）
  await loadLoading(win, finalPort);

  await startRedisIfNeeded();
  startBackend();

  const ready = await waitBackendReady(finalPort);
  if (!ready) {
    log(`backend not ready in ${APP_WAIT_SECONDS}s`);
    dialog.showErrorBox("启动超时", `后端服务未能在 ${APP_WAIT_SECONDS} 秒内就绪。\n即将为你打开后端日志（${backendLogPath()}），请携日志联系维护人员。`);
    void shell.openPath(backendLogPath());
    return;
  }
  log(`backend ready on port ${finalPort}`);
  await loadApp(win, finalPort);
}

function loadLoading(win: BrowserWindow, port: number): Promise<void> {
  // loading.html 是 Edge 壳既有资产，零改动复用：端口经 query 传入
  return win.loadFile(loadingHtmlPath(), { query: { port: String(port) } });
}

async function loadApp(win: BrowserWindow, port: number): Promise<void> {
  if (win.isDestroyed()) return;
  // loading 页 JS 会自行 location.replace 跳转；仅当窗口还停在 loading 时才切页，避免重复刷新
  const current = win.webContents.getURL();
  if (current === "" || current.startsWith("file:")) {
    await win.loadURL(`http://127.0.0.1:${port}/`);
  }
}
