#!/usr/bin/env python3
"""生成并启动评测结果审查页面。

读取工作区目录，发现运行记录（包含 outputs/ 子目录的目录），
将所有输出数据嵌入到一个自包含的 HTML 页面中，并通过一个轻量 HTTP 服务器提供服务。
评审反馈自动保存到工作区中的 feedback.json。

用法：
    python generate_review.py <工作区路径> [--port 端口号] [--skill-name 技能名称]
    python generate_review.py <工作区路径> --previous-feedback /旧反馈文件路径/feedback.json

仅依赖 Python 标准库，无需额外第三方库。
"""

import argparse
import base64
import json
import mimetypes
import os
import re
import signal
import subprocess
import sys
import time
import webbrowser
from functools import partial
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# 从输出列表中排除的文件
METADATA_FILES = {"transcript.md", "user_notes.md", "metrics.json"}

# 我们以内联文本形式渲染的扩展名
TEXT_EXTENSIONS = {
    ".txt", ".md", ".json", ".csv", ".py", ".js", ".ts", ".tsx", ".jsx",
    ".yaml", ".yml", ".xml", ".html", ".css", ".sh", ".rb", ".go", ".rs",
    ".java", ".c", ".cpp", ".h", ".hpp", ".sql", ".r", ".toml",
}

# 我们以内联图片形式渲染的扩展名
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}

