// cesi_cancel_listener —— cordis 插件：带外回合取消（前端「停止」的真实终止通道）
//
// 背景：dsh SDK stdio 面只接线 initialize / session/prompt / shutdown，
// session/cancel 在 handleRequest 里不接线（0.1.2-alpha.2 / alpha.3 复测均如此）。
// 但运行时的 Agent 类自带完整取消机制（agent-loop 的 AbortController，
// 官方 web/桌面客户端的停止按钮走 dsh-api-session-controller.cancel →
// agent.cancel({kind:"user"}, {keepInbox:true})）——sdk profile 下只是没人接入口。
// 本插件补上这个入口：监听 $DSH_HOME 下的 cancel-*.signal 信号文件，
// 文件内容 = 要取消的 dsh 会话别名（"*" = 本进程全部在途回合），
// 命中即调 agent.cancel()——在途 LLM 请求/工具循环被 abort，
// 回合以 turn/end reason=aborted 收尾，SDK run() 立即返回；
// 会话档案完好，**原会话可直接续用**（无需升代数/历史注入）。
//
// 信号文件由后端写入（app/langchain/agents/qa_agent.py::DshQaAgent._signal_cancel），
// 消费（成功取消）后由本插件删除；无命中的信号文件保持不动
// （可能属于共享同一 DSH_HOME 的另一个运行时实例，或回合已自然结束）。
//
// 红线：dsh 进程 stdout 是 JSON-RPC 通道——本插件一切日志只走 stderr，
// 所有异常就地吞掉（插件异常会杀 fiber，绝不允许外抛）。

import { watch } from "node:fs";
import { readdir, readFile, rm } from "node:fs/promises";
import { join } from "node:path";

export const name = "cesi-cancel-listener";
export const inject = ["agents"];

const PREFIX = "cancel-";
const SUFFIX = ".signal";

export function apply(ctx, config) {
	const home = process.env.CESI_CANCEL_DIR || process.env.DSH_HOME || "";
	if (!home) return;
	const log = (msg) => {
		try {
			process.stderr.write(msg + "\n");
		} catch {
			/* never throw */
		}
	};

	// 返回成功取消的回合数（0 = 无命中，信号文件应保留给其它实例/后续扫描）
	const cancelBySpec = (spec) => {
		const want = String(spec || "").trim();
		if (!want) return 0;
		let hit = 0;
		try {
			if (want === "*") {
				for (const [id, entry] of ctx.agents.store) {
					try {
						entry.agent?.cancel?.({ kind: "user" }, { keepInbox: true });
						hit++;
						log("[cesi-cancel] cancelled (*) session=" + id);
					} catch {
						/* 单个失败不影响其余 */
					}
				}
			} else {
				const agent = ctx.agents.get(want);
				if (agent) {
					agent.cancel({ kind: "user" }, { keepInbox: true });
					hit++;
					log("[cesi-cancel] cancelled session=" + want);
				} else {
					log("[cesi-cancel] no live agent for " + want + " (turn may have ended or lives in another runtime)");
				}
			}
		} catch (e) {
			log("[cesi-cancel] error: " + String(e));
		}
		return hit;
	};

	let busy = false;
	const scan = async () => {
		if (busy) return;
		busy = true;
		try {
			const files = await readdir(home);
			for (const f of files) {
				if (!f.startsWith(PREFIX) || !f.endsWith(SUFFIX)) continue;
				const p = join(home, f);
				try {
					const hit = cancelBySpec(await readFile(p, "utf8"));
					if (hit > 0) await rm(p, { force: true });
				} catch {
					/* 读/删失败下轮再来 */
				}
			}
		} catch {
			/* 目录不可读等：静默，轮询兜底 */
		} finally {
			busy = false;
		}
	};

	// inotify 即时触发 + 2s 轮询兜底（watch 丢事件时不至于永远漏掉）
	try {
		watch(home, (_eventType, filename) => {
			const fn = String(filename || "");
			if (fn.startsWith(PREFIX) && fn.endsWith(SUFFIX)) scan();
		});
	} catch {
		/* watch 不可用还有轮询 */
	}
	const timer = setInterval(scan, 2000);
	timer.unref?.();
	scan();
	log("[cesi-cancel] listener ready, watching " + home);
}
