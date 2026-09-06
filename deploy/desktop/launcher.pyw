# -*- coding: utf-8 -*-
"""
桌面单机版启动器（无黑框、秒开窗口、关窗即停）。

由 runtime\\pythonw.exe 加载（安装包快捷方式 / launcher.vbs 双击入口）。
生命周期对齐普通桌面应用——只有一个图标：

  双击图标 → 立即弹出 Edge 应用模式窗口（--app=，无地址栏/标签页）：
    * 后端已在运行：窗口直接打开应用页面
    * 后端未运行：窗口先显示本地 loading 页（秒开），launcher 后台依次
      起 Redis、起后端；loading 页自动探测端口，就绪后原地跳转进应用
  关闭窗口 → launcher 检测到 Edge 进程退出 → 自动停掉后端与内置 Redis

其他要点：
  - 互斥体防重复启动（第二次双击提示"已在运行"而不是弹技术细节）
  - 首选端口被其他程序占用时自动换空闲端口并回写 .env（不强制占端口）
  - 找不到 Edge 时回退默认浏览器（此时退化为"等就绪再开窗"，无关窗即停）
  - 开窗前清场：杀掉仍占着本应用专属 profile 的残留 Edge 进程。
    同一 --user-data-dir 被占用时，新 msedge.exe 会退化成"委托壳"——把开窗
    请求转发给既有实例后立即退出，launcher 的 wait() 秒回、误判关窗、错杀
    刚起的后端，而真正的窗口活在 launcher 观察不到的进程里（2026-08-12 实测
    复现的"卡死在 loading 页"根因）。profile 是安装目录下本应用专属的
    .edge_profile，清场安全、不伤用户自己的浏览器。

占位符 __APP_NAME__ 由 pack_desktop.py 打包时替换为实际应用名。
本文件必须保持 UTF-8 编码（python 直接解析，不受 bat 的 GBK 约束）。
"""
import ctypes
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
import webbrowser
import zlib

APP_NAME = '__APP_NAME__'
ROOT = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(ROOT, 'logs')
EDGE_PROFILE_DIR = os.path.join(ROOT, '.edge_profile')
CREATE_NO_WINDOW = 0x08000000
REDIS_WAIT_SECONDS = 15
APP_WAIT_SECONDS = 180
MB_ICONINFO = 0x40

# 停服务：按可执行文件路径前缀杀本目录内的后端/Redis（反斜杠结尾防误伤 D:\app2），
# 再按命令行里含本应用 profile 路径杀 Edge（关窗即停后不留僵尸浏览器进程）。
PS_KILL = (
    "$d=$env:STOP_ROOT;$p=$env:EDGE_PROFILE;"
    "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe' OR Name='redis-server.exe'\" |"
    "Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($d) } |"
    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue };"
    "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" |"
    "Where-Object { $_.CommandLine -and $_.CommandLine.Contains($p) } |"
    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
)

# 开窗前清场：只杀命令行里含本应用 profile 路径的 msedge（详见文件头说明）
PS_KILL_EDGE = (
    "$p=$env:EDGE_PROFILE;"
    "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" |"
    "Where-Object { $_.CommandLine -and $_.CommandLine.Contains($p) } |"
    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
)


def _log(line: str):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(os.path.join(LOG_DIR, 'launcher.log'), 'a', encoding='utf-8') as f:
            f.write(time.strftime('%Y-%m-%d %H:%M:%S ') + line + '\n')
    except Exception:
        pass


def _msg(text: str):
    try:
        ctypes.windll.user32.MessageBoxW(0, text, APP_NAME, MB_ICONINFO)
    except Exception:
        pass


def read_port() -> int:
    """从安装目录 .env 读 DESKTOP_PORT，缺失/异常回落 9999。"""
    try:
        with open(os.path.join(ROOT, '.env'), encoding='utf-8', errors='ignore') as f:
            for line in f:
                s = line.strip()
                if s.startswith('DESKTOP_PORT='):
                    value = s.split('=', 1)[1].strip()
                    if value.isdigit():
                        return int(value)
    except Exception:
        pass
    return 9999