# 常见类型的 MIME 类型覆盖
MIME_OVERRIDES = {
    ".svg": "image/svg+xml",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def get_mime_type(path: Path) -> str:
    """根据文件扩展名返回对应 MIME 类型。"""
    ext = path.suffix.lower()
    if ext in MIME_OVERRIDES:
        return MIME_OVERRIDES[ext]
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "application/octet-stream"


def find_runs(workspace: Path) -> list[dict]:
    """递归查找包含 outputs/ 子目录的目录，作为一次运行记录。"""
    runs: list[dict] = []
    _find_runs_recursive(workspace, workspace, runs)
    runs.sort(key=lambda r: (r.get("eval_id", float("inf")), r["id"]))
    return runs


def _find_runs_recursive(root: Path, current: Path, runs: list[dict]) -> None:
    """递归辅助函数：遍历目录，收集所有包含 outputs/ 子目录的运行记录。"""
    if not current.is_dir():
        return

    outputs_dir = current / "outputs"
    if outputs_dir.is_dir():
        run = build_run(root, current)
        if run:
            runs.append(run)
        return

    skip = {"node_modules", ".git", "__pycache__", "skill", "inputs"}
    for child in sorted(current.iterdir()):
        if child.is_dir() and child.name not in skip:
            _find_runs_recursive(root, child, runs)


def build_run(root: Path, run_dir: Path) -> dict | None:
    """构建运行记录字典，包含提示词、输出文件和评分数据。"""
    prompt = ""
    eval_id = None

    # 优先尝试 eval_metadata.json
    for candidate in [run_dir / "eval_metadata.json", run_dir.parent / "eval_metadata.json"]:
        if candidate.exists():
            try:
                metadata = json.loads(candidate.read_text())
                prompt = metadata.get("prompt", "")
                eval_id = metadata.get("eval_id")
            except (json.JSONDecodeError, OSError):
                pass
            if prompt:
                break

    # 回退到 transcript.md
    if not prompt:
        for candidate in [run_dir / "transcript.md", run_dir / "outputs" / "transcript.md"]:
            if candidate.exists():
                try:
                    text = candidate.read_text()
                    match = re.search(r"## Eval Prompt\n\n([\s\S]*?)(?=\n##|$)", text)
                    if match:
                        prompt = match.group(1).strip()
                except OSError:
                    pass
                if prompt:
                    break

    if not prompt:
        prompt = "（未找到提示词）"

    run_id = str(run_dir.relative_to(root)).replace("/", "-").replace("\\", "-")

    # 收集输出文件
    outputs_dir = run_dir / "outputs"
    output_files: list[dict] = []
    if outputs_dir.is_dir():
        for f in sorted(outputs_dir.iterdir()):
            if f.is_file() and f.name not in METADATA_FILES:
                output_files.append(embed_file(f))

    # 加载评分数据（如果存在）
    grading = None
    for candidate in [run_dir / "grading.json", run_dir.parent / "grading.json"]:
        if candidate.exists():
            try:
                grading = json.loads(candidate.read_text())
            except (json.JSONDecodeError, OSError):
                pass
            if grading:
                break

    return {
        "id": run_id,
        "prompt": prompt,
        "eval_id": eval_id,
        "outputs": output_files,
        "grading": grading,
    }


def embed_file(path: Path) -> dict:
    """读取文件并返回内嵌表示形式（文本、图片、PDF、Excel 或二进制）。"""
    ext = path.suffix.lower()
    mime = get_mime_type(path)

    if ext in TEXT_EXTENSIONS:
        try:
            content = path.read_text(errors="replace")
        except OSError:
            content = "（读取文件时出错）"
        return {
            "name": path.name,
            "type": "text",
            "content": content,
        }
    elif ext in IMAGE_EXTENSIONS:
        try:
            raw = path.read_bytes()
            b64 = base64.b64encode(raw).decode("ascii")
        except OSError:
            return {"name": path.name, "type": "error", "content": "（读取文件时出错）"}
        return {
            "name": path.name,
            "type": "image",
            "mime": mime,
            "data_uri": f"data:{mime};base64,{b64}",
        }
    elif ext == ".pdf":
        try:
            raw = path.read_bytes()
            b64 = base64.b64encode(raw).decode("ascii")
        except OSError:
            return {"name": path.name, "type": "error", "content": "（读取文件时出错）"}
        return {
            "name": path.name,
            "type": "pdf",
            "data_uri": f"data:{mime};base64,{b64}",
        }
    elif ext == ".xlsx":
        try:
            raw = path.read_bytes()
            b64 = base64.b64encode(raw).decode("ascii")
        except OSError:
            return {"name": path.name, "type": "error", "content": "（读取文件时出错）"}
        return {
            "name": path.name,
            "type": "xlsx",
            "data_b64": b64,
        }
    else:
        # 二进制 / 未知类型 — base64 下载链接
        try:
            raw = path.read_bytes()
            b64 = base64.b64encode(raw).decode("ascii")
        except OSError:
            return {"name": path.name, "type": "error", "content": "（读取文件时出错）"}
        return {
            "name": path.name,
            "type": "binary",
            "mime": mime,
            "data_uri": f"data:{mime};base64,{b64}",
        }


def load_previous_iteration(workspace: Path) -> dict[str, dict]:
    """加载上一轮迭代的评审反馈和输出。

    返回一个 run_id -> {"feedback": str, "outputs": list[dict]} 的映射。
    """
    result: dict[str, dict] = {}

    # 加载反馈
    feedback_map: dict[str, str] = {}
    feedback_path = workspace / "feedback.json"
    if feedback_path.exists():
        try:
            data = json.loads(feedback_path.read_text())
            feedback_map = {
                r["run_id"]: r["feedback"]
                for r in data.get("reviews", [])
                if r.get("feedback", "").strip()
            }
        except (json.JSONDecodeError, OSError, KeyError):
            pass

    # 加载运行记录（用于获取输出）
    prev_runs = find_runs(workspace)
    for run in prev_runs:
        result[run["id"]] = {
            "feedback": feedback_map.get(run["id"], ""),
            "outputs": run.get("outputs", []),
        }

    # 同时为有反馈但无对应运行记录的 run_id 补充条目
    for run_id, fb in feedback_map.items():
        if run_id not in result:
            result[run_id] = {"feedback": fb, "outputs": []}

    return result


def generate_html(
        runs: list[dict],
        skill_name: str,
        previous: dict[str, dict] | None = None,
        benchmark: dict | None = None,
) -> str:
    """生成完整的独立 HTML 页面，内含嵌入数据。"""
    template_path = Path(__file__).parent / "viewer.html"
    template = template_path.read_text()

    # 为模板构建 previous_feedback 和 previous_outputs 映射
    previous_feedback: dict[str, str] = {}
    previous_outputs: dict[str, list[dict]] = {}
    if previous:
        for run_id, data in previous.items():
            if data.get("feedback"):
                previous_feedback[run_id] = data["feedback"]
            if data.get("outputs"):
                previous_outputs[run_id] = data["outputs"]

    embedded = {
        "skill_name": skill_name,
        "runs": runs,
        "previous_feedback": previous_feedback,
        "previous_outputs": previous_outputs,
    }
    if benchmark:
        embedded["benchmark"] = benchmark

    data_json = json.dumps(embedded)

    return template.replace("/*__EMBEDDED_DATA__*/", f"const EMBEDDED_DATA = {data_json};")


# ---------------------------------------------------------------------------
# HTTP 服务器（仅使用标准库，零外部依赖）
# ---------------------------------------------------------------------------

def _kill_port(port: int) -> None:
    """结束占用指定端口的进程。"""
    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True, text=True, timeout=5,
        )
        for pid_str in result.stdout.strip().split("\n"):
            if pid_str.strip():
                try:
                    os.kill(int(pid_str.strip()), signal.SIGTERM)
                except (ProcessLookupError, ValueError):
                    pass
        if result.stdout.strip():
            time.sleep(0.5)
    except subprocess.TimeoutExpired:
        pass
    except FileNotFoundError:
        print("注意：未找到 lsof 命令，无法检查端口是否被占用", file=sys.stderr)


