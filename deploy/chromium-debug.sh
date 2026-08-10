#!/bin/sh
# 幂等拉起 headless chromium（playwright 内置版，/usr/local/bin/chromium 软链），开放 CDP 调试端口，
# 供 browser-use（browser-harness daemon）附着。用法：chromium-debug [port]（默认 9222）
#
# 设计要点：
# - user-data-dir 用 $HOME/.config/chromium：browser-harness daemon 的 PROFILES 扫描列表含此目录，
#   DevToolsActivePort 会被自动发现，browser-use 无需任何额外参数即可附着
# - --no-sandbox：容器内无 SYS_ADMIN cap 时 chromium sandbox 会 fork 挂死，此参数彻底规避
#   （compose 里同时也加了 cap_add SYS_ADMIN，双保险）
# - --disable-dev-shm-usage：/dev/shm 不足也不崩溃（compose 也把 shm_size 提到了 512mb）
set -eu

PORT="${1:-9222}"
PROFILE_DIR="${HOME}/.config/chromium"
LOG_FILE="${PROFILE_DIR}/headless.log"
READY_URL="http://127.0.0.1:${PORT}/json/version"

mkdir -p "$PROFILE_DIR"

probe() {
    python -c 'import sys, urllib.request; urllib.request.urlopen(sys.argv[1], timeout=1)' "$READY_URL" >/dev/null 2>&1
}

if probe; then
    echo "✅ chromium 已在 :${PORT} 运行（CDP: ${READY_URL}）"
    exit 0
fi

nohup chromium \
    --headless=new \
    --no-sandbox \
    --disable-dev-shm-usage \
    --disable-gpu \
    --remote-debugging-port="${PORT}" \
    --user-data-dir="${PROFILE_DIR}" \
    about:blank >> "${LOG_FILE}" 2>&1 &

i=0
while [ "$i" -lt 30 ]; do
    if probe; then
        echo "✅ chromium 就绪（CDP: ${READY_URL}，日志: ${LOG_FILE}）"
        exit 0
    fi
    i=$((i + 1))
    sleep 1
done

echo "❌ chromium 30s 内未就绪，最后日志：" >&2
tail -n 20 "${LOG_FILE}" >&2 || true
exit 1
