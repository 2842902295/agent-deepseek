#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Windows 桌面单机版打包脚本

像 pack_deploy.py 一样一键执行，产出一个可双击安装的 setup.exe（Inno Setup），
最终用户安装后即可在自己电脑上运行；数据库、大模型保持远程不变。

用法（在 Windows 开发机上）：
    python pack_desktop.py

产物（均在 desktop_dist/ 下）：
    CesiFastAdmin/                      绿色免安装目录（start.bat 直接可用）
    CesiFastAdmin-setup-<时间戳>.exe     Inno Setup 安装包（需本机已装 Inno Setup 6）

整体思路（与 Docker「venv 烘焙 + 源码挂载」同范式，不用 PyInstaller）：
  1. pnpm build 前端 → web/dist
  2. 嵌入式 Python(python-3.12-embed) + 按 pdm.lock 导出并剔除重型件后 pip 安装依赖 → runtime/
  3. 便携 Redis(redis-server.exe) → redis/（登录一次性 RSA 私钥依赖它，不可省）
  4. 暂存应用树 + 生成桌面 .env（远程库/模型沿用根 .env，仅覆盖本地项）
  5. ISCC 编译安装包

被剔除的重型件：torch / sentence-transformers / triton / nvidia-*（多 GB 主因，
核心版 app/ 从不 import）；可选次级 crawl4ai / playwright（浏览器自动化 skill 随之不可用）。
"""
import fnmatch
import os
import pathlib
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from datetime import datetime

BASE_PATH = pathlib.Path(__file__).resolve().parent
DESKTOP_DIR = BASE_PATH / 'deploy' / 'desktop'
OUTPUT_DIR = BASE_PATH / 'desktop_dist'
CACHE_DIR = OUTPUT_DIR / '_cache'           # 下载缓存（python zip / get-pip / redis zip）
APP_DIR_NAME = 'CesiFastAdmin'
APP_ROOT = OUTPUT_DIR / APP_DIR_NAME        # 暂存出的完整应用树

PYTHON_VERSION = '3.12.7'
PYTHON_EMBED_NAME = f'python-{PYTHON_VERSION}-embed-amd64.zip'
PYTHON_EMBED_URLS = [
    f'https://mirrors.huaweicloud.com/python/{PYTHON_VERSION}/{PYTHON_EMBED_NAME}',
    f'https://www.python.org/ftp/python/{PYTHON_VERSION}/{PYTHON_EMBED_NAME}',
]
GET_PIP_URL = 'https://bootstrap.pypa.io/get-pip.py'
REDIS_VERSION = '5.0.14.1'
REDIS_ZIP_NAME = f'Redis-x64-{REDIS_VERSION}.zip'
REDIS_URLS = [
    f'https://github.com/tporadowski/redis/releases/download/v{REDIS_VERSION}/{REDIS_ZIP_NAME}',
]
PIP_INDEX = 'https://pypi.tuna.tsinghua.edu.cn/simple'

INNO_CANDIDATES = [
    os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)') + r'\Inno Setup 6\ISCC.exe',
    os.environ.get('ProgramFiles', r'C:\Program Files') + r'\Inno Setup 6\ISCC.exe',
]


# ───────────────────────────── 交互（复用 pack_deploy 的读键方案） ─────────────────────────────
def _read_key() -> str:
    if os.name == 'nt':
        import msvcrt
        ch = msvcrt.getwch()
        if ch == '\x03':
            return 'ctrl-c'
        if ch in ('\r', '\n'):
            return 'enter'
        if ch in ('\x00', '\xe0'):
            ch2 = msvcrt.getwch()
            return {'H': 'up', 'P': 'down'}.get(ch2, 'special')
        if ch == '\x1b':
            return 'esc'
        return ch.lower()
    import termios, tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = os.read(fd, 1)
        if ch == b'\x03':
            return 'ctrl-c'
        if ch in (b'\r', b'\n'):
            return 'enter'
        if ch == b'\x1b':
            seq = os.read(fd, 2)
            return {'[A': 'up', '[B': 'down'}.get(seq.decode('latin1'), 'esc')
        return ch.decode('utf-8', errors='ignore').lower()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def prompt_choice(question: str, options: list[tuple[str, str]], default_index: int = 0) -> str:
    """options 为 [(value, label), ...]，返回选中 value。方向键优先，退化序号，非交互取默认。"""
    print()
    print(question)
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if not interactive:
        value, label = options[default_index]
        for i, (_, lbl) in enumerate(options, 1):
            print(f'  {i}) {lbl}' + (' (默认)' if i - 1 == default_index else ''))
        print(f'[非交互模式] 使用默认: {label}')
        return value

    cursor = default_index
    print('（↑/↓ 选择，Enter 确认，q/Esc 用默认）')

    def render(first: bool):
        if not first:
            sys.stdout.write(f'\x1b[{len(options)}A')
        for i, (_, lbl) in enumerate(options):
            line = ('> ' if i == cursor else '  ') + lbl
            if i == cursor:
                line = f'\x1b[36m{line}\x1b[0m'
            sys.stdout.write('\x1b[2K' + line + '\n')
        sys.stdout.flush()

    render(True)
    while True:
        key = _read_key()
        if key == 'up':
            cursor = (cursor - 1) % len(options)
            render(False)
        elif key == 'down':
            cursor = (cursor + 1) % len(options)
            render(False)
        elif key == 'enter':
            value, label = options[cursor]
            print(f'已选择: {label}')
            return value
        elif key in ('q', 'esc', 'ctrl-c'):
            value, label = options[default_index]
            print(f'已取消，使用默认: {label}')
            return value


def collect_options() -> dict:
    print('=' * 60)
    print('桌面单机版打包 - 配置选择')
    print('=' * 60)

    port = prompt_choice('服务端口（安装后访问 http://localhost:<端口>）：', [
        ('9999', '9999（默认，沿用项目约定）'),
        ('8000', '8000'),
        ('9527', '9527'),
    ], 0)

    rebuild_runtime = prompt_choice('是否重新构建 Python 运行环境 runtime/（首次必须构建；后续可复用缓存加速）：', [
        ('no', '复用已有 runtime/（快，日常打包）'),
        ('yes', '重新构建（首次 / 依赖变更后）'),
    ], 0)

    build_frontend = prompt_choice('是否重新构建前端 web/dist：', [
        ('yes', '构建（默认，保证最新）'),
        ('no', '跳过（web/dist 已是最新时省时间）'),
    ], 0)

    strip_secondary = prompt_choice('是否剔除次级浏览器依赖（crawl4ai/playwright，核心版用不到，省约百 MB）：', [
        ('yes', '剔除（默认，核心版）'),
        ('no', '保留（需要浏览器自动化时）'),
    ], 0)

    return {
        'port': port,
        'rebuild_runtime': rebuild_runtime == 'yes',
        'build_frontend': build_frontend == 'yes',
        'strip_secondary': strip_secondary,
    }


# ───────────────────────────── 基础工具 ─────────────────────────────
def run(cmd: list[str], desc: str, shell: bool | None = None, cwd: pathlib.Path | None = None):
    print(f'\n[执行] {desc}\n  $ {" ".join(str(c) for c in cmd)}')
    if shell is None:
        shell = os.name == 'nt'
    result = subprocess.run([str(c) for c in cmd], shell=shell, cwd=str(cwd) if cwd else None)
    if result.returncode != 0:
        print(f'[错误] 命令失败，退出码 {result.returncode}')
        sys.exit(1)


def download(urls: list[str], dest: pathlib.Path, desc: str):
    """带镜像回退的下载；已存在则跳过。"""
    if dest.exists() and dest.stat().st_size > 0:
        print(f'  [缓存] {desc} 已存在，跳过下载')
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    for url in urls:
        try:
            print(f'  [下载] {desc}\n    {url}')
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=120) as resp, open(dest, 'wb') as f:
                shutil.copyfileobj(resp, f)
            size_mb = dest.stat().st_size / (1024 * 1024)
            print(f'    完成 ({size_mb:.1f} MB)')
            return
        except Exception as e:
            print(f'    失败: {e}，尝试下一个源…')
    print(f'[错误] {desc} 下载失败（所有源均不可用）')
    sys.exit(1)


# ───────────────────────────── 依赖剔除名单 ─────────────────────────────
def load_denylist() -> tuple[list[str], list[str]]:
    """返回 (主剔除, 次级剔除)。支持 `前缀*` 通配；[secondary] 段为次级。"""
    primary: list[str] = []
    secondary: list[str] = []
    current = primary
    for raw in (DESKTOP_DIR / 'requirements_denylist.txt').read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.lower() == '[secondary]':
            current = secondary
            continue
        if line.startswith('#'):
            continue
        current.append(line.lower())
    return primary, secondary


def _denied(pkg: str, deny: list[str]) -> bool:
    pkg = pkg.lower()
    for pat in deny:
        if pat.endswith('*'):
            if pkg.startswith(pat[:-1]):
                return True
        elif pkg == pat:
            return True
    return False


def filter_requirements(src: pathlib.Path, dst: pathlib.Path, deny: list[str]) -> int:
    """从 pdm 导出的全量 pin 清单里剔除 deny 包，返回剔除数量。"""
    removed = 0
    out_lines = []
    for line in src.read_text(encoding='utf-8').splitlines():
        s = line.strip()
        # 包名是行首到 == / >= / [ / ; 之前的部分
        m = re.match(r'^\s*([A-Za-z0-9][A-Za-z0-9._-]*)', s)
        if m and _denied(m.group(1), deny):
            removed += 1
            print(f'    [剔除] {s}')
            continue
        out_lines.append(line)
    dst.write_text('\n'.join(out_lines) + '\n', encoding='utf-8')
    return removed


# ───────────────────────────── 步骤 1：构建前端 ─────────────────────────────
def build_frontend():
    print('\n=== Step 1: 构建前端 ===')
    web_dir = BASE_PATH / 'web'
    if not (web_dir / 'package.json').exists():
        print('[错误] 未找到 web/package.json')
        sys.exit(1)
    run(['pnpm', '--dir', str(web_dir), 'install'], '安装前端依赖')
    run(['pnpm', '--dir', str(web_dir), 'build'], '构建前端（vite build --mode prod）')
    if not (web_dir / 'dist' / 'index.html').exists():
        print('[错误] pnpm build 完成但 web/dist/index.html 不存在')
        sys.exit(1)
    print('  前端构建完成: web/dist/')


# ───────────────────────────── 步骤 2：嵌入式 Python runtime ─────────────────────────────
def _runtime_python() -> pathlib.Path:
    return APP_ROOT / 'runtime' / 'python.exe'


def prepare_runtime(rebuild: bool, strip_secondary: bool):
    runtime_dir = APP_ROOT / 'runtime'
    py = _runtime_python()
    marker = runtime_dir / '.deps_installed'

    if rebuild and runtime_dir.exists():
        print('  [重建] 删除旧 runtime/')
        shutil.rmtree(runtime_dir, ignore_errors=True)

    if py.exists() and marker.exists() and not rebuild:
        print('\n=== Step 2: Python runtime（复用缓存，跳过重建）===')
        return

    print('\n=== Step 2: 准备嵌入式 Python runtime ===')
    runtime_dir.mkdir(parents=True, exist_ok=True)

    # 2.1 下载并解压嵌入式 Python
    embed_zip = CACHE_DIR / PYTHON_EMBED_NAME
    download(PYTHON_EMBED_URLS, embed_zip, f'嵌入式 Python {PYTHON_VERSION}')
    print('  [解压] 嵌入式 Python → runtime/')
    with zipfile.ZipFile(embed_zip) as zf:
        zf.extractall(runtime_dir)

    # 2.2 改写 ._pth：启用 site、加入 install 根目录(..)与 Lib\site-packages
    pth_files = list(runtime_dir.glob('python*._pth'))
    zip_files = list(runtime_dir.glob('python3*.zip'))
    if not pth_files:
        print('[错误] runtime/ 中未找到 python*._pth，嵌入式 Python 解压异常')
        sys.exit(1)
    zip_name = zip_files[0].name if zip_files else f'python{PYTHON_VERSION.replace(".", "")[:3]}.zip'
    pth_content = f'{zip_name}\n.\n..\nLib\\site-packages\n\nimport site\n'
    pth_files[0].write_text(pth_content, encoding='utf-8')
    print(f'  [配置] {pth_files[0].name} 已启用 site 并加入 install 根目录(..)')

    # 2.3 引导 pip
    get_pip = CACHE_DIR / 'get-pip.py'
    download([GET_PIP_URL], get_pip, 'get-pip.py')
    run([str(py), str(get_pip), '--no-warn-script-location', '--disable-pip-version-check', '-i', PIP_INDEX],
        '引导 pip 进 runtime', shell=False)

    # 2.3.1 嵌入式 Python 默认不带 setuptools/wheel，源码构建（sdist）的包需要它们兜底
    run([str(py), '-m', 'pip', 'install', '--no-warn-script-location', '--disable-pip-version-check',
         '-i', PIP_INDEX, 'setuptools', 'wheel'],
        '安装 setuptools/wheel（源码构建兜底）', shell=False)

    # 2.4 导出依赖并剔除重型件
    req_full = CACHE_DIR / 'requirements_full.txt'
    req_desktop = CACHE_DIR / 'requirements_desktop.txt'
    run(['pdm', 'export', '-f', 'requirements', '--no-hashes', '--prod', '-o', str(req_full)],
        'pdm 导出全量依赖（完整 pin 闭包）', cwd=BASE_PATH)
    primary, secondary = load_denylist()
    deny = primary + (secondary if strip_secondary else [])
    removed = filter_requirements(req_full, req_desktop, deny)
    print(f'  [依赖] 剔除 {removed} 个重型包（{"含" if strip_secondary else "不含"}次级）')

    # 2.5 安装依赖（--no-deps：闭包已完整，避免回拉 torch）
    run([str(py), '-m', 'pip', 'install', '--no-deps', '--no-warn-script-location',
         '--disable-pip-version-check', '-i', PIP_INDEX, '-r', str(req_desktop)],
        'pip 安装精简依赖到 runtime', shell=False)

    marker.write_text('ok', encoding='utf-8')
    print('  runtime 准备完成')


# ───────────────────────────── 步骤 3：便携 Redis ─────────────────────────────
def prepare_redis():
    redis_dir = APP_ROOT / 'redis'
    server = redis_dir / 'redis-server.exe'
    if server.exists():
        print('\n=== Step 3: Redis（已存在，跳过）===')
        return
    print('\n=== Step 3: 准备便携 Redis ===')
    redis_dir.mkdir(parents=True, exist_ok=True)

    redis_zip = CACHE_DIR / REDIS_ZIP_NAME
    download(REDIS_URLS, redis_zip, f'Redis {REDIS_VERSION} (Windows)')
    with zipfile.ZipFile(redis_zip) as zf:
        for member in ('redis-server.exe', 'redis-cli.exe'):
            data = zf.read(member)
            (redis_dir / member).write_bytes(data)
            print(f'  [提取] {member}')
    shutil.copy2(DESKTOP_DIR / 'redis.windows.conf', redis_dir / 'redis.windows.conf')
    print('  [配置] redis.windows.conf')


# ───────────────────────────── 步骤 4：暂存应用树 ─────────────────────────────
def _ignore(dirpath, names):
    pats = ('__pycache__', '*.pyc', '*.pyo', '.git', 'node_modules', '*.log',
            '.pytest_cache', '.ruff_cache', '*.egg-info')
    return {n for n in names if any(fnmatch.fnmatch(n, p) for p in pats)}


def _copytree(src: pathlib.Path, dst: pathlib.Path):
    if not src.exists():
        print(f'  [跳过] {src.relative_to(BASE_PATH)} 不存在')
        return
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst, ignore=_ignore)
    print(f'  [+] {dst.relative_to(APP_ROOT)}/')


def _parse_env_overrides(text: str) -> list[tuple[str, str]]:
    pairs = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith('#'):
            continue
        m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$', s)
        if m:
            pairs.append((m.group(1), m.group(2)))
    return pairs


def generate_desktop_env(port: str) -> str:
    """以根 .env 为底，应用 env.desktop 覆盖项（含端口占位替换），生成安装目录 .env 内容。"""
    overrides_text = (DESKTOP_DIR / 'env.desktop').read_text(encoding='utf-8').replace('__APP_PORT__', port)
    overrides = _parse_env_overrides(overrides_text)
    override_map = dict(overrides)

    root_env = (BASE_PATH / '.env').read_text(encoding='utf-8')
    consumed = set()
    out_lines = []
    for line in root_env.splitlines():
        if not line.strip().startswith('#'):
            m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\s*=', line)
            if m and m.group(1) in override_map:
                key = m.group(1)
                out_lines.append(f'{key}={override_map[key]}')
                consumed.add(key)
                continue
        out_lines.append(line)

    # 追加根 .env 里没有的覆盖键（如 SERVE_FRONTEND / DESKTOP_HOST / DESKTOP_PORT）
    appended = [f'{k}={v}' for k, v in overrides if k not in consumed]
    if appended:
        out_lines.append('')
        out_lines.append('# ===== 桌面单机版覆盖项 =====')
        out_lines.extend(appended)
    return '\n'.join(out_lines) + '\n'


def stage_tree(options: dict):
    print('\n=== Step 4: 暂存应用树 ===')
    APP_ROOT.mkdir(parents=True, exist_ok=True)

    _copytree(BASE_PATH / 'app', APP_ROOT / 'app')
    shutil.copy2(BASE_PATH / 'run.py', APP_ROOT / 'run.py')
    print('  [+] run.py')
    _copytree(BASE_PATH / 'web' / 'dist', APP_ROOT / 'web' / 'dist')
    _copytree(BASE_PATH / 'static', APP_ROOT / 'static')
    _copytree(BASE_PATH / 'migrations', APP_ROOT / 'migrations')
    _copytree(BASE_PATH / '.agent_workspace' / '.agent_skills',
              APP_ROOT / '.agent_workspace' / '.agent_skills')

    # 生成桌面 .env
    env_text = generate_desktop_env(options['port'])
    (APP_ROOT / '.env').write_text(env_text, encoding='utf-8')
    print('  [+] .env（远程库/模型沿用根 .env，本地项已覆盖）')

    # 启动 / 停止脚本（替换端口占位）
    for name in ('start.bat', 'stop.bat'):
        text = (DESKTOP_DIR / name).read_text(encoding='utf-8').replace('__APP_PORT__', options['port'])
        (APP_ROOT / name).write_text(text, encoding='utf-8')
        print(f'  [+] {name}')


# ───────────────────────────── 步骤 5：编译安装包 ─────────────────────────────
def _find_iscc() -> str | None:
    for cand in INNO_CANDIDATES:
        if os.path.exists(cand):
            return cand
    found = shutil.which('ISCC') or shutil.which('iscc')
    return found


def compile_installer() -> str | None:
    print('\n=== Step 5: 编译 Inno Setup 安装包 ===')
    iscc = _find_iscc()
    if not iscc:
        print('  [跳过] 未找到 Inno Setup（ISCC.exe）。请安装 Inno Setup 6 后重跑本步骤，')
        print(f'         或直接使用绿色目录：{APP_ROOT}')
        return None

    run([iscc, str(DESKTOP_DIR / 'installer.iss')], 'ISCC 编译安装包', shell=False)
    produced = OUTPUT_DIR / 'CesiFastAdmin-setup.exe'
    if not produced.exists():
        print('  [警告] 未找到编译产物 CesiFastAdmin-setup.exe')
        return None
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    final = OUTPUT_DIR / f'CesiFastAdmin-setup-{ts}.exe'
    produced.replace(final)
    size_mb = final.stat().st_size / (1024 * 1024)
    print(f'  安装包: {final.name}  ({size_mb:.1f} MB)')
    return final.name


# ───────────────────────────── 主流程 ─────────────────────────────
def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    CACHE_DIR.mkdir(exist_ok=True)

    options = collect_options()

    print()
    print('=' * 60)
    print('开始打包桌面单机版…')
    print(f'输出目录: {OUTPUT_DIR}')
    print(f'配置: 端口={options["port"]} 重建runtime={options["rebuild_runtime"]} '
          f'构建前端={options["build_frontend"]} 剔除次级={options["strip_secondary"]}')
    print('=' * 60)

    if options['build_frontend']:
        build_frontend()
    else:
        print('\n=== Step 1: 前端构建（按选项跳过）===')
        if not (BASE_PATH / 'web' / 'dist' / 'index.html').exists():
            print('[错误] 选择了跳过前端构建，但 web/dist/index.html 不存在')
            sys.exit(1)

    prepare_runtime(options['rebuild_runtime'], options['strip_secondary'])
    prepare_redis()
    stage_tree(options)
    installer = compile_installer()

    print()
    print('=' * 60)
    print('桌面单机版打包完成！')
    print(f'  绿色目录: {APP_ROOT}  （双击 start.bat 即可运行）')
    if installer:
        print(f'  安装包:   {OUTPUT_DIR / installer}')
    else:
        print('  安装包:   未生成（缺少 Inno Setup，可先用绿色目录）')
    print('  说明:     数据库 / 大模型沿用根 .env 的远程配置；桌面版 Redis 已随包内置于 redis/')
    print('=' * 60)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\n[取消] 用户中断')
        sys.exit(1)
    except Exception as e:
        print(f'\n[失败] {e}')
        import traceback
        traceback.print_exc()
        sys.exit(1)