def port_alive(port: int) -> bool:
    """端口有任何 HTTP 响应（含 4xx/5xx）即视为服务在跑。"""
    try:
        urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=2)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def port_free(port: int) -> bool:
    """端口无人监听（可绑定）。与 port_alive 互补：区分"我们的服务"与"被占用"。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(('127.0.0.1', port))
            return True
        except OSError:
            return False


def find_free_port(preferred: int) -> int:
    """从 preferred 起向上找空闲端口；实在没有就让系统随机分配。"""
    for p in range(preferred, preferred + 100):
        if port_free(p):
            return p
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def update_env_port(port: int):
    """把新端口回写 .env 的 DESKTOP_PORT（下次启动沿用，URL 稳定）。"""
    env_path = os.path.join(ROOT, '.env')
    try:
        text = open(env_path, encoding='utf-8', errors='ignore').read()
        lines = text.splitlines()
        replaced = False
        for i, line in enumerate(lines):
            if line.strip().startswith('DESKTOP_PORT='):
                lines[i] = f'DESKTOP_PORT={port}'
                replaced = True
        if not replaced:
            lines.append(f'DESKTOP_PORT={port}')
        open(env_path, 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
    except Exception as e:
        _log(f'update_env_port failed: {e!r}')


def redis_alive() -> bool:
    cli = os.path.join(ROOT, 'redis', 'redis-cli.exe')
    if not os.path.exists(cli):
        return False
    try:
        out = subprocess.run([cli, '-p', '6379', 'ping'], capture_output=True,
                             creationflags=CREATE_NO_WINDOW, timeout=5)
        return b'PONG' in out.stdout
    except Exception:
        return False


def start_redis_if_needed():
    if redis_alive():
        return
    redis_dir = os.path.join(ROOT, 'redis')
    server = os.path.join(redis_dir, 'redis-server.exe')
    conf = os.path.join(redis_dir, 'redis.windows.conf')
    if not os.path.exists(server):
        return
    subprocess.Popen([server, conf], cwd=redis_dir,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=CREATE_NO_WINDOW)
    # 0.2s 粒度轮询（总时长上限仍为 REDIS_WAIT_SECONDS）：redis 通常 <0.5s 就绪，
    # 旧版 sleep(1) 起步就白等一整秒，且后端必须等 redis 确认后才启动，白等直接传导给启动时间
    for _ in range(int(REDIS_WAIT_SECONDS / 0.2)):
        time.sleep(0.2)
        if redis_alive():
            break
    _log('redis: ' + ('ok' if redis_alive() else 'timeout'))


def start_backend():
    backend_log = open(os.path.join(LOG_DIR, 'backend.log'), 'a', encoding='utf-8')
    subprocess.Popen([os.path.join(ROOT, 'runtime', 'python.exe'),
                      os.path.join(ROOT, 'run.py')],
                     cwd=ROOT, stdout=backend_log, stderr=subprocess.STDOUT,
                     creationflags=CREATE_NO_WINDOW)


def wait_ready(port: int, timeout: int = APP_WAIT_SECONDS) -> bool:
    for _ in range(timeout):
        time.sleep(1)
        if port_alive(port):
            return True
    return False


def _find_edge() -> str | None:
    candidates = [
        os.path.join(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'),
                     'Microsoft', 'Edge', 'Application', 'msedge.exe'),
        os.path.join(os.environ.get('ProgramFiles', r'C:\Program Files'),
                     'Microsoft', 'Edge', 'Application', 'msedge.exe'),
        os.path.join(os.environ.get('LocalAppData', ''),
                     'Microsoft', 'Edge', 'Application', 'msedge.exe'),
    ]
    for exe in candidates:
        if exe and os.path.exists(exe):
            return exe
    return None


def kill_stale_edge():
    """杀掉仍占用本应用专属 profile 的残留 Edge 进程（开窗前必做，见文件头说明）。"""
    env = dict(os.environ, EDGE_PROFILE=EDGE_PROFILE_DIR)
    try:
        subprocess.run(['powershell', '-NoProfile', '-Command', PS_KILL_EDGE],
                       env=env, capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=30)
        _log('stale edge instances cleared')
    except Exception as e:
        _log(f'kill_stale_edge failed: {e!r}')


def open_app_window(port: int, ready: bool) -> subprocess.Popen | None:
    """打开 Edge 应用模式窗口，返回其进程句柄（关窗检测用）。

    ready=False 时先开本地 loading 页（秒开），由页面 JS 探测端口就绪后原地跳转。
    独立 --user-data-dir 保证：窗口即独立实例，关窗进程必退出（launcher 据此停服务）。
    调用前必须先 kill_stale_edge()，否则 profile 被占时返回的是秒退的委托壳。
    """
    exe = _find_edge()
    if not exe:
        return None
    if ready:
        url = f'http://localhost:{port}/'
    else:
        root_posix = ROOT.replace('\\', '/')
        url = f'file:///{root_posix}/loading.html?port={port}'
    _log(f'open edge app window: {url}')
    return subprocess.Popen(
        [exe, f'--app={url}', f'--user-data-dir={EDGE_PROFILE_DIR}',
         '--no-first-run', '--no-default-browser-check'],
        creationflags=CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def stop_services():
    """关掉本安装目录内的后端、Redis 与 Edge 应用窗口（绝不误伤目录外进程）。"""
    env = dict(os.environ, STOP_ROOT=ROOT + '\\', EDGE_PROFILE=EDGE_PROFILE_DIR)
    try:
        subprocess.run(['powershell', '-NoProfile', '-Command', PS_KILL],
                       env=env, capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=60)
        _log('services stopped')
    except Exception as e:
        _log(f'stop_services failed: {e!r}')


def _acquire_mutex() -> bool:
    """按安装路径生成互斥体名（支持多副本共存）。已被占用返回 False。"""
    suffix = format(zlib.crc32(ROOT.lower().encode()) & 0xFFFFFFFF, '08X')
    ctypes.windll.kernel32.CreateMutexW(None, False, f'CesiFastAdmin_Desktop_Launcher_{suffix}')
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def main():
    os.makedirs(LOG_DIR, exist_ok=True)

    if not _acquire_mutex():
        _msg('应用已在运行中，请在任务栏查看它的窗口。')
        return

    # 开窗前清场：残留的 Edge（上次异常退出 / 窗口未关）会让新窗口委托到旧进程，
    # launcher 将失去对窗口生命周期的观察（详见文件头"开窗前清场"说明）
    kill_stale_edge()

    port = read_port()

    if port_alive(port):
        # 服务已在跑（上次未正常关窗 / 手动起的）：直接开窗并接管生命周期
        _log(f'attach: port={port} already alive')
        edge = open_app_window(port, ready=True)
        if edge is None:
            webbrowser.open(f'http://localhost:{port}/')  # 无 Edge：仅开窗，不接管
            return
        edge.wait()
        stop_services()
        return

    _log(f'start: preferred port={port}')

    # 首选端口被其他程序占用 → 自动换空闲端口并回写 .env（不强制占端口）
    if not port_free(port):
        new_port = find_free_port(port + 1)
        update_env_port(new_port)
        _log(f'port {port} occupied by another program, switched to {new_port}')
        port = new_port

    # 秒开：先弹 loading 窗口（本地文件，立即可见），后台再起 Redis / 后端
    edge = open_app_window(port, ready=False)

    start_redis_if_needed()
    start_backend()

    if edge is None:
        # 无 Edge 回退路径：等就绪后用默认浏览器打开（无法感知关窗，不做关窗即停）
        if wait_ready(port):
            _log('ready (fallback browser)')
            webbrowser.open(f'http://localhost:{port}/')
        else:
            _log('timeout')
            _msg(f'后端服务未能在 {APP_WAIT_SECONDS} 秒内就绪。\n请查看安装目录下 logs\\backend.log 后重试。')
        return

    # Edge 应用窗口路径：等用户关窗（loading 页会自行跳转进应用），然后停服务。
    # 委托壳兜底：正常根进程不会秒退；若 5 秒内就退出，说明清场未净（如杀毒软件拦截
    # PowerShell）导致委托还是发生了——此时绝不能走 stop_services（真正的窗口还活在
    # 旧进程里，杀了后端用户就永远卡在 loading 页）。保留刚启动的服务，提示用户
    # 稍后再双击（走 attach 路径直接进应用）。
    try:
        code = edge.wait(timeout=5)
        _log(f'edge exited in 5s (code={code}); delegation stub suspected — keeping services alive')
        _msg('窗口接管异常，但服务已在后台启动；请稍等片刻后再次双击图标进入应用。')
        return
    except subprocess.TimeoutExpired:
        pass  # 5 秒后仍存活 = 真根进程，进入正常"等关窗"流程
    _log('waiting for app window to close…')
    edge.wait()
    _log('app window closed')
    stop_services()


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        _log(f'error: {e!r}')
        _msg(f'启动失败：{e}')
