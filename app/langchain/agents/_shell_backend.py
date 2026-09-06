"""
deepagents 的 `LocalShellBackend` 在 `virtual_mode=True` 下，文件工具看到的虚拟根
（`/` 指 workspace 根）和 shell 看到的真实根（`/` 指真实磁盘根）不一致；模型很
容易把 `glob`/`ls` 返回的虚拟路径直接喂给 `execute`，导致 "No such file or
directory" 反复试错。

本模块的 `SafeLocalShellBackend` 做两件事：
1. 在 `execute` 入口前做一次**窄而确定**的路径改写：仅当命令字符串里出现以
   `/.agent_skills` 或 `/.agent_workspace` 开头的"独立 token"时，把它替换成
   workspace 内对应的真实路径。
2. 支持可选的 `shell_executable` 参数（默认 `"auto"`），可传 `None` 保持系统
   原生 shell，或显式指定如 `"pwsh"`、`"/bin/bash"`。

`"auto"` 在 Windows 上的检测顺序为 **Git Bash → pwsh → powershell**，找不到
则回退 None（cmd.exe）；其它平台一律返回 None（走系统默认 /bin/sh，保持现状）。
选 Git Bash 的原因：LLM 训练语料与 skill 文档以 bash 命令为主，cmd.exe 下首条
命令几乎必然是 ls/grep 等 Unix 命令然后报错重试；bash 语法（管道、$VAR、
mkdir -p）可直接可用。注意**绝不使用** `C:\\Windows\\System32\\bash.exe`——
那是 WSL 入口，文件系统视图完全不同。

这样即使 prompt 出错，机制上也避免了同一类失败。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from deepagents.backends import LocalShellBackend
from deepagents.backends.protocol import ExecuteResponse
from loguru import logger

# 仅改写这两个虚拟前缀；它们在真实 OS 根下不存在，安全。
_VIRTUAL_PREFIXES = ("/.agent_skills", "/.agent_workspace")

# 匹配独立 token：前面是行首/空白/引号/=（命令边界或参数边界），后面是行尾/空白/
# 引号/shell 元字符；中间是合法路径字符。引号内的路径也覆盖到（前后引号视为边界）。
_TOKEN_RE = re.compile(
    r"""(?P<lead>(?:\A|[\s'"=]))         # 左边界
        (?P<path>/\.agent_(?:skills|workspace)
                 (?:/[^\s'";|&<>()`$]*)? # 路径主体，遇 shell 元字符停止
        )
        (?=\Z|[\s'";|&<>()`$])           # 右边界（lookahead 不消耗）
    """,
    re.VERBOSE,
)


def _detect_git_bash() -> str | None:
    """在 Windows 上寻找 Git for Windows 的 bash.exe。

    **绝不用 `shutil.which("bash")`**：PATH 里先命中的往往是
    `C:\\Windows\\System32\\bash.exe`（WSL 入口），命令会跑进 Linux 子系统，
    文件系统视图与 Windows 完全不同。只接受从 Git 安装位置推导出的 bash.exe。
    """
    candidates: list[Path] = []
    git_exe = shutil.which("git")
    if git_exe:
        # git.exe 一般在 <GitRoot>/cmd/ 或 <GitRoot>/mingw64/bin/ 下
        git_root = Path(git_exe).resolve().parent.parent
        candidates += [
            git_root / "usr" / "bin" / "bash.exe",
            git_root / "bin" / "bash.exe",
        ]
    # 兜底：常见安装位置
    for base in (
        os.environ.get("PROGRAMFILES"),
        os.environ.get("PROGRAMFILES(X86)"),
        str(Path.home() / "scoop" / "apps" / "git" / "current"),
    ):
        if not base:
            continue
        candidates += [
            Path(base) / "Git" / "usr" / "bin" / "bash.exe",
            Path(base) / "Git" / "bin" / "bash.exe",
        ]

    seen: set[Path] = set()
    for cand in candidates:
        cand = cand.resolve() if cand.exists() else cand
        if cand in seen or not cand.is_file():
            continue
        seen.add(cand)
        try:
            probe = subprocess.run([str(cand), "--version"], capture_output=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            continue
        # Git Bash 自报 "GNU bash, version ..."；借此排除任何非 GNU bash
        if b"GNU bash" in (probe.stdout or b""):
            return str(cand)
    return None


def detect_agent_shell() -> str | None:
    """自动检测 agent execute 工具使用的 shell。

    - Windows：Git Bash → pwsh → powershell → None（回退 cmd.exe）
    - 其它平台：一律 None，走系统默认 /bin/sh（保持 Linux 部署现状不变）

    返回完整路径以确保 subprocess 能找到。
    """
    if os.name != "nt":
        return None

    git_bash = _detect_git_bash()
    if git_bash:
        return git_bash

    for shell in ("pwsh", "powershell"):
        full_path = shutil.which(shell)
        if full_path:
            try:
                subprocess.run([full_path, "-Version"], capture_output=True, timeout=5, check=False)
                return full_path
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue
    return None


# 向后兼容别名
_detect_shell = detect_agent_shell


def shell_family(shell_executable: str | None) -> str:
    """把 shell 可执行文件归类：'bash' / 'pwsh' / 'powershell' / 'cmd'。

    cmd 表示未指定 shell（Windows 下 subprocess shell=True 默认 cmd.exe，
    Linux 下为 /bin/sh——调用方按平台自行解读）。
    """
    if not shell_executable:
        return "cmd"
    name = Path(shell_executable).name.lower()
    if "bash" in name or name == "sh":
        return "bash"
    if "pwsh" in name:
        return "pwsh"
    if "powershell" in name:
        return "powershell"
    return "cmd"


class SafeLocalShellBackend(LocalShellBackend):
    """`LocalShellBackend` 的安全壳：执行前重写虚拟绝对路径，并支持指定 shell。"""

    def __init__(
            self,
            *args,
            workspace: Path,
            shell_executable: str | None = "auto",
            **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._workspace = Path(workspace).resolve()
        # shell_executable:
        #   "auto"（默认）→ detect_agent_shell()：Windows 上 Git Bash > pwsh >
        #                    powershell，找不到则 None（回退 cmd.exe）；其它平台 None
        #   None          → 不指定，走系统默认 shell（Windows: cmd.exe, Linux: /bin/sh）
        #   "xxx"         → 显式指定，如 "powershell" / "pwsh" / "/bin/bash"
        if shell_executable == "auto":
            self._shell_executable = detect_agent_shell()
        else:
            self._shell_executable = shell_executable
        self._shell_family = shell_family(self._shell_executable)

        # Windows + Git Bash：coreutils（ls/mkdir/grep/cat...）都在 <Git>/usr/bin。
        # 交互式 Git Bash 靠 /etc/profile 把它加进 PATH，而 `bash -c` 不加载
        # profile——宿主进程 PATH 里没有该目录时，agent 的 ls/mkdir 会 command
        # not found。这里显式补进子进程 PATH（插到最前；该目录无 python/pip，
        # 不会遮蔽虚拟环境可执行文件）。
        if os.name == "nt" and self._shell_family == "bash" and self._shell_executable:
            bash_dir = Path(self._shell_executable).parent
            git_bin_dirs = [bash_dir, bash_dir.parent / "usr" / "bin"]
            path_parts = self._env.get("PATH", "").split(os.pathsep)
            for d in reversed(git_bin_dirs):
                sd = str(d)
                if d.is_dir() and sd not in path_parts:
                    path_parts.insert(0, sd)
            self._env["PATH"] = os.pathsep.join(path_parts)

        logger.info(
            f"[SafeLocalShellBackend] shell_executable={self._shell_executable!r}"
            f" family={self._shell_family}（参数={shell_executable!r}）"
        )

    # 真实路径替换函数：把 "/.agent_skills/..." 改为 "<workspace>/.agent_skills/..."
    def _rewrite_path(self, virtual: str) -> str:
        # virtual 形如 "/.agent_skills/foo/bar"；去掉前导 "/"，拼到 workspace 下
        return str(self._workspace / virtual.lstrip("/"))

    def _rewrite_command(self, command: str) -> tuple[str, list[tuple[str, str]]]:
        rewrites: list[tuple[str, str]] = []

        def _sub(m: re.Match) -> str:
            virtual = m.group("path")
            real = self._rewrite_path(virtual)
            rewrites.append((virtual, real))
            return f"{m.group('lead')}{real}"

        # 快路径：没有任一虚拟前缀，直接返回，零开销
        if not any(p in command for p in _VIRTUAL_PREFIXES):
            return command, rewrites
        return _TOKEN_RE.sub(_sub, command), rewrites

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        rewritten, rewrites = self._rewrite_command(command)
        if rewrites:
            preview = ", ".join(f"{v} → {r}" for v, r in rewrites[:3])
            more = f"（共 {len(rewrites)} 处）" if len(rewrites) > 3 else ""
            logger.info(f"[shell-rewrite] 修正虚拟绝对路径：{preview}{more}")

        # 如果指定了 shell_executable，直接调 subprocess.run 绕过父类的 execute
        if self._shell_executable:
            return self._execute_with_shell(rewritten, timeout=timeout)
        return super().execute(rewritten, timeout=timeout)

    # 各 shell 输出解码方式。bash（Git Bash 的 coreutils）输出 UTF-8；
    # pwsh 7 重定向输出 UTF-8；powershell 5.1 重定向输出跟随系统代码页
    # （中文 Windows 为 GBK），用 None 交给 locale 默认解码。
    _SHELL_ENCODING: dict[str, str | None] = {
        "bash": "utf-8",
        "pwsh": "utf-8",
        "powershell": None,
    }

    def _execute_with_shell(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """使用指定的 shell 执行命令（绕过父类 execute，添加 executable 参数）。"""
        effective_timeout = timeout if timeout is not None else self._default_timeout
        if effective_timeout <= 0:
            msg = f"timeout must be positive, got {effective_timeout}"
            raise ValueError(msg)

        encoding = self._SHELL_ENCODING.get(self._shell_family)

        try:
            if self._shell_family == "bash":
                # Windows 上 `subprocess.run(cmd, shell=True, executable=bash)` 会被
                # 拼成 `bash.exe /c "cmd"`——`/c` 被 bash 当成脚本路径（实测 rc=127）。
                # 必须用 argv 形式传 `-c`。Linux 下 /bin/bash 同样适用，无平台分支。
                result = subprocess.run(  # noqa: S602
                    [self._shell_executable, "-c", command],
                    check=False,
                    shell=False,
                    capture_output=True,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    encoding=encoding,
                    errors="replace",
                    timeout=effective_timeout,
                    env=self._env,
                    cwd=str(self.cwd),
                )
            else:
                result = subprocess.run(  # noqa: S602
                    command,
                    check=False,
                    shell=True,
                    executable=self._shell_executable,  # 关键：指定 shell
                    capture_output=True,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    encoding=encoding,
                    errors="replace",
                    timeout=effective_timeout,
                    env=self._env,
                    cwd=str(self.cwd),
                )

            output_parts = []
            if result.stdout:
                output_parts.append(result.stdout)
            if result.stderr:
                stderr_lines = result.stderr.strip().split("\n")
                output_parts.extend(f"[stderr] {line}" for line in stderr_lines)

            output = "\n".join(output_parts) if output_parts else "<no output>"

            if len(output) > self._max_output_bytes:
                output = output[: self._max_output_bytes]
                output += f"\n\n... Output truncated at {self._max_output_bytes} bytes."
                truncated = True
            else:
                truncated = False

            if result.returncode != 0:
                output = f"{output.rstrip()}\n\nExit code: {result.returncode}"

            return ExecuteResponse(
                output=output,
                exit_code=result.returncode,
                truncated=truncated,
            )

        except subprocess.TimeoutExpired:
            if timeout is not None:
                return ExecuteResponse(
                    output=f"Command timed out after {timeout} seconds.",
                    exit_code=-1,
                    truncated=False,
                )
            return ExecuteResponse(
                output=f"Command timed out after {self._default_timeout} seconds.",
                exit_code=-1,
                truncated=False,
            )

    @property
    def shell_executable(self) -> str | None:
        """当前使用的 shell 可执行文件名。"""
        return self._shell_executable

    @property
    def shell_family_name(self) -> str:
        """shell 家族：'bash' / 'pwsh' / 'powershell' / 'cmd'。"""
        return self._shell_family


__all__ = ["SafeLocalShellBackend", "detect_agent_shell", "shell_family"]
