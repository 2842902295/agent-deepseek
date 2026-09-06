#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Windows 桌面单机版打包脚本

像 pack_deploy.py 一样一键执行，产出一个可双击安装的 setup.exe（Inno Setup），
最终用户安装后即可在自己电脑上运行；数据库、大模型保持远程不变。

用法（在 Windows 开发机上）：
    python pack_desktop.py

运行时选择打包版本 standard / generic（同 pack_deploy.py，可用环境变量 PACK_VARIANT 免交互覆盖），
应用名取自 web/src/constants/brand.ts 对应版本的 qaSidebarTitle，
它决定绿色目录名、安装包文件名、安装向导/快捷方式/卸载项显示名。

产物（均在 desktop_dist/ 下）：
    <应用名>/                        绿色免安装目录（过渡期双击 launcher.vbs 无窗口运行；start.bat 为带控制台调试入口）
    <应用名>安装包-<时间戳>.exe       Electron 安装包（NSIS；需本机 Node ≥ 20 + pnpm，缺则跳过、绿色目录仍可用）

整体思路（与 Docker「venv 烘焙 + 源码挂载」同范式，不用 PyInstaller）：
  1. pnpm build 前端 → web/dist
  2. 嵌入式 Python(python-3.12-embed) + 按 pdm.lock 导出并剔除重型件后 pip 安装依赖 → runtime/
  3. 便携 Redis(redis-server.exe) → redis/（登录一次性 RSA 私钥依赖它，不可省）
  4. 暂存应用树 + 生成桌面 .env（远程库/模型沿用根 .env，仅覆盖本地项；
     含 DESKTOP_CLIENT_MODE=true 纯客户端开关，启动跳过共享库 DDL/seed）+ build-info.json（构建戳单一事实源之一）
  4.5 暂存 runtime 实测 import app（denylist 剔多/剔错的最终裁决）
  5. electron-builder 编译 Electron 安装包（NSIS 中文向导，暂存树整体作为 extraResources 载荷）

构建戳单一事实源：main() 开头生成的 stamp 三处共用 —— 桌面 .env 的 DESKTOP_BUILD_VERSION、
暂存树 build-info.json、安装包文件名后缀。Electron 壳的构建戳读取与版本提示（阶段 2）据此对比。

阶段 1 过渡期：绿色目录仍带 Edge 壳（launcher.vbs 可用）兜底；安装包已切 Electron。
Inno 与 NSIS 两套安装器互不升级，老用户需先手动卸旧版（发行说明写明）。

