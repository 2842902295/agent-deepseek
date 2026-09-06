"""
诊断脚本：定位 DashScope embedding 接口 400 错误
直接调 langchain_openai，不经 app/ 包装，避开 app/__init__.py 副作用
"""
import asyncio
import os
import sys
import traceback
from pathlib import Path

# 加载 .env，确保 EMBEDDING_* 进入环境
from dotenv import load_dotenv  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


async def main():
    base_url = os.environ.get("EMBEDDING_BASE_URL")
    api_key = os.environ.get("EMBEDDING_API_KEY")
    model = os.environ.get("EMBEDDING_MODEL")
    dim = os.environ.get("EMBEDDING_DIMENSION")
    print(f"base_url  = {base_url}")
    print(f"model     = {model}")
    print(f"dimension = {dim}")
    print()

    # 复测：临时强制切到 v3，看是否同样报错
    override = os.environ.get("OVERRIDE_MODEL")
    if override:
        model = override
        print(f"[override] model = {model}")
        print()

    from langchain_openai import OpenAIEmbeddings
    embeddings = OpenAIEmbeddings(
        model=model,
        base_url=base_url,
        api_key=api_key,
        check_embedding_ctx_length=False,  # ← 关键：不让 langchain 用 tiktoken 切 token
    )

    cases = [
        ("单条普通文本", ["标准名称：测试标准\n适用范围：通用"]),
        ("批量 5 条", [f"标准名称：测试{i}\n适用范围：通用" for i in range(5)]),
        ("批量 10 条", [f"标准名称：测试{i}\n适用范围：通用" for i in range(10)]),
        ("批量 11 条（直接打）", [f"标准名称：测试{i}\n适用范围：通用" for i in range(11)]),
        ("批量 50 条（直接打）", [f"标准名称：测试{i}\n适用范围：通用" for i in range(50)]),
    ]

    for name, texts in cases:
        try:
            print(f"[CASE] {name}（{len(texts)} 条）...", flush=True)
            result = await embeddings.aembed_documents(texts)
            print(f"  -> OK，返回 {len(result)} 条，维度={len(result[0]) if result else 0}")
        except Exception as e:
            msg = str(e)
            if len(msg) > 400:
                msg = msg[:400] + "..."
            print(f"  -> FAILED: {type(e).__name__}: {msg}")
        print()

    # 再走一遍 provider 层（验证分块）
    print("=" * 60)
    print("provider.embed_texts 层（含自动分块）")
    print("=" * 60)
    # 直接 import 模块文件，绕过 app/__init__.py 的副作用
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "ep_mod", str(ROOT / "app" / "langchain" / "embedding_providers.py")
    )
    # 但 ep_mod 内 import app.langchain.config 仍会触发副作用，干脆直接复用主测的 OpenAIEmbeddings 客户端做分块对照
    batch_size = int(os.environ.get("EMBEDDING_BATCH_SIZE", "10"))
    print(f"模拟分块 batch_size = {batch_size}")
    big = [f"标准名称：测试{i}\n适用范围：通用" for i in range(100)]
    try:
        all_emb: list = []
        for s in range(0, len(big), batch_size):
            chunk = big[s:s + batch_size]
            part = await embeddings.aembed_documents(chunk)
            all_emb.extend(part)
        print(f"  -> OK，分块累计 {len(all_emb)} 条")
    except Exception as e:
        msg = str(e)
        if len(msg) > 400:
            msg = msg[:400] + "..."
        print(f"  -> FAILED: {type(e).__name__}: {msg}")


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    asyncio.run(main())
