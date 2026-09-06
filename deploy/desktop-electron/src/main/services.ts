// Redis / Python 后端子进程的起停。
// 红线：退出钩子必须显式杀进程树（Electron #9862 残留进程坑），且绝不误伤目录外进程
// （杀前按可执行路径前缀校验，复用 launcher.pyw 的 PowerShell ExecutablePath.StartsWith 逻辑）。
import { spawn, spawnSync } from "child_process";
import * as fs from "fs";
import { log } from "./logger";
import { backendRoot, backendLogPath, logDir, pythonExe, redisCliExe, redisConfPath, redisDir, redisServerExe, runPyPath } from "./paths";
import { portAlive } from "./ports";

const REDIS_WAIT_SECONDS = 15;
export const APP_WAIT_SECONDS = 180;

const spawned: { backend?: number; redis?: number } = {};
let attachedBackendPid: number | null = null;
let stopped = false;

/** ATTACH 路径：登记已在跑的后端 PID（退出时按可执行路径校验后杀树） */
export function registerAttachedPid(pid: number): void {
  attachedBackendPid = pid;
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

export function redisAlive(): Promise<boolean> {
  return new Promise((resolve) => {
    const cli = redisCliExe();
    if (!fs.existsSync(cli)) {
      resolve(false);
      return;
    }
    const child = spawn(cli, ["-p", "6379", "ping"], { windowsHide: true });
    let out = "";
    const timer = setTimeout(() => {
      child.kill();
      resolve(false);
    }, 5000);
    child.stdout.on("data", (d: Buffer) => {
      out += d.toString();
    });
    child.on("error", () => {
      clearTimeout(timer);
      resolve(false);
    });
    child.on("close", () => {
      clearTimeout(timer);
      resolve(out.includes("PONG"));
    });
  });
}

/** Redis 未起则拉起内置便携 redis（0.2s 粒度轮询、上限 15s，对齐 launcher.start_redis_if_needed） */
export async function startRedisIfNeeded(): Promise<void> {
  if (await redisAlive()) return;
  const server = redisServerExe();
  if (!fs.existsSync(server)) {
    log("redis-server.exe 不存在，跳过拉起");
    return;
  }
  const args: string[] = [];
  const conf = redisConfPath();
  if (fs.existsSync(conf)) args.push(conf);
  const child = spawn(server, args, {
    cwd: redisDir(),
    stdio: "ignore",
    windowsHide: true,
  });
  child.on("error", (e) => log(`redis spawn error: ${String(e)}`));
  spawned.redis = child.pid;
  const deadline = Date.now() + REDIS_WAIT_SECONDS * 1000;
  while (Date.now() < deadline) {
    await sleep(200);
    if (await redisAlive()) break;
  }
  log(`redis: ${(await redisAlive()) ? "ok" : "timeout"}`);
}

/** 起后端（日志追加到 logs/backend.log，对齐 launcher.start_backend；粒度秒显由编排层负责） */
export function startBackend(): void {
  fs.mkdirSync(logDir(), { recursive: true });
  const fd = fs.openSync(backendLogPath(), "a");
  const child = spawn(pythonExe(), [runPyPath()], {
    cwd: backendRoot(),
    stdio: ["ignore", fd, fd],
    windowsHide: true,
  });
  fs.closeSync(fd); // 子进程已复制该 fd，父进程关掉自己这份句柄
  child.on("error", (e) => log(`backend spawn error: ${String(e)}`));
  spawned.backend = child.pid;
  log(`backend spawned pid=${child.pid}`);
}

/** 后端就绪轮询（0.5s 粒度 / 上限 180s；launcher 为 1s 粒度，减半加快切页） */
export async function waitBackendReady(port: number): Promise<boolean> {
  const deadline = Date.now() + APP_WAIT_SECONDS * 1000;
  while (Date.now() < deadline) {
    if (await portAlive(port)) return true;
    await sleep(500);
  }
  return false;
}

/** Windows 标准杀树：系统 taskkill /T /F（零依赖，不引 tree-kill） */
function killTree(pid: number): void {
  const r = spawnSync("taskkill", ["/pid", String(pid), "/T", "/F"], { windowsHide: true });
  log(`taskkill /pid ${pid} /T /F → exit=${r.status}`);
}

/** 同步查进程可执行路径（ATTACH 杀前校验用，复用 launcher 的 Win32_Process 查询） */
function executablePathOfPidSync(pid: number): string | null {
  const cmd = `(Get-CimInstance Win32_Process -Filter "ProcessId=${pid}").ExecutablePath`;
  const r = spawnSync("powershell", ["-NoProfile", "-Command", cmd], {
    windowsHide: true,
    timeout: 30000,
  });
  const out = r.stdout.toString().trim();
  return out.length > 0 ? out : null;
}

// 兜底清场：按可执行路径前缀杀本安装目录内的 python/pythonw/redis-server
// （对齐 launcher PS_KILL 的后半段，收掉 ATTACH 的 redis 与被强杀主进程遗留的孤儿；绝不碰目录外进程）
const PS_SWEEP =
  "$d=$env:STOP_ROOT;" +
  'Get-CimInstance Win32_Process -Filter "Name=\'python.exe\' OR Name=\'pythonw.exe\' OR Name=\'redis-server.exe\'" |' +
  "Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($d) } |" +
  "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }";

function sweepByInstallDir(): void {
  try {
    const r = spawnSync("powershell", ["-NoProfile", "-Command", PS_SWEEP], {
      env: { ...process.env, STOP_ROOT: backendRoot() + "\\" },
      windowsHide: true,
      timeout: 60000,
    });
    log(`install-dir process sweep exit=${r.status}`);
  } catch (e) {
    log(`sweep failed: ${String(e)}`);
  }
}

/** 退出钩子：同步杀完我起的子进程树 + ATTACH 后端（路径校验后）+ 安装目录兜底清场，再放行退出 */
export function stopAllServices(): void {
  if (stopped) return;
  stopped = true;
  if (spawned.backend !== undefined) killTree(spawned.backend);
  if (spawned.redis !== undefined) killTree(spawned.redis);
  if (attachedBackendPid !== null) {
    const p = executablePathOfPidSync(attachedBackendPid);
    const rootPrefix = (backendRoot() + "\\").toLowerCase();
    if (p !== null && p.toLowerCase().startsWith(rootPrefix)) {
      killTree(attachedBackendPid);
    } else {
      log(`ATTACH pid=${attachedBackendPid} 可执行路径校验未过，跳过杀树: ${p ?? "未知"}`);
    }
  }
  sweepByInstallDir();
  log("services stopped");
}