被剔除的重型件：torch / sentence-transformers / triton / nvidia-*（多 GB 主因，
核心版 app/ 从不 import）；可选次级 crawl4ai / playwright / patchright（浏览器自动化 skill 随之不可用）。
"""
import fnmatch
import json
import os
import pathlib
import random
import re
import shutil
import socket
import subprocess
import sys
import urllib.request
import zipfile
from datetime import datetime

BASE_PATH = pathlib.Path(__file__).resolve().parent
DESKTOP_DIR = BASE_PATH / 'deploy' / 'desktop'
ELECTRON_DIR = BASE_PATH / 'deploy' / 'desktop-electron'   # Electron 壳工程（阶段 1 与 Edge 壳并存，阶段 2 取代 deploy/desktop）
OUTPUT_DIR = BASE_PATH / 'desktop_dist'
CACHE_DIR = OUTPUT_DIR / '_cache'           # 下载缓存（python zip / get-pip / redis zip）
ELECTRON_OUTPUT_DIR = OUTPUT_DIR / 'electron_out'   # electron-builder 中间产物（成功后重命名进 desktop_dist 根）
APP_DIR_NAME = 'agent-deepseek桌面版'       # 默认应用名；main() 中按用户输入重新赋值
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
    import termios
    import tty
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


def _opt_from_env(key: str) -> str | None:
    """非交互覆盖项：环境变量设置后跳过对应交互提问（重跑 / 自动化场景用）。"""
    v = os.environ.get(key)
    return v.strip().lower() if v and v.strip() else None


def _validate_app_name(name: str):
    if not name:
        print('[错误] 应用名不能为空')
        sys.exit(1)
    if re.search(r'[\\/:*?"<>|]', name):
        print('[错误] 应用名不能包含 \\ / : * ? " < > |（要用作目录与文件名）')
        sys.exit(1)
    if len(name) > 40:
        print('[错误] 应用名过长（请 ≤ 40 个字符）')
        sys.exit(1)
    try:
        name.encode('gbk')
    except UnicodeEncodeError:
        print('[错误] 应用名需可 GBK 编码（start.bat 为 GBK 脚本；请勿使用 emoji / 生僻符号）')
        sys.exit(1)


def _brand_block(variant: str) -> str:
    """返回 brand.ts 中 BRAND_<VARIANT> 对象块的文本。"""
    brand_ts = BASE_PATH / 'web' / 'src' / 'constants' / 'brand.ts'
    text = brand_ts.read_text(encoding='utf-8')
    block = re.search(r'BRAND_' + variant.upper() + r'\s*:\s*BrandText\s*=\s*\{(.*?)\n\};', text, re.S)
    if not block:
        print(f'[错误] brand.ts 中未找到 BRAND_{variant.upper()} 块')
        sys.exit(1)
    return block.group(1)


def _brand_str_field(block: str, field: str) -> str | None:
    """解析块内字符串字段（未找到返回 None）。"""
    m = re.search(field + r'\s*:\s*["\']([^"\']+)["\']', block)
    return m.group(1).strip() if m else None


def _app_name_from_brand(variant: str) -> str:
    """从 web/src/constants/brand.ts 的 BRAND_<VARIANT> 块解析 qaSidebarTitle 作为应用名。"""
    title = _brand_str_field(_brand_block(variant), 'qaSidebarTitle')
    if not title:
        print(f'[错误] brand.ts 的 BRAND_{variant.upper()} 块中未找到 qaSidebarTitle')
        sys.exit(1)
    return title


def _random_port() -> str:
    """随机挑一个 20000-60000 的高位空闲端口（本机实测可绑定）。

    桌面版默认端口不再沿用 9999：
    - 打包机/用户机上开发后端也用 9999，同机并存会抢端口；更隐蔽的是 launcher
      的 attach 逻辑——首选端口上只要有任何 HTTP 响应就当作「本应用已在运行」
      直接接管，桌面窗口会挂到别的后端上（2026-08-13 实测踩坑）。
    - 这只是首选值：launcher 启动时仍有「被占自动换空闲端口并回写 .env」兜底。
    """
    for _ in range(20):
        p = random.randint(20000, 60000)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('127.0.0.1', p))
                return str(p)
            except OSError:
                continue
    return str(random.randint(20000, 60000))


def collect_options() -> dict:
    print('=' * 60)
    print('桌面单机版打包 - 配置选择')
    print('=' * 60)
    print('（支持环境变量免交互覆盖：PACK_VARIANT / PACK_PORT / PACK_REBUILD_RUNTIME / PACK_BUILD_FRONTEND / PACK_STRIP_SECONDARY / PACK_REBUILD_NODE_MODULES）')

    variant = _opt_from_env('PACK_VARIANT') or prompt_choice('打包版本（应用名取自 brand.ts 对应版本的 qaSidebarTitle）：', [
        ('standard', 'standard（默认，品牌 = 同道·标准 AI 助理）'),
        ('generic', 'generic（品牌 = agent-deepseek）'),
    ], 0)
    if variant not in ('standard', 'generic'):
        print(f'[错误] PACK_VARIANT 只支持 standard / generic，收到: {variant}')
        sys.exit(1)
    app_name = _app_name_from_brand(variant)
    # loading 页标语 / 英文标识同取自 brand.ts（文案单点维护）
    brand_block = _brand_block(variant)
    tagline = _brand_str_field(brand_block, 'desktopTagline') or ''
    mark = _brand_str_field(brand_block, 'workbenchSub') or ''
    print(f'应用名: {app_name}（来自 brand.ts qaSidebarTitle）；标语: {tagline or "（无）"}')
    _validate_app_name(app_name)

    # 默认每次打包随机一个高位端口，避开开发后端 9999 / 前端 dev 9527 等常用口
    rand_port = _random_port()
    port = _opt_from_env('PACK_PORT') or prompt_choice('首选服务端口（启动时被其他程序占用会自动换空闲端口）：', [
        (rand_port, f'{rand_port}（默认：随机高位端口，避开开发环境常用口）'),
        ('9999', '9999（项目旧约定；开发后端在跑时会抢端口/被 launcher 误接管）'),
    ], 0)

    rebuild_runtime = _opt_from_env('PACK_REBUILD_RUNTIME') or prompt_choice('是否重新构建 Python 运行环境 runtime/（首次必须构建；后续可复用缓存加速）：', [
        ('no', '复用已有 runtime/（快，日常打包）'),
        ('yes', '重新构建（首次 / 依赖变更后）'),
    ], 0)

    build_frontend = _opt_from_env('PACK_BUILD_FRONTEND') or prompt_choice('是否重新构建前端 web/dist：', [
        ('yes', '构建（默认，保证最新）'),
        ('no', '跳过（web/dist 已是最新时省时间）'),
    ], 0)

    strip_secondary = _opt_from_env('PACK_STRIP_SECONDARY') or prompt_choice('是否剔除次级浏览器依赖（crawl4ai/playwright/patchright，核心版用不到，省约百 MB）：', [
        ('yes', '剔除（默认，核心版）'),
        ('no', '保留（需要浏览器自动化时）'),
    ], 0)

    rebuild_node_modules = _opt_from_env('PACK_REBUILD_NODE_MODULES') or prompt_choice('是否重装 Electron 壳依赖 node_modules/（通常可复用，仅依赖变化时需要）：', [
        ('no', '复用已有 node_modules/（快，日常打包）'),
        ('yes', '重装（首跑 / electron 或 electron-builder 版本变更后）'),
    ], 0)

    return {
        'variant': variant,
        'app_name': app_name,
        'tagline': tagline,
        'mark': mark,
        'port': port,
        'rebuild_runtime': rebuild_runtime == 'yes',
        'build_frontend': build_frontend == 'yes',
        # 注意必须是布尔值：传 'no' 字符串在 prepare_runtime 里是 truthy，会导致"保留"选项失效
        'strip_secondary': strip_secondary == 'yes',
        'rebuild_node_modules': rebuild_node_modules == 'yes',
    }


# ───────────────────────────── 基础工具 ─────────────────────────────
def run(cmd: list[str], desc: str, shell: bool | None = None, cwd: pathlib.Path | None = None, timeout: int | None = None, env: dict | None = None):
    print(f'\n[执行] {desc}\n  $ {" ".join(str(c) for c in cmd)}')
    if shell is None:
        shell = os.name == 'nt'
    try:
        result = subprocess.run([str(c) for c in cmd], shell=shell, cwd=str(cwd) if cwd else None, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        print(f'[错误] 命令超时（{timeout}s）')
        sys.exit(1)
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


# runtime 瘦身：安装速度瓶颈在解压落盘的文件数，这里删的全是运行期用不到的内容。
# - __pycache__ / *.pyc：首次导入会按需重新生成（仅首跑略慢）
# - *.pyi：类型存根，纯开发期给 IDE / pyright 用，运行期从不加载
# - tests：各包自带的测试目录，运行期从不 import（pandas.tests / numpy.tests / scipy.*.tests…）
# - litellm/proxy/_experimental/out：litellm 代理服务的 Next.js 静态站，本项目只把 litellm 当客户端库
# - pip / wheel：只在打包期装依赖用，运行期不需要（桌面版不从 runtime 里 pip install）。
#   注意 setuptools 必须保留：passlib（登录密码哈希核心）运行期 import pkg_resources。
_PRUNE_DIR_NAMES = ('__pycache__', 'tests')
_PRUNE_REL_DIRS = (r'Lib\site-packages\litellm\proxy\_experimental\out',)
_PRUNE_PACKAGES = ('pip', 'wheel')


def prune_runtime():
    """删 runtime 内运行期用不到的目录/文件，显著减少安装包解压的文件数。幂等。"""
    runtime_dir = APP_ROOT / 'runtime'
    site_packages = runtime_dir / 'Lib' / 'site-packages'
    if not site_packages.exists():
        return
    removed_dirs = removed_files = 0
    for rel in _PRUNE_REL_DIRS:
        target = runtime_dir / rel
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
            removed_dirs += 1
    # 打包期专用工具包：包目录 + dist-info（带版本后缀，用 glob）
    for pkg in _PRUNE_PACKAGES:
        for target in [site_packages / pkg, *site_packages.glob(f'{pkg}-*.dist-info')]:
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
                removed_dirs += 1
    for dirpath, dirnames, filenames in os.walk(site_packages, topdown=False):
        for name in dirnames:
            if name in _PRUNE_DIR_NAMES:
                shutil.rmtree(os.path.join(dirpath, name), ignore_errors=True)
                removed_dirs += 1
        for name in filenames:
            if name.endswith(('.pyc', '.pyo', '.pyi')):
                try:
                    os.remove(os.path.join(dirpath, name))
                    removed_files += 1
                except OSError:
                    pass
    # walk 用 topdown=False，子目录先删，os.walk 不会因 dirnames 原地删除而漏掉计数误差
    if removed_dirs or removed_files:
        print(f'  [瘦身] runtime 删除 {removed_dirs} 个冗余目录 / {removed_files} 个 .pyc|.pyi（加速安装解压）')


def strip_denied_packages(strip_secondary: bool):
    """复用 runtime 路径的补剔：把 denylist 包从暂存 runtime 里就地删掉。幂等。

    剔除名单只在重建路径（filter_requirements → pip install）生效；复用旧 runtime 时
    若名单已更新（或旧缓存当初没剔次级），必须在这里补删，否则重型包会溜进安装包
    （2026-08-11 实测：patchright 105MB 因此进了包）。
    匹配方式：*.dist-info 的元数据名命中名单 → 删 dist-info + top_level.txt 列出的
    顶层目录/单文件模块；再按规范化目录名兜底扫一遍（防 dist-info 缺失）。
    """
    site_packages = APP_ROOT / 'runtime' / 'Lib' / 'site-packages'
    if not site_packages.exists():
        return
    primary, secondary = load_denylist()
    deny = primary + (secondary if strip_secondary else [])
    if not deny:
        return

    removed: list[str] = []

    def _rmtree(path: pathlib.Path):
        """Windows 下只读属性会让 rmtree 静默失败，先 chmod 再重试。"""
        def _on_error(func, p, exc_info):
            try:
                os.chmod(p, 0o777)
                func(p)
            except OSError:
                pass
        shutil.rmtree(path, onerror=_on_error)

    def _remove_top(top: str):
        for cand in (site_packages / top, site_packages / f'{top}.py'):
            if cand.is_dir():
                _rmtree(cand)
                removed.append(top)
            elif cand.is_file():
                try:
                    cand.unlink()
                    removed.append(f'{top}.py')
                except OSError:
                    pass

    # 1) dist-info 驱动（最可靠：top_level.txt 给出真实顶层模块名）
    for dist in list(site_packages.glob('*.dist-info')):
        # 目录名形如 <name>-<version>.dist-info；先剥后缀再切版本，
        # 否则版本号里的点会被 rsplit('-') 错切成 name 的一部分
        meta_name = dist.name[: -len('.dist-info')].rsplit('-', 1)[0]
        if not _denied(meta_name.replace('_', '-'), deny):
            continue
        tops: list[str] = []
        tl = dist / 'top_level.txt'
        if tl.exists():
            tops = [ln.strip() for ln in tl.read_text(encoding='utf-8', errors='ignore').splitlines() if ln.strip()]
        _rmtree(dist)
        for top in tops or [meta_name]:
            _remove_top(top)

    # 2) 目录名兜底（dist-info 缺失/异常命名时）
    for entry in list(site_packages.iterdir()):
        if entry.is_dir() and _denied(entry.name.replace('_', '-'), deny) and not entry.name.endswith('.dist-info'):
            _rmtree(entry)
            removed.append(entry.name)

    if removed:
        print(f'  [剔除] 复用路径补剔 denylist 包：{", ".join(sorted(set(removed)))}')


def prepare_runtime(rebuild: bool, strip_secondary: bool):
    runtime_dir = APP_ROOT / 'runtime'
    py = _runtime_python()
    marker = runtime_dir / '.deps_installed'

    if rebuild and runtime_dir.exists():
        print('  [重建] 删除旧 runtime/')
        shutil.rmtree(runtime_dir, ignore_errors=True)

    if py.exists() and marker.exists() and not rebuild:
        print('\n=== Step 2: Python runtime（复用缓存，跳过重建）===')
        strip_denied_packages(strip_secondary)
        prune_runtime()
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
    prune_runtime()
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


def generate_desktop_env(port: str, stamp: str) -> str:
    """以根 .env 为底，应用 env.desktop 覆盖项（含端口 / 构建戳占位替换），生成安装目录 .env 内容。"""
    overrides_text = (DESKTOP_DIR / 'env.desktop').read_text(encoding='utf-8') \
        .replace('__APP_PORT__', port).replace('__BUILD_VERSION__', stamp)
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
    env_text = generate_desktop_env(options['port'], options['stamp'])
    (APP_ROOT / '.env').write_text(env_text, encoding='utf-8')
    print('  [+] .env（远程库/模型沿用根 .env，本地项已覆盖）')

    # 构建戳单一事实源之一：与 .env 的 DESKTOP_BUILD_VERSION、安装包文件名后缀同源；
    # Electron 壳 / app-config 接口读取后用于「发现新版本」对比（阶段 2）
    build_info = {
        'buildVersion': options['stamp'],
        'appName': options['app_name'],
        'variant': options['variant'],
        'packedAt': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }
    (APP_ROOT / 'build-info.json').write_text(json.dumps(build_info, ensure_ascii=False, indent=2), encoding='utf-8')
    print('  [+] build-info.json')

    # 启动 / 停止脚本（替换端口与应用名占位）。
    # bat 为 GBK + CRLF 编码（中文消息；约束原因见 start.bat 头部注释），按原编码读写，
    # read_text 会把 CRLF 归一成 LF，写出时补回。
    for name in ('start.bat', 'stop.bat'):
        text = (DESKTOP_DIR / name).read_text(encoding='gbk')
        text = text.replace('__APP_PORT__', options['port']).replace('__APP_NAME__', options['app_name'])
        (APP_ROOT / name).write_bytes(text.replace('\r\n', '\n').replace('\n', '\r\n').encode('gbk'))
        print(f'  [+] {name}')

    # 无窗口启动 / 停止脚本（快捷方式与双击入口走 pythonw.exe，无控制台黑框）。
    # pyw 含 __APP_NAME__ 占位（UTF-8 读写即可）；launcher.vbs 为绿色目录双击入口（纯 ASCII）。
    # stop.pyw/stop.bat 仅作调试/应急保留，不再建快捷方式（单图标生命周期：关窗即停）。
    for name in ('launcher.pyw', 'stop.pyw'):
        text = (DESKTOP_DIR / name).read_text(encoding='utf-8').replace('__APP_NAME__', options['app_name'])
        (APP_ROOT / name).write_text(text, encoding='utf-8')
        print(f'  [+] {name}')
    shutil.copy2(DESKTOP_DIR / 'launcher.vbs', APP_ROOT / 'launcher.vbs')
    print('  [+] launcher.vbs')

    # 秒开 loading 页（本地占位页，就绪自动跳转）+ 应用图标（快捷方式与任务栏用）
    # 占位：__APP_NAME__（纯文本，title 用）/ __APP_NAME_HTML__（间隔号「·」包品牌渐变）/
    # __APP_TAGLINE__（标语）/ __APP_MARK__（右下角英文标识）
    text = (DESKTOP_DIR / 'loading.html').read_text(encoding='utf-8')
    text = (
        text.replace('__APP_NAME_HTML__', options['app_name'].replace('·', '<b>·</b>'))
        .replace('__APP_TAGLINE__', options['tagline'])
        .replace('__APP_MARK__', options['mark'])
        .replace('__APP_NAME__', options['app_name'])
    )
    (APP_ROOT / 'loading.html').write_text(text, encoding='utf-8')
    print('  [+] loading.html')
    visual = DESKTOP_DIR / 'aurora-visual.jpg'
    if visual.exists():
        shutil.copy2(visual, APP_ROOT / 'aurora-visual.jpg')
        print('  [+] aurora-visual.jpg')
    else:
        print('  [警告] aurora-visual.jpg 不存在，loading 页主视觉将缺失')
    favicon = BASE_PATH / 'web' / 'dist' / 'favicon.ico'
    if favicon.exists():
        shutil.copy2(favicon, APP_ROOT / 'favicon.ico')
        print('  [+] favicon.ico')
    else:
        print('  [警告] web/dist/favicon.ico 不存在，快捷方式图标将缺失')


# ───────────────────────────── 步骤 4.5：打包期验证 ─────────────────────────────
def verify_staged_runtime():
    """用暂存 runtime 实测 `import app`，一网打尽 denylist 剔多/剔错。

    denylist 按「app 零引用 + 孤儿链」静态判断剔包，但静态判断有盲区
    （第三方包的运行期 import、可选依赖回退路径），只有真实 import 才是最终裁决。
    PYTHONDONTWRITEBYTECODE=1 防止验证过程生成 __pycache__ 混进安装包
    （瘦身阶段刚删干净，不能让验证再写回来）。
    """
    print('\n=== Step 4.5: 打包期验证（暂存 runtime 实测 import app）===')
    py = _runtime_python()
    if not py.exists():
        print('  [跳过] runtime/python.exe 不存在')
        return
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    run([str(py), '-c', 'import app; print("[验证] import app OK")'],
        '暂存 runtime 实测 import app（首次较慢，属预期）',
        shell=False, cwd=APP_ROOT, timeout=600, env=env)
    # 双保险：即便有漏网的字节码缓存（如手工跑过暂存 python），编译前清掉
    for dirpath, dirnames, _ in os.walk(APP_ROOT):
        for name in dirnames:
            if name == '__pycache__':
                shutil.rmtree(os.path.join(dirpath, name), ignore_errors=True)


# ───────────────────────────── 步骤 5：编译 Electron 安装包 ─────────────────────────────
def _ico_max_size(path: pathlib.Path) -> int:
    """读 ICO 文件头取最大帧边长（宽/高字节为 0 表示 256）。纯 stdlib，不引 Pillow。"""
    try:
        with open(path, 'rb') as f:
            head = f.read(6)
            if len(head) < 6 or head[2:4] != b'\x01\x00':
                return 0
            count = int.from_bytes(head[4:6], 'little')
            best = 0
            for _ in range(count):
                entry = f.read(16)
                if len(entry) < 16:
                    break
                best = max(best, min(entry[0] or 256, entry[1] or 256))
            return best
    except OSError:
        return 0


def _render_electron_builder_yml(options: dict) -> pathlib.Path:
    """把 electron-builder.yml 模板里的 ${env.*} 占位替换成字面值，渲染出 generated.yml。

    为什么不用 electron-builder 的运行时 ${env.*} 宏（2026-09 Windows 实测 + 26.15.3 源码核实）：
    ① productName 从不走宏展开器（appInfo.js 直取 config），字面量漏进 NSIS 脚本，
       makensis 报 warning 6000（未知变量）且 warning 被当 error，编译直接失败；
    ② extraResources.from 在宏展开**之前**先被 path.resolve 相对工程目录拼接
       （fileMatcher.js getFileMatchers），绝对路径会被拼成 <工程目录>\\D:\\... 的畸形路径，
       「file source doesn't exist」只告警不报错 → 后端载荷静默丢失、装出来是个空壳。
    渲染时 Windows 反斜杠统一转正斜杠（YAML plain scalar 里反斜杠无歧义是事实，但正斜杠
    同时免掉双引号标量转义坑；path.resolve 对正斜杠绝对路径照样识别）。
    """
    tpl = (ELECTRON_DIR / 'electron-builder.yml').read_text(encoding='utf-8')
    subs = {
        '${env.DESKTOP_APP_NAME}': options['app_name'],
        '${env.DESKTOP_STAGE_DIR}': str(APP_ROOT).replace('\\', '/'),
        '${env.DESKTOP_OUTPUT_DIR}': str(ELECTRON_OUTPUT_DIR).replace('\\', '/'),
        '${env.DESKTOP_BUILD_VERSION}': options['stamp'],
    }
    rendered = tpl
    for placeholder, value in subs.items():
        rendered = rendered.replace(placeholder, value)
    # 逐行扫描非注释行，确认没有漏网的占位（${ext} 是 electron-builder 自身宏，保留）
    leaked = [ln for ln in rendered.splitlines() if '${env.' in ln and not ln.lstrip().startswith('#')]
    if leaked:
        print(f'[错误] electron-builder.yml 存在未替换的占位：{leaked}')
        sys.exit(1)
    gen = ELECTRON_DIR / 'electron-builder.generated.yml'
    gen.write_text(rendered, encoding='utf-8')
    return gen


def build_electron_installer(options: dict) -> str | None:
    """electron-builder 编译 NSIS 中文向导安装包。缺 Node/pnpm 时跳过（绿色目录仍可用）。

    配置模板 = deploy/desktop-electron/electron-builder.yml，参数（应用名 / 暂存树路径 /
    输出目录 / 构建戳）由 _render_electron_builder_yml 渲染成字面值经 --config 传入
    （不依赖 ${env.*} 运行时宏，原因见该函数）；壳工程本体零运行时依赖（files 只收 tsc 产物），
    暂存应用树整体作 extraResources 载荷 → 安装后 resources/backend/（paths.ts 单一出口）。
    """
    print('\n=== Step 5: 编译 Electron 安装包（electron-builder / NSIS 中文向导）===')
    node = shutil.which('node')
    pnpm = shutil.which('pnpm')
    if not node or not pnpm:
        print('  [跳过] 未找到 Node / pnpm。请安装 Node ≥ 20 + pnpm 后重跑本步骤，')
        print(f'         或直接使用绿色目录：{APP_ROOT}')
        return None
    node_ver = subprocess.run([node, '-v'], capture_output=True, text=True, timeout=30).stdout.strip().lstrip('v')
    try:
        node_major = int(node_ver.split('.')[0])
    except ValueError:
        node_major = 0
    if node_major < 20:
        print(f'  [跳过] Node 版本过低（{node_ver}），electron-builder 需 Node ≥ 20')
        print(f'         请升级 Node 后重跑，或直接使用绿色目录：{APP_ROOT}')
        return None

    # 图标：基线 build/icon.ico 已入库（7 帧含 256，electron-builder 要求 ≥256）；
    # 前端产物 favicon 最大帧 ≥256 才覆盖同步，否则保留基线（旧 favicon 仅 64 帧，不够格）
    icon_dst = ELECTRON_DIR / 'build' / 'icon.ico'
    favicon = APP_ROOT / 'favicon.ico'
    if favicon.exists() and _ico_max_size(favicon) >= 256:
        shutil.copy2(favicon, icon_dst)
        print('  [图标] favicon.ico 最大帧 ≥256，覆盖同步 build/icon.ico')
    else:
        print('  [图标] favicon.ico 不足 256px（或缺失），保留基线 build/icon.ico')

    if options['rebuild_node_modules'] and (ELECTRON_DIR / 'node_modules').exists():
        print('  [重建] 删除旧 node_modules/')
        shutil.rmtree(ELECTRON_DIR / 'node_modules', ignore_errors=True)

    gen = _render_electron_builder_yml(options)
    print(f'  [渲染] {gen.name}（应用名 / 暂存树 / 输出目录 / 构建戳已注入为字面值）')

    # 内部发行不签名：关掉证书自动发现，避免打包机证书库差异导致签名步骤失败/卡交互
    env = dict(os.environ, CSC_IDENTITY_AUTO_DISCOVERY='false')
    # 下载镜像显式注入（2026-09 实测踩坑）：.npmrc 的 electron_mirror 只在 `pnpm run`
    # 生命周期脚本里被 pnpm 注入成 npm_config_* 环境变量；`pnpm exec electron-builder`
    # 不注入 → @electron/get 回落默认 github.com → 打包机到 GitHub 超时（20.205.243.166）。
    # 镜像变量名源码核实：@electron/get 认 npm_config_electron_mirror / ELECTRON_MIRROR，
    # electron-builder 二进制（nsis/7zip/…）认 ELECTRON_BUILDER_BINARIES_MIRROR。
    # setdefault：用户已自行配置（自有镜像/代理）时不覆盖。
    _EM = 'https://npmmirror.com/mirrors/electron/'
    _BM = 'https://npmmirror.com/mirrors/electron-builder-binaries/'
    for k, v in (('ELECTRON_MIRROR', _EM), ('NPM_CONFIG_ELECTRON_MIRROR', _EM),
                 ('ELECTRON_BUILDER_BINARIES_MIRROR', _BM), ('NPM_CONFIG_ELECTRON_BUILDER_BINARIES_MIRROR', _BM)):
        env.setdefault(k, v)
    run(['pnpm', 'install'], '安装 Electron 壳依赖', cwd=ELECTRON_DIR, env=env)
    run(['pnpm', 'run', 'build'], '编译主进程（tsc）', cwd=ELECTRON_DIR, env=env)
    run(['pnpm', 'exec', 'electron-builder', '--win', 'nsis', '--config', gen.name],
        'electron-builder 编译 NSIS 安装包', cwd=ELECTRON_DIR, env=env)

    # 按修改时间取最新：输出目录可能残留上一次打包的 exe（__uninstaller.exe 等中间件按名排除）
    exes = sorted((p for p in ELECTRON_OUTPUT_DIR.glob('*.exe') if 'uninstall' not in p.name.lower()),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    if not exes:
        print(f'  [警告] {ELECTRON_OUTPUT_DIR} 下未找到安装包产物')
        return None
    produced = exes[0]
    final = OUTPUT_DIR / f'{options["app_name"]}安装包-{options["stamp"]}.exe'
    if final.exists():
        final.unlink()
    shutil.move(str(produced), final)
    size_mb = final.stat().st_size / (1024 * 1024)
    print(f'  安装包: {final.name}  ({size_mb:.1f} MB)')
    return final.name


# ─────────────── 旧 Inno Setup 链路（阶段 1 过渡保留、不再调用；阶段 2 连同 installer.iss 一起删） ───────────────
def _find_iscc() -> str | None:
    for cand in INNO_CANDIDATES:
        if os.path.exists(cand):
            return cand
    found = shutil.which('ISCC') or shutil.which('iscc')
    return found


def _render_installer_iss(app_name: str) -> pathlib.Path:
    """按实际应用名把模板 installer.iss 渲染成 installer.generated.iss（UTF-8 BOM）。

    模板里的 #define / DefaultDirName / OutputBaseFilename 用默认名，可独立编译；
    渲染做整行替换，避免 ISCC /D 与脚本内 #define 的优先级歧义。
    """
    tpl = (DESKTOP_DIR / 'installer.iss').read_text(encoding='utf-8-sig')
    subs = (
        ('#define MyAppName', f'#define MyAppName "{app_name}"'),
        ('#define SourceDir', f'#define SourceDir "..\\..\\desktop_dist\\{app_name}"'),
        ('DefaultDirName=', f'DefaultDirName={{localappdata}}\\Programs\\{app_name}'),
        ('OutputBaseFilename=', f'OutputBaseFilename={app_name}安装包'),
    )
    out = []
    for line in tpl.splitlines():
        for prefix, replacement in subs:
            if line.startswith(prefix):
                line = replacement
                break
        out.append(line)
    gen = DESKTOP_DIR / 'installer.generated.iss'
    gen.write_bytes(('\n'.join(out) + '\n').encode('utf-8-sig'))
    return gen


def compile_installer(app_name: str) -> str | None:
    print('\n=== Step 5: 编译 Inno Setup 安装包 ===')
    iscc = _find_iscc()
    if not iscc:
        print('  [跳过] 未找到 Inno Setup（ISCC.exe）。请安装 Inno Setup 6 后重跑本步骤，')
        print(f'         或直接使用绿色目录：{APP_ROOT}')
        return None

    iss = _render_installer_iss(app_name)
    print(f'  [渲染] {iss.name}（应用名: {app_name}）')
    run([iscc, str(iss)], 'ISCC 编译安装包', shell=False)
    produced = OUTPUT_DIR / f'{app_name}安装包.exe'
    if not produced.exists():
        print(f'  [警告] 未找到编译产物 {produced.name}')
        return None
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    final = OUTPUT_DIR / f'{app_name}安装包-{ts}.exe'
    produced.replace(final)
    size_mb = final.stat().st_size / (1024 * 1024)
    print(f'  安装包: {final.name}  ({size_mb:.1f} MB)')
    return final.name


# ───────────────────────────── 主流程 ─────────────────────────────
def main():
    global APP_DIR_NAME, APP_ROOT
    OUTPUT_DIR.mkdir(exist_ok=True)
    CACHE_DIR.mkdir(exist_ok=True)

    options = collect_options()
    APP_DIR_NAME = options['app_name']
    APP_ROOT = OUTPUT_DIR / APP_DIR_NAME
    # 构建戳单一事实源：桌面 .env 的 DESKTOP_BUILD_VERSION、build-info.json、安装包文件名后缀三处共用
    options['stamp'] = datetime.now().strftime('%Y%m%d_%H%M%S')

    print()
    print('=' * 60)
    print('开始打包桌面单机版…')
    print(f'输出目录: {OUTPUT_DIR}')
    print(f'配置: 版本={options["variant"]} 应用名={options["app_name"]} 端口={options["port"]} '
          f'重建runtime={options["rebuild_runtime"]} 构建前端={options["build_frontend"]} '
          f'剔除次级={options["strip_secondary"]} 重装壳依赖={options["rebuild_node_modules"]} 构建戳={options["stamp"]}')
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
    verify_staged_runtime()
    installer = build_electron_installer(options)

    print()
    print('=' * 60)
    print('桌面单机版打包完成！')
    print(f'  绿色目录: {APP_ROOT}  （过渡期双击 launcher.vbs 兜底；start.bat 为带控制台调试入口）')
    if installer:
        print(f'  安装包:   {OUTPUT_DIR / installer}  （Electron / NSIS 中文向导）')
    else:
        print('  安装包:   未生成（缺 Node ≥ 20 / pnpm，可先用绿色目录）')
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
