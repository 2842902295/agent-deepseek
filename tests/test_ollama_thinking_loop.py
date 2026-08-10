"""
Ollama thinking 模式"无限思考"压测脚本（OpenAI 兼容接口版）

走 /v1/chat/completions（与生产 LLM 调用同一通道），SSE 流式解析。
对每组参数 profile 跑 N 次同一个 prompt，统计：
  - ok          : 正常输出（finish_reason=stop）
  - truncated   : 思考完了但正文被 max_tokens 截断（finish_reason=length 且已产出 content）
  - loop        : 一直在 thinking 没出 content 就被截断（疑似无限思考）
  - timeout     : 整体硬超时
  - error       : 网络/解析错误

可调参数集中在 PROFILES 字典里。OpenAI 标准字段直接生效，Ollama 扩展字段
（repeat_penalty / repeat_last_n / min_p / mirostat / top_k 等）由 Ollama
OpenAI 兼容层映射到底层 options。

用法:
    python tests/test_ollama_thinking_loop.py
    python tests/test_ollama_thinking_loop.py --profile baseline strong_repeat
    python tests/test_ollama_thinking_loop.py --runs 20 --prompt "9.11 和 9.9 哪个大?"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values


# ── 读取 .env 里的 LLM_* 配置（与 app/langchain/config.py 同名）─────────────
ROOT = Path(__file__).resolve().parents[1]
ENV = dotenv_values(ROOT / ".env")

BASE_URL = (ENV.get("LLM_BASE_URL") or os.getenv("LLM_BASE_URL") or "http://localhost:11434/v1").rstrip("/")
API_KEY = ENV.get("LLM_API_KEY") or os.getenv("LLM_API_KEY") or "ollama"
MODEL = ENV.get("LLM_MODEL") or os.getenv("LLM_MODEL") or "qwen3:8b"
DEFAULT_MAX_TOKENS = int(ENV.get("LLM_MAX_TOKENS") or os.getenv("LLM_MAX_TOKENS") or 10000)
CHAT_URL = f"{BASE_URL}/chat/completions"


# ── 参数 profile：围绕 temperature / frequency_penalty / presence_penalty 扫参 ──
# 这三个都是 OpenAI 标准字段，Ollama 兼容层原生支持。
#   temperature        : 越低越确定性，过低会让模型卡在同一路径上反复推
#   frequency_penalty  : [-2,2]，正值压低已出现 token 的频率，越大越抗循环
#   presence_penalty   : [-2,2]，正值鼓励引入新 token / 新话题，越大越抗卡死
# 想快速定位最优组合：先看 baseline 是不是真能复现 loop，再依次切到下面的 profile。
PROFILES: dict[str, dict[str, Any]] = {
    # ── 对照组 ───────────────────────────────────────────────────────────────
    "baseline": {
        # 不加惩罚，确认能复现无限思考
        "temperature": 0.7,
    },

    # ── 仅调 temperature ────────────────────────────────────────────────────
    "temp_0.3": {"temperature": 0.3},
    "temp_0.6": {"temperature": 0.6},
    "temp_0.9": {"temperature": 0.9},

    # ── 仅 frequency_penalty（看抗重复单独效果）────────────────────────────
    "freq_0.3": {"temperature": 0.6, "frequency_penalty": 0.3},
    "freq_0.5": {"temperature": 0.6, "frequency_penalty": 0.5},
    "freq_0.8": {"temperature": 0.6, "frequency_penalty": 0.8},
    "freq_1.2": {"temperature": 0.6, "frequency_penalty": 1.2},

    # ── 仅 presence_penalty ────────────────────────────────────────────────
    "pres_0.3": {"temperature": 0.6, "presence_penalty": 0.3},
    "pres_0.6": {"temperature": 0.6, "presence_penalty": 0.6},
    "pres_1.0": {"temperature": 0.6, "presence_penalty": 1.0},

    # ── 组合：frequency + presence（你说有效的方向，重点扫这一片）──────────
    "combo_light":  {"temperature": 0.6, "frequency_penalty": 0.3, "presence_penalty": 0.3},
    "combo_medium": {"temperature": 0.6, "frequency_penalty": 0.5, "presence_penalty": 0.3},
    "combo_strong": {"temperature": 0.6, "frequency_penalty": 0.8, "presence_penalty": 0.5},
    "combo_max":    {"temperature": 0.7, "frequency_penalty": 1.0, "presence_penalty": 0.6},

    # ── 推荐起点（生产建议值）────────────────────────────────────────────────
    "recommended":  {"temperature": 0.6, "frequency_penalty": 0.5, "presence_penalty": 0.3},
}


DEFAULT_PROMPTS = [
    # 经典踩坑题，qwen 系列 thinking 模式很容易在这里反复推
    "9.11 和 9.9 哪个数字大？请简短回答。",
    "strawberry 这个单词里有几个字母 r？请简短回答。",
    "一个农夫要带一只狼、一只羊和一棵白菜过河，船一次只能载农夫和一样东西，怎么过？给出最短方案。",
]


@dataclass
class RunStat:
    status: str = "error"
    thinking_chars: int = 0
    content_chars: int = 0
    elapsed: float = 0.0
    first_content_at: float | None = None
    finish_reason: str | None = None
    error: str | None = None


@dataclass
class ProfileStat:
    name: str
    runs: list[RunStat] = field(default_factory=list)

    def summary(self) -> str:
        n = len(self.runs)
        if n == 0:
            return f"{self.name}: no runs"
        cnt = {"ok": 0, "truncated": 0, "loop": 0, "timeout": 0, "error": 0}
        for r in self.runs:
            cnt[r.status] = cnt.get(r.status, 0) + 1
        ok_runs = [r for r in self.runs if r.status == "ok"]
        avg_elapsed = sum(r.elapsed for r in ok_runs) / len(ok_runs) if ok_runs else 0
        avg_think = sum(r.thinking_chars for r in ok_runs) / len(ok_runs) if ok_runs else 0
        return (
            f"{self.name:<16} | ok={cnt['ok']}/{n}  trunc={cnt['truncated']}  "
            f"loop={cnt['loop']}  timeout={cnt['timeout']}  err={cnt['error']}  "
            f"| avg(ok) elapsed={avg_elapsed:.1f}s thinking_chars={avg_think:.0f}"
        )


def _extract_thinking(delta: dict) -> str:
    """不同实现把思考内容放在不同字段里，全收一遍。"""
    for k in ("reasoning_content", "reasoning", "thinking"):
        v = delta.get(k)
        if v:
            return v
    return ""


def run_once(prompt: str, options: dict[str, Any], hard_timeout: float) -> RunStat:
    """对 OpenAI 兼容 /v1/chat/completions 发一次流式请求。"""
    body: dict[str, Any] = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "max_tokens": DEFAULT_MAX_TOKENS,
        # Ollama 兼容层接受 think 字段开启思考模式
        "think": True,
        **options,
    }
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

    stat = RunStat()
    t0 = time.monotonic()
    had_content = False
    try:
        with httpx.stream(
            "POST",
            CHAT_URL,
            json=body,
            headers=headers,
            timeout=httpx.Timeout(connect=10.0, read=hard_timeout, write=10.0, pool=10.0),
        ) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if time.monotonic() - t0 > hard_timeout:
                    stat.status = "timeout"
                    stat.elapsed = time.monotonic() - t0
                    return stat
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                choices = data.get("choices") or []
                if not choices:
                    continue
                ch = choices[0]
                delta = ch.get("delta") or {}
                think_delta = _extract_thinking(delta)
                content_delta = delta.get("content") or ""
                if think_delta:
                    stat.thinking_chars += len(think_delta)
                if content_delta:
                    if not had_content:
                        stat.first_content_at = time.monotonic() - t0
                        had_content = True
                    stat.content_chars += len(content_delta)
                fr = ch.get("finish_reason")
                if fr:
                    stat.finish_reason = fr
    except httpx.ReadTimeout:
        stat.status = "timeout"
        stat.elapsed = time.monotonic() - t0
        return stat
    except Exception as e:
        stat.status = "error"
        stat.error = f"{type(e).__name__}: {e}"
        stat.elapsed = time.monotonic() - t0
        return stat

    stat.elapsed = time.monotonic() - t0
    if stat.finish_reason == "stop":
        stat.status = "ok"
    elif stat.finish_reason == "length":
        stat.status = "truncated" if had_content else "loop"
    else:
        stat.status = "error"
        stat.error = f"unexpected finish_reason={stat.finish_reason!r}"
    return stat


def run_profile(name: str, options: dict[str, Any], prompts: list[str], runs: int, hard_timeout: float) -> ProfileStat:
    print(f"\n=== profile: {name}  options={options} ===")
    ps = ProfileStat(name=name)
    total = runs * len(prompts)
    i = 0
    for prompt in prompts:
        for _ in range(runs):
            i += 1
            stat = run_once(prompt, options, hard_timeout)
            ps.runs.append(stat)
            tag = stat.status.upper().ljust(9)
            extra = f"finish={stat.finish_reason}" if stat.finish_reason else (stat.error or "")
            print(
                f"  [{i:>2}/{total}] {tag} t={stat.elapsed:5.1f}s "
                f"think_chars={stat.thinking_chars:>6} content_chars={stat.content_chars:>5} {extra}"
            )
    print(ps.summary())
    return ps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", nargs="*", default=list(PROFILES.keys()),
                    help=f"要跑的 profile，默认全部：{list(PROFILES.keys())}")
    ap.add_argument("--runs", type=int, default=5, help="每个 prompt 重复次数（默认 5）")
    ap.add_argument("--prompt", action="append", help="自定义 prompt，可多次传入；不传则用内置三道题")
    ap.add_argument("--timeout", type=float, default=180.0, help="单次硬超时秒数（默认 180）")
    ap.add_argument("--base-url", help="覆盖 LLM_BASE_URL（不动 .env）")
    ap.add_argument("--api-key", help="覆盖 LLM_API_KEY")
    ap.add_argument("--model", help="覆盖 LLM_MODEL")
    args = ap.parse_args()

    global BASE_URL, API_KEY, MODEL, CHAT_URL
    if args.base_url:
        BASE_URL = args.base_url.rstrip("/")
        CHAT_URL = f"{BASE_URL}/chat/completions"
    if args.api_key:
        API_KEY = args.api_key
    if args.model:
        MODEL = args.model

    prompts = args.prompt if args.prompt else DEFAULT_PROMPTS

    print(f"target  : {CHAT_URL}")
    print(f"model   : {MODEL}")
    print(f"runs    : {args.runs} × {len(prompts)} prompt = {args.runs * len(prompts)} 次/profile")
    print(f"timeout : {args.timeout}s/次, 默认 max_tokens={DEFAULT_MAX_TOKENS}")

    stats: list[ProfileStat] = []
    for name in args.profile:
        if name not in PROFILES:
            print(f"!! 未知 profile: {name}，跳过", file=sys.stderr)
            continue
        stats.append(run_profile(name, PROFILES[name], prompts, args.runs, args.timeout))

    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    for ps in stats:
        print(ps.summary())


if __name__ == "__main__":
    main()
