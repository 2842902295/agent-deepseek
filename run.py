import argparse
import os

import uvicorn

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", dest="env_file", default=None, help="指定 .env 文件路径，例如 .env.test / .env.prod")
    args, _ = parser.parse_known_args()

    if args.env_file:
        os.environ["ENV_FILE"] = args.env_file

    try:
        # SQLite 不支持多进程并发写入，暂时使用单 worker
        # 如果需要多进程，请切换到 PostgreSQL 或 MySQL
        workers = 1

        # host/port 可被环境变量覆盖（桌面单机版用 127.0.0.1），默认 0.0.0.0:9999 不变
        host = os.getenv("DESKTOP_HOST", "0.0.0.0")
        port = int(os.getenv("DESKTOP_PORT", "9999"))

        uvicorn.run(
            "app:app",
            host=host,
            port=port,
            reload=False,
            # 只监听 app/ 源码：默认监听整个 CWD 会被 .agent_workspace 的高频文件变动
            # （技能物化/同步）拖垮甚至触发无意义重启
            reload_dirs=["app"],
            workers=workers
        )
    except KeyboardInterrupt:
        ...
