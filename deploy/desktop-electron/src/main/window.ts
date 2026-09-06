// 主窗口：秒显由编排层先载 loading 页保证；窗口只加载后端 http 页，不暴露任何 Node 能力。
import { BrowserWindow } from "electron";
import * as fs from "fs";
import { appIconPath } from "./paths";

export function createMainWindow(): BrowserWindow {
  const icon = appIconPath();
  const win = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 960,
    minHeight: 600,
    show: false,
    autoHideMenuBar: true,
    icon: fs.existsSync(icon) ? icon : undefined,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
  });
  win.once("ready-to-show", () => win.show());
  return win;
}
