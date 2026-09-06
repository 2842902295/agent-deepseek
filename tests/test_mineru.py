"""
MinerU 工具独立测试
用法：python -X utf8 tests/test_mineru.py
"""
import asyncio
import sys
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, str(Path(__file__).parent.parent))

TEST_FILE = Path(__file__).parent.parent / "docs" / "f84518242b1643aaa9eb36abff248402.png"


async def main():
    import httpx
    from app.settings import APP_SETTINGS as settings
    from app.utils.mineru import _UPLOAD_PATH

    print(f"上传文件：{TEST_FILE}")
    async with httpx.AsyncClient() as client:
        with open(TEST_FILE, "rb") as f:
            resp = await client.post(
                f"{settings.MINERU_BASE_URL}{_UPLOAD_PATH}",
                files={"files": (TEST_FILE.name, f, "image/png")},
                timeout=60,
            )
        resp.raise_for_status()
        paths = resp.json()
        print(f"上传结果：{paths!r}")

        # 直接测试 convert_to_markdown（通过 URL 方式）
        # 用本地文件 URL 不可行，改为直接调用内部函数测试
        from app.utils.mineru import _post_task, _stream_result

        server_path = paths[0]
        event_id = await _post_task(client, server_path)
        print(f"event_id：{event_id}")

        markdown = ""
        async for event in _stream_result(client, event_id):
            if event is None:
                continue
            if isinstance(event, list) and len(event) > 2:
                if event[2]:
                    markdown = event[2]
            elif isinstance(event, dict):
                if event.get("msg") == "process_completed":
                    data = event.get("output", {}).get("data", [])
                    if len(data) > 2 and data[2]:
                        markdown = data[2]
                    elif data and data[0]:
                        markdown = data[0]
                elif event.get("msg") == "process_errored":
                    print(f"ERROR: {event}")

        print(f"\n=== 最终 markdown（长度={len(markdown)}）===")
        print(markdown[:500] if markdown else "(空)")


if __name__ == "__main__":
    asyncio.run(main())
