// Electron 壳入口：单实例锁 → 建窗 → 启动编排 → 退出钩子（停服务）。
// 阶段 1 与 Edge 壳功能对等：关窗 = 退出并停服务；阶段 2 托盘常驻改 close 行为。
import { app, dialog } from "electron";
import type { BrowserWindow } from "electron";
import { initLogger, log } from "./logger";
import { bootstrap } from "./orchestrator";
import { backendRoot, electronLogPath, userDataDir } from "./paths";
import { stopAllServices } from "./services";
import { createMainWindow } from "./window";

// userData 跟随后端载荷目录 → 单实例锁按安装目录生效，多副本可共存
// （对齐 launcher.pyw 按安装路径 crc32 互斥体的语义）
app.setPath("userData", userDataDir());

let mainWindow: BrowserWindow | null = null;

if (!app.requestSingleInstanceLock()) {
  // 同安装目录已有实例在跑：静默退出（已有实例窗口会被 second-instance 聚焦）
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow === null) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
  });

  void app.whenReady().then(async () => {
    initLogger(electronLogPath());
    log(`app ready (electron ${process.versions.electron}), backend: ${backendRoot()}`);
    mainWindow = createMainWindow();
    mainWindow.on("closed", () => {
      mainWindow = null;
    });
    try {
      await bootstrap(mainWindow);
    } catch (e) {
      // 启动编排异常不允许静默：窗口会停在 loading 页，用户无从排查
      log(`bootstrap error: ${String(e)}`);
      dialog.showErrorBox("启动失败", `启动过程发生异常：${String(e)}\n\n详细日志：${electronLogPath()}`);
    }
  });

  // 阶段 1：关窗 = 退出并停服务（对齐现状「关窗即停」）；阶段 2 改托盘常驻 no-op
  app.on("window-all-closed", () => {
    app.quit();
  });

  // 退出钩子：同步杀完 backend/redis 进程树才退出（before-quit 内全同步）
  app.on("before-quit", () => {
    stopAllServices();
  });
}