class ReviewHandler(BaseHTTPRequestHandler):
    """提供审查 HTML 页面并处理反馈保存的 HTTP 请求处理器。

    每次页面加载时重新生成 HTML，以便刷新浏览器即可获取新的评测输出，
    无需重启服务器。
    """

    def __init__(
            self,
            workspace: Path,
            skill_name: str,
            feedback_path: Path,
            previous: dict[str, dict],
            benchmark_path: Path | None,
            *args,
            **kwargs,
    ):
        self.workspace = workspace
        self.skill_name = skill_name
        self.feedback_path = feedback_path
        self.previous = previous
        self.benchmark_path = benchmark_path
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:
        """处理 GET 请求：返回审查页面或反馈数据。"""
        if self.path == "/" or self.path == "/index.html":
            # 每次请求时重新生成 HTML（重新扫描工作区以获取新输出）
            runs = find_runs(self.workspace)
            benchmark = None
            if self.benchmark_path and self.benchmark_path.exists():
                try:
                    benchmark = json.loads(self.benchmark_path.read_text())
                except (json.JSONDecodeError, OSError):
                    pass
            html = generate_html(runs, self.skill_name, self.previous, benchmark)
            content = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        elif self.path == "/api/feedback":
            data = b"{}"
            if self.feedback_path.exists():
                data = self.feedback_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        """处理 POST 请求：保存评审反馈。"""
        if self.path == "/api/feedback":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
                if not isinstance(data, dict) or "reviews" not in data:
                    raise ValueError("JSON 对象需包含 'reviews' 键")
                self.feedback_path.write_text(json.dumps(data, indent=2) + "\n")
                resp = b'{"ok":true}'
                self.send_response(200)
            except (json.JSONDecodeError, OSError, ValueError) as e:
                resp = json.dumps({"error": str(e)}).encode()
                self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)
        else:
            self.send_error(404)

    def log_message(self, format: str, *args: object) -> None:
        # 抑制请求日志，保持终端输出整洁
        pass


def main() -> None:
    """主入口：解析参数，发现运行记录，启动 HTTP 服务器或导出静态 HTML。"""
    parser = argparse.ArgumentParser(description="生成并启动评测审查页面")
    parser.add_argument("workspace", type=Path, help="工作区目录路径")
    parser.add_argument("--port", "-p", type=int, default=3117, help="服务器端口（默认：3117）")
    parser.add_argument("--skill-name", "-n", type=str, default=None, help="页面标题中显示的技能名称")
    parser.add_argument(
        "--previous-workspace", type=Path, default=None,
        help="上一轮迭代的工作区路径（用于展示旧输出和反馈作为参考）",
    )
    parser.add_argument(
        "--benchmark", type=Path, default=None,
        help="benchmark.json 文件路径，用于在基准测试标签页中展示",
    )
    parser.add_argument(
        "--static", "-s", type=Path, default=None,
        help="将独立 HTML 写入指定路径，而不启动服务器",
    )
    args = parser.parse_args()

    workspace = args.workspace.resolve()
    if not workspace.is_dir():
        print(f"错误：{workspace} 不是有效的目录", file=sys.stderr)
        sys.exit(1)

    runs = find_runs(workspace)
    if not runs:
        print(f"在 {workspace} 中未找到运行记录", file=sys.stderr)
        sys.exit(1)

    skill_name = args.skill_name or workspace.name.replace("-workspace", "")
    feedback_path = workspace / "feedback.json"

    previous: dict[str, dict] = {}
    if args.previous_workspace:
        previous = load_previous_iteration(args.previous_workspace.resolve())

    benchmark_path = args.benchmark.resolve() if args.benchmark else None
    benchmark = None
    if benchmark_path and benchmark_path.exists():
        try:
            benchmark = json.loads(benchmark_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    if args.static:
        html = generate_html(runs, skill_name, previous, benchmark)
        args.static.parent.mkdir(parents=True, exist_ok=True)
        args.static.write_text(html)
        print(f"\n  静态审查页面已写入：{args.static}\n")
        sys.exit(0)

    # 结束目标端口上可能已存在的进程
    port = args.port
    _kill_port(port)
    handler = partial(ReviewHandler, workspace, skill_name, feedback_path, previous, benchmark_path)
    try:
        server = HTTPServer(("127.0.0.1", port), handler)
    except OSError:
        # 杀掉进程后端口仍被占用 — 自动寻找一个空闲端口
        server = HTTPServer(("127.0.0.1", 0), handler)
        port = server.server_address[1]

    url = f"http://localhost:{port}"
    print(f"\n  评测审查器")
    print(f"  ─────────────────────────────────")
    print(f"  访问地址：  {url}")
    print(f"  工作区：    {workspace}")
    print(f"  反馈文件：  {feedback_path}")
    if previous:
        print(f"  上一版本：  {args.previous_workspace}（{len(previous)} 条运行记录）")
    if benchmark_path:
        print(f"  基准数据：  {benchmark_path}")
    print(f"\n  按 Ctrl+C 停止服务器。\n")

    webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
        server.server_close()


if __name__ == "__main__":
    main()
