#!/usr/bin/env python3
"""一键同步本项目到 GitHub 公开仓库（自动过滤敏感文件 + 密钥扫描 + 推送）。

用法：
    python sync_public.py                  # 同步 + 提交 + 推送（提交前可交互输入本次说明，直接回车用默认日志）
    python sync_public.py -m "修复xxx"     # 跳过交互，直接用指定提交说明
    python sync_public.py --dry-run        # 只预览：会同步哪些文件、密钥扫描结果，不写不推
    python sync_public.py --no-push        # 只同步 + 提交，不推送
    python sync_public.py --force          # 推送被拒绝时强制覆盖远端（镜像语义，慎用）

安全机制：
1. 排除清单：.env 系列（*.example 除外）、内网临时脚本、本地工作区等不进公开仓库
2. 推送前密钥扫描（双重）：
   - 通用模式：sk-* / ark-* / LTAI* / ghp_* / 私钥块等常见凭据特征
   - 动态指纹：读取本项目所有真实 .env 文件里的 KEY/SECRET/PASSWORD/TOKEN 值，
     逐一检查同步内容中是否残留（命中的是「变量名」，不会打印密钥本身）
   任一命中立即中止，绝不推送。

依赖：git（命令行可用）；推送到 https 远端需已配置凭据（PAT 或 credential manager）。
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ── 目标配置 ─────────────────────────────────────────────────────────────────
SRC = Path(__file__).resolve().parent
DST = Path(r"D:\Code\agent-deepseek")
REMOTE_URL = "https://github.com/2842902295/agent-deepseek.git"
BRANCH = "main"
# 本机经本地代理访问 GitHub 时填写（如 Clash 混合端口）；留空则直连。
# 推送时优先走该代理，失败自动回退直连
GIT_PROXY = "http://127.0.0.1:7890"

# ── 排除规则（公开仓库不需要的内容，按需增删）───────────────────────────────
# 任意深度命中的目录名
EXCLUDE_DIRS = {
    ".git", ".claude", ".codex", ".idea", ".vscode",
    "node_modules", ".pnpm-store", "dist", "dist-ssr", "coverage",
    "__pycache__", ".ruff_cache", ".pytest_cache", ".mypy_cache",
    ".venv", "venv", "redis_data", "logs", "data",
    "temp_script", "temp_doc", "docs",
    "offline_deploy", "desktop_dist", ".tmp_images", ".extraction_log",
    ".qa_workspace", ".VSCodeCounter", "migrations",
    "videos",  # 运行时产物视频；仓库根目录的 screenshots/ 是 README 配图，不排除
    "ssl", "maintenance",  # SSL 私钥 / 运维内部资料，严禁公开
    "conversation_history",  # 对话记录，隐私内容严禁公开
}
# 相对路径精确排除的目录（目录名本身太通用、不能全局排除时使用）
EXCLUDE_DIR_PATHS = {
    "web/cypress/screenshots",
    "web/cypress/videos",
}
# 文件名通配
EXCLUDE_FILE_PATTERNS = (
    "*.pyc", "*.log", "*.sqlite3", "*.sqlite3-*", ".DS_Store", "*.zip",
    "tortoise_queries.json", "tortoise_last.txt", ".build_fingerprint",
    ".pdm-python", "*.local",
)
# 相对路径精确排除（一次性临时脚本 / 内部资料）
EXCLUDE_PATHS = {
    "CLAUDE.md",
    "add_menu_temp.py",
    "_fix_migration.py",
    "_write_plan.py",
    "apipod_create.md",
    "apipod_extract.txt",
    "apipod_query.md",
    "fix_preview_image_path.sql",
    "generate_visualization.py",
    "test_artifact_mismatch.html",
}
# .agent_workspace 下只同步技能目录（内置技能随仓库分发）
WORKSPACE_KEEP = {".agent_skills"}

# 密钥扫描跳过的二进制扩展名
BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".bmp",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".mp4", ".webm", ".mp3", ".wav", ".pdf", ".gz", ".7z", ".rar",
    ".pyc", ".pyo", ".so", ".dll", ".exe",
}

# 通用凭据特征（正则；前置负向后行断言排除 mask-x / .sk-x 类名等误报）
GENERIC_SECRET_PATTERNS = (
    ("OpenAI 风格 sk-* 密钥", re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9]{16,}")),
    ("OpenRouter 密钥", re.compile(r"(?<![A-Za-z0-9])sk-or-v1-[a-f0-9]{40,}")),
    ("Anthropic 密钥", re.compile(r"(?<![A-Za-z0-9])sk-ant-[A-Za-z0-9\-]{20,}")),
    ("火山 ark-* 密钥", re.compile(r"(?<![A-Za-z0-9])ark-[a-f0-9]{8}-[a-f0-9\-]{10,}")),
    ("OpenRouter r-v1-* 密钥", re.compile(r"(?<![A-Za-z0-9])r-v1-[a-f0-9]{40,}")),
    ("阿里云 AccessKey", re.compile(r"LTAI[A-Za-z0-9]{12,}")),
    ("GitHub Token", re.compile(r"ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}")),
    ("Slack Token", re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("PEM 私钥", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)


def log(msg: str) -> None:
    print(msg, flush=True)


def progress(done: int, total: int, label: str, step: int = 250) -> None:
    """阶段性进度输出，避免长时间无回显。"""
    if done % step == 0 or done == total:
        log(f"      {label} {done}/{total}")


def run(cmd: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), check=check, capture_output=True, text=True, encoding="utf-8", errors="replace")


# ── 文件收集 ─────────────────────────────────────────────────────────────────
def env_file_allowed(name: str) -> bool:
    """.env / .env.xxx 一律排除，唯一例外：*.example 模板。"""
    if name == ".env" or name.startswith(".env."):
        return name.endswith(".example")
    return True


def should_exclude_file(rel: Path) -> bool:
    name = rel.name
    posix = rel.as_posix()
    if posix in EXCLUDE_PATHS:
        return True
    if not env_file_allowed(name):
        return True
    return any(_fnmatch(name, pat) for pat in EXCLUDE_FILE_PATTERNS)


def _fnmatch(name: str, pattern: str) -> bool:
    import fnmatch

    return fnmatch.fnmatch(name, pattern)


def collect_source_files() -> list[Path]:
    """遍历源目录，返回应同步文件的相对路径列表。"""
    result: list[Path] = []

    def walk(cur: Path, rel: Path) -> None:
        for entry in sorted(cur.iterdir()):
            entry_rel = rel / entry.name if rel != Path(".") else Path(entry.name)
            if entry.is_dir():
                if entry.name in EXCLUDE_DIRS or entry.name.endswith(".egg-info"):
                    continue
                if entry_rel.as_posix() in EXCLUDE_DIR_PATHS:
                    continue
                if entry.name == ".agent_workspace" and rel == Path("."):
                    # 工作区只保留技能目录
                    for sub in sorted(entry.iterdir()):
                        if sub.is_dir() and sub.name in WORKSPACE_KEEP:
                            walk(sub, Path(".agent_workspace") / sub.name)
                    continue
                walk(entry, entry_rel)
            else:
                if not should_exclude_file(entry_rel):
                    result.append(entry_rel)

    walk(SRC, Path("."))
    return sorted(result)


# ── 密钥扫描 ─────────────────────────────────────────────────────────────────
def collect_env_secrets() -> dict[str, str]:
    """从源目录全部真实 .env 文件（*.example 除外）提取疑似凭据值。

    返回 {指纹描述: 值}；指纹描述形如「.env::CHAT_DASHSCOPE_API_KEY」，
    报告时只展示描述，绝不打印值本身。
    """
    secrets: dict[str, str] = {}
    for env_file in sorted(SRC.rglob(".env*")):
        if any(p in EXCLUDE_DIRS for p in env_file.relative_to(SRC).parts[:-1]):
            continue
        if env_file.name.endswith(".example"):
            continue
        if not env_file.is_file():
            continue
        rel = env_file.relative_to(SRC).as_posix()
        try:
            lines = env_file.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if len(value) < 8:
                continue
            if not any(tag in key.upper() for tag in ("KEY", "SECRET", "PASSWORD", "TOKEN")):
                continue
            # 跳过明显的占位符
            if any(p in value.lower() for p in ("xxxx", "your-", "change-me", "placeholder")):
                continue
            secrets[f"{rel}::{key}"] = value
    return secrets


def scan_tree(root: Path, env_secrets: dict[str, str]) -> list[str]:
    """扫描目录内全部文本文件，返回命中的「文件 ← 指纹」描述列表。"""
    hits: list[str] = []
    fingerprint_values = list(env_secrets.items())
    candidates = [p for p in sorted(root.rglob("*")) if p.is_file() and p.suffix.lower() not in BINARY_EXTS]
    for i, path in enumerate(candidates, 1):
        progress(i, len(candidates), "已扫描")
        rel = path.relative_to(root).as_posix()
        if rel.startswith(".git/"):
            continue
        try:
            if path.stat().st_size > 2 * 1024 * 1024:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for label, pattern in GENERIC_SECRET_PATTERNS:
            if pattern.search(text):
                hits.append(f"{rel}  ←  通用特征[{label}]")
        for fingerprint, value in fingerprint_values:
            if value in text:
                hits.append(f"{rel}  ←  {fingerprint}")
    return hits


# ── 镜像同步 ─────────────────────────────────────────────────────────────────
def mirror(files: list[Path], dry_run: bool) -> tuple[int, int]:
    copied = 0
    deleted = 0
    src_set = {f.as_posix() for f in files}

    if dry_run:
        return 0, 0

    DST.mkdir(parents=True, exist_ok=True)

    for i, rel in enumerate(files, 1):
        src_p = SRC / rel
        dst_p = DST / rel
        if dst_p.exists() and dst_p.stat().st_size == src_p.stat().st_size \
                and abs(dst_p.stat().st_mtime - src_p.stat().st_mtime) < 1:
            continue
        dst_p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_p, dst_p)
        copied += 1
        if copied % 100 == 0:
            log(f"      已复制 {copied} 个（进度 {i}/{len(files)}）")

    # 删除目标里源中已不存在的文件（镜像语义；.git 子树整体跳过）
    all_paths = sorted(DST.rglob("*"), key=lambda p: len(p.parts), reverse=True)
    for i, path in enumerate(all_paths, 1):
        if i % 500 == 0:
            log(f"      清理比对 {i}/{len(all_paths)}")
        if path.relative_to(DST).parts[0] == ".git":
            continue
        if path.is_dir():
            try:
                path.rmdir()  # 仅当目录为空时删除
                deleted += 1
            except OSError:
                pass
        else:
            rel = path.relative_to(DST).as_posix()
            if rel not in src_set:
                path.unlink()
                deleted += 1
    return copied, deleted


# ── git 提交推送 ─────────────────────────────────────────────────────────────
def git_push(dry_run: bool, no_push: bool, force: bool, message: str = "") -> None:
    if dry_run:
        return
    if not (DST / ".git").exists():
        r = run(["git", "init", "-b", BRANCH], DST, check=False)
        if r.returncode != 0:  # 老版本 git 不支持 -b
            run(["git", "init"], DST)
            run(["git", "checkout", "-b", BRANCH], DST, check=False)
        log(f"[git] 已在 {DST} 初始化仓库（分支 {BRANCH}）")

    r = run(["git", "remote", "get-url", "origin"], DST, check=False)
    if r.returncode != 0:
        run(["git", "remote", "add", "origin", REMOTE_URL], DST)
    elif r.stdout.strip() != REMOTE_URL:
        run(["git", "remote", "set-url", "origin", REMOTE_URL], DST)

    # 提交身份兜底
    if run(["git", "config", "user.email"], DST, check=False).stdout.strip() == "":
        g_name = run(["git", "config", "user.name"], DST, check=False).stdout.strip()
        g_mail = run(["git", "config", "user.email"], DST, check=False).stdout.strip()
        if g_name and g_mail:
            run(["git", "config", "user.name", g_name], DST)
            run(["git", "config", "user.email", g_mail], DST)
        else:
            log("[git] 错误：未配置 git user.name / user.email，请先执行：")
            log('      git config --global user.name "你的名字"')
            log('      git config --global user.email "你的邮箱"')
            sys.exit(1)

    log("[git] 暂存变更（git add，文件较多时需十几秒）……")
    run(["git", "add", "-A"], DST)
    status = run(["git", "status", "--porcelain"], DST).stdout.strip()
    if status:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        if message:
            msg = f"{message}\n\n(Agent DeepSeek sync, {stamp})"
        else:
            msg = f"Agent DeepSeek sync ({stamp})"
        run(["git", "commit", "-m", msg], DST)
        log(f"[git] 已提交：{msg.splitlines()[0]}")
    else:
        log("[git] 无新变更需要提交（若此前有未推送的提交，仍会继续推送）")

    if no_push:
        log("[git] --no-push：跳过推送")
        return

    # 优先走代理推送，失败回退直连（代理节点未连接时直连可能反而通）
    attempts = []
    if GIT_PROXY:
        attempts.append((f"代理 {GIT_PROXY}", ["-c", f"http.proxy={GIT_PROXY}", "-c", f"https.proxy={GIT_PROXY}"]))
    attempts.append(("直连", []))
    for label, cfg in attempts:
        log(f"[git] 推送（{label}）……")
        push_cmd = ["git", *cfg, "push"] + (["-f"] if force else []) + ["-u", "origin", BRANCH]
        r = run(push_cmd, DST, check=False)
        out = (r.stdout or "") + (r.stderr or "")
        if r.returncode == 0:
            if "Everything up-to-date" in out:
                log(f"[git] 远端已是最新，无需推送（{label}）")
            else:
                log(f"[git] 已推送到 {REMOTE_URL}（{BRANCH}，{label}）")
            return
        err = out.strip()
        log(f"[git] {label}推送失败：{err.splitlines()[-1] if err else '未知错误'}")
        if "rejected" in err and not force:
            log("[git] 推送被拒绝（远端有本地没有的提交）。两个选择：")
            log("      1) 先手动进入目标目录 git pull --rebase 后再跑本脚本；")
            log("      2) 确认要用本地完全覆盖远端时，重跑并加 --force。")
            sys.exit(1)
    log("[git] 所有推送方式均失败（多为网络无法连通 GitHub：检查代理客户端节点是否已连接，")
    log("      或稍后重试。本地提交已完成，网络恢复后重跑本脚本即可续推，不会重复提交。")
    sys.exit(1)


# ── 主流程 ───────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="同步项目到 GitHub 公开仓库（过滤敏感文件）")
    parser.add_argument("-m", "--message", default="", help="自定义本次提交说明（默认自动生成 Agent DeepSeek sync (时间)）")
    parser.add_argument("--dry-run", action="store_true", help="只预览与扫描，不写不推")
    parser.add_argument("--no-push", action="store_true", help="只同步提交，不推送")
    parser.add_argument("--force", action="store_true", help="推送被拒时强制覆盖远端")
    args = parser.parse_args()
    t_start = time.time()

    # 提交说明：-m 已指定则直接用；否则交互式输入，直接回车用默认 sync 日志
    message = args.message
    if not message and not args.dry_run:
        try:
            message = input("本次提交说明（直接回车用默认 sync 日志）：").strip()
        except EOFError:  # 非交互环境（管道 / 定时任务）静默用默认
            message = ""

    log(f"[1/4] 收集源文件：{SRC}")
    files = collect_source_files()
    log(f"      待同步 {len(files)} 个文件（已排除 .env 系列 / 临时脚本 / 内部目录）")

    log("[2/4] 密钥扫描（同步文件集合）……")
    env_secrets = collect_env_secrets()
    # 先在源侧对「将要同步的文件」扫一遍，提早暴露问题
    pre_hits = []
    for i, rel in enumerate(files, 1):
        progress(i, len(files), "已扫描")
        p = SRC / rel
        if p.suffix.lower() in BINARY_EXTS or p.stat().st_size > 2 * 1024 * 1024:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for label, pattern in GENERIC_SECRET_PATTERNS:
            if pattern.search(text):
                pre_hits.append(f"{rel.as_posix()}  ←  通用特征[{label}]")
        for fingerprint, value in env_secrets.items():
            if value in text:
                pre_hits.append(f"{rel.as_posix()}  ←  {fingerprint}")
    if pre_hits:
        log("      发现疑似密钥，中止（请清理后重跑）：")
        for h in sorted(set(pre_hits)):
            log(f"      - {h}")
        sys.exit(2)
    log(f"      通过（对照了 {len(env_secrets)} 条本地凭据指纹 + {len(GENERIC_SECRET_PATTERNS)} 类通用特征）")

    log(f"[3/4] 镜像同步 → {DST}" + ("（dry-run，跳过写入）" if args.dry_run else ""))
    copied, deleted = mirror(files, args.dry_run)
    if not args.dry_run:
        log(f"      复制/更新 {copied} 个，删除目标侧多余 {deleted} 个")

    if not args.dry_run:
        log("      推送前对目标目录再做一次全量密钥扫描……")
        post_hits = scan_tree(DST, env_secrets)
        if post_hits:
            log("      目标目录发现疑似密钥，中止（不会提交/推送）：")
            for h in sorted(set(post_hits)):
                log(f"      - {h}")
            sys.exit(2)
        log("      通过")

    log("[4/4] 提交并推送" + ("（dry-run，跳过）" if args.dry_run else ""))
    git_push(args.dry_run, args.no_push, args.force, message)
    log(f"完成（总耗时 {time.time() - t_start:.0f}s）。")


if __name__ == "__main__":
    main()
