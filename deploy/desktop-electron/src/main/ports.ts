// 端口探测三件 + 按端口查 PID（逐条对齐 launcher.pyw 的端口语义）。
import { spawn } from "child_process";
import * as http from "http";
import * as net from "net";

/** 端口有任何 HTTP 响应（含 4xx/5xx）即视为服务在跑（对齐 launcher.port_alive） */
export function portAlive(port: number, timeoutMs = 2000): Promise<boolean> {
  return new Promise((resolve) => {
    const req = http.get({ host: "127.0.0.1", port, path: "/", timeout: timeoutMs }, (res) => {
      res.resume(); // 扔掉 body，有响应即活着
      resolve(true);
    });
    req.on("timeout", () => {
      req.destroy();
      resolve(false);
    });
    req.on("error", () => resolve(false));
  });
}

/** 端口无人监听（可绑定）。与 port_alive 互补：区分「我们的服务」与「被别的程序占用」 */
export function portFree(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const srv = net.createServer();
    srv.once("error", () => resolve(false));
    srv.listen({ host: "127.0.0.1", port }, () => {
      srv.close(() => resolve(true));
    });
  });
}

/** 从 preferred 起向上找空闲端口；实在没有让系统随机分配（对齐 launcher.find_free_port） */
export async function findFreePort(preferred: number): Promise<number> {
  for (let p = preferred; p < preferred + 100; p++) {
    if (await portFree(p)) return p;
  }
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.once("error", reject);
    srv.listen({ host: "127.0.0.1", port: 0 }, () => {
      const addr = srv.address();
      const p = addr && typeof addr === "object" ? addr.port : 0;
      srv.close(() => resolve(p));
    });
  });
}

/** netstat -ano 解析监听端口的 PID（ATTACH 路径：登记已在跑的服务，退出时校验后清理） */
export function pidByPort(port: number): Promise<number | null> {
  return new Promise((resolve) => {
    const child = spawn("netstat", ["-ano"], { windowsHide: true });
    let out = "";
    let settled = false;
    const timer = setTimeout(() => {
      child.kill();
      if (!settled) {
        settled = true;
        resolve(null);
      }
    }, 10000);
    child.stdout.on("data", (d: Buffer) => {
      out += d.toString();
    });
    child.on("error", () => {
      clearTimeout(timer);
      if (!settled) {
        settled = true;
        resolve(null);
      }
    });
    child.on("close", () => {
      clearTimeout(timer);
      if (settled) return;
      settled = true;
      const suffix = `:${port}`;
      for (const line of out.split(/\r?\n/)) {
        if (!line.includes("LISTENING")) continue;
        // TCP    0.0.0.0:51234    0.0.0.0:0    LISTENING    12345
        const cols = line.trim().split(/\s+/);
        if (cols.length < 5 || !cols[1].endsWith(suffix)) continue;
        const pid = parseInt(cols[4], 10);
        if (Number.isFinite(pid) && pid > 0) {
          resolve(pid);
          return;
        }
      }
      resolve(null);
    });
  });
}
