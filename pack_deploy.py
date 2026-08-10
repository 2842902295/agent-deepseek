#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
项目打包脚本 - 用于生成部署包
排除开发环境、测试文件、临时文件等

支持在打包时通过交互式选择覆盖部分配置（不修改本地工作区，仅作用于 zip 内文件）：
  - docker-compose.yml 中 nginx 容器对外端口（默认 1880，可改为 80 / 443）
  - web/.env 中 VITE_BRAND（默认 standard，可改为 generic）
"""
import hashlib
import os
import pathlib
import fnmatch
import re
import subprocess
import sys
import zipfile
from datetime import datetime

# 打包后保留最近几个历史部署包，其余删除（设为 0 则只留当前最新）
KEEP_PACKAGES = 3

# 需要排除的目录（与 .gitignore 对齐）
EXCLUDE_DIRS = {
    # VCS / IDE / OS
    '.git',
    '.idea',
    '.vscode',
    '.codex',
    '.claude',
    # Python 构建/缓存
    '__pycache__',
    '.venv',
    'venv',
    '.ruff_cache',
    '.pytest_cache',
    '.mypy_cache',
    # 项目自定义
    'tests',
    'examples',
    'docs',
    'temp_doc',
    'temp_script',
    'data',
    'offline_deploy',
    'redis_data',
    '.tmp_images',
    '.extraction_log',
    '.qa_workspace',
    # 注意：.agent_workspace 不在此处 —— 它需要"只保留 .agent_skills 子目录"的特殊处理
    # 前端（dist 不排除：web/dist 由本地预构建后随包分发，见 build_frontend）
    'node_modules',
    'dist-ssr',
    'coverage',
    '.VSCodeCounter',
    '.pnpm-store',
    'videos',          # cypress
    'screenshots',     # cypress
}

# 需要排除的文件（精确匹配）
EXCLUDE_FILES = {
    'pack_deploy.py',  # 排除打包脚本自身
    '.dockerignore',
    'nul',
    # 数据/迁移痕迹（.gitignore 列出）
    'tortoise_queries.json',
    'tortoise_last.txt',
    # 本地解释器指针/工具缓存（带到服务器会让 PDM 误判去下 Python）
    '.pdm-python',
}

# 需要排除的文件扩展名
EXCLUDE_EXTENSIONS = {
    '.pyc',
    '.pyo',
    '.sqlite3',
    '.sqlite3-shm',
    '.sqlite3-wal',
    '.sqlite3-journal',
    '.log',
}

# 需要排除的文件模式
EXCLUDE_PATTERNS = [
    'README',
    'LICENSE',
]


# 需要排除的文件名通配符（fnmatch 规则）
EXCLUDE_FILE_GLOBS = [
    '*.local',
    '.env.local',
    '.env.*.local',
    '*.suo',
    '*.sw?',
    'deploy_package_*.zip',
    'offline_package_*.zip',
    'update_package_*.zip',
]

# 需要排除的目录名通配符（如 *.egg-info）
EXCLUDE_DIR_GLOBS = [
    '*.egg-info',
]


def _read_key_unix() -> str:
    """Unix/macOS：从 stdin 原始模式读取一个按键，返回标准化名称。

    返回值: 'up' / 'down' / 'enter' / 'ctrl-c' / 'q' / 其他单字符
    """
    import termios, tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = os.read(fd, 1)
        if ch == b'\x03':  # Ctrl-C
            return 'ctrl-c'
        if ch in (b'\r', b'\n'):
            return 'enter'
        if ch == b'\x1b':  # ESC，可能是方向键的开头
            # 读后续两字节（非阻塞短超时）；终端正常情况下立即可读
            seq = os.read(fd, 2)
            if seq == b'[A':
                return 'up'
            if seq == b'[B':
                return 'down'
            return 'esc'
        try:
            return ch.decode('utf-8', errors='ignore').lower()
        except Exception:
            return ''
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _read_key_windows() -> str:
    """Windows：用 msvcrt 读取按键。方向键以 0xE0 / 0x00 前缀 + H/P 表示。"""
    import msvcrt
    ch = msvcrt.getwch()
    if ch == '\x03':
        return 'ctrl-c'
    if ch in ('\r', '\n'):
        return 'enter'
    if ch in ('\x00', '\xe0'):
        ch2 = msvcrt.getwch()
        if ch2 == 'H':
            return 'up'
        if ch2 == 'P':
            return 'down'
        return 'special'
    if ch == '\x1b':
        return 'esc'
    return ch.lower()


def _read_key() -> str:
    """跨平台读单键。"""
    if os.name == 'nt':
        return _read_key_windows()
    return _read_key_unix()


def _supports_arrow_keys() -> bool:
    """判断当前 stdin/stdout 是否支持原始模式下的方向键交互。"""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    if os.name == 'nt':
        try:
            import msvcrt  # noqa: F401
            return True
        except ImportError:
            return False
    try:
        import termios, tty  # noqa: F401
        return True
    except ImportError:
        return False


def prompt_choice(question: str, options: list[tuple[str, str]], default_index: int = 0) -> str:
    """交互式单选。options 为 [(value, label), ...]，返回选中的 value。

    优先使用上下方向键 + 回车选择；终端不支持时回退到序号输入；非交互式回退到默认项。
    """
    print()
    print(question)

    # 非交互式：直接默认
    if not sys.stdin.isatty():
        value, label = options[default_index]
        for i, (_, lbl) in enumerate(options, start=1):
            mark = ' (默认)' if (i - 1) == default_index else ''
            print(f'  {i}) {lbl}{mark}')
        print(f'[非交互模式] 使用默认: {label}')
        return value

    # 不支持方向键：回退到序号输入
    if not _supports_arrow_keys():
        for i, (_, lbl) in enumerate(options, start=1):
            mark = ' (默认)' if (i - 1) == default_index else ''
            print(f'  {i}) {lbl}{mark}')
        while True:
            try:
                raw = input(f'请输入序号 [1-{len(options)}, 回车=默认]: ').strip()
            except EOFError:
                value, label = options[default_index]
                print(f'[EOF] 使用默认: {label}')
                return value
            if raw == '':
                value, label = options[default_index]
                print(f'已选择: {label}')
                return value
            if raw.isdigit():
                idx = int(raw) - 1
                if 0 <= idx < len(options):
                    value, label = options[idx]
                    print(f'已选择: {label}')
                    return value
            print('输入无效，请重新输入。')

    # 方向键交互模式：渲染菜单 -> 监听键盘 -> 重绘
    cursor = default_index
    print('（↑/↓ 选择，Enter 确认，q/Esc 取消使用默认）')

    def render(first: bool):
        if not first:
            # 回到菜单起始行：上移 len(options) 行并清行
            sys.stdout.write(f'\x1b[{len(options)}A')
        for i, (_, lbl) in enumerate(options):
            prefix = '> ' if i == cursor else '  '
            line = f'{prefix}{lbl}'
            # 高亮当前项
            if i == cursor:
                line = f'\x1b[36m{line}\x1b[0m'
            sys.stdout.write('\x1b[2K' + line + '\n')
        sys.stdout.flush()

    render(first=True)

    while True:
        key = _read_key()
        if key == 'up':
            cursor = (cursor - 1) % len(options)
            render(first=False)
        elif key == 'down':
            cursor = (cursor + 1) % len(options)
            render(first=False)
        elif key == 'enter':
            value, label = options[cursor]
            print(f'已选择: {label}')
            return value
        elif key in ('q', 'esc', 'ctrl-c'):
            value, label = options[default_index]
            print(f'已取消，使用默认: {label}')
            return value
        # 其他按键忽略


# 前端源码指纹文件（存 web/ 根下：不进 zip、不进 git）：
# 打包时比对指纹，源码无变化则跳过 pnpm install/build，直接复用现有 web/dist
FRONTEND_FINGERPRINT_NAME = '.build_fingerprint'

# 计算指纹时跳过的目录：构建产物 / 依赖 / 缓存 / 测试产物
FRONTEND_HASH_SKIP_DIRS = {
    'node_modules', '.pnpm-store', 'dist', 'dist-ssr', 'coverage',
    '.vite', '.cache', '.VSCodeCounter', 'cypress', '.git',
}


def compute_frontend_fingerprint(web_dir: pathlib.Path) -> str:
    """计算 web/ 源码指纹（相对路径 + 文件内容的 sha256），产物与依赖目录不参与。"""
    hasher = hashlib.sha256()
    for root, dirs, files in os.walk(web_dir):
        root_path = pathlib.Path(root)
        dirs[:] = sorted(d for d in dirs if d not in FRONTEND_HASH_SKIP_DIRS)
        for name in sorted(files):
            fp = root_path / name
            if fp.name == FRONTEND_FINGERPRINT_NAME:
                continue
            rel = fp.relative_to(web_dir).as_posix()
            hasher.update(rel.encode('utf-8'))
            hasher.update(fp.read_bytes())
    return hasher.hexdigest()


def build_frontend(base_path: pathlib.Path) -> None:
    """打包前置：本地预构建前端，web/dist 随包分发，服务器零 node 编译。

    品牌变体不参与构建产物（前端运行时从后端 /ai/agent/app-config 拉取
    brand_variant，见 web/src/utils/brand-config.ts），直接按默认环境构建即可。

    源码指纹与上次成功构建一致且 web/dist 已存在时，跳过构建直接复用产物；
    删除 web/.build_fingerprint 或 web/dist 可强制重新构建。
    """
    web_dir = base_path / 'web'
    if not (web_dir / 'package.json').exists():
        print('[跳过] 未找到 web/package.json，不构建前端')
        return

    fingerprint_file = web_dir / FRONTEND_FINGERPRINT_NAME
    dist_dir = web_dir / 'dist'

    print('\n[检查] 计算前端源码指纹，判断是否需要构建...')
    fingerprint = compute_frontend_fingerprint(web_dir)
    last_fingerprint = ''
    if fingerprint_file.exists():
        last_fingerprint = fingerprint_file.read_text(encoding='utf-8').strip()

    if dist_dir.exists() and last_fingerprint and last_fingerprint == fingerprint:
        print('[跳过] 前端源码无变化，复用现有 web/dist，跳过构建')
        return

    env = os.environ.copy()
    env['NODE_OPTIONS'] = '--max_old_space_size=4096'

    def run(cmd: list[str], desc: str):
        print(f'\n[执行] {desc}')
        print(f'  $ {" ".join(cmd)}')
        result = subprocess.run(cmd, shell=(sys.platform == 'win32'), cwd=str(web_dir), env=env)
        if result.returncode != 0:
            print(f'[错误] {desc}失败，中止打包')
            sys.exit(1)

    run(['pnpm', 'install'], '安装前端依赖（幂等，已装则秒过）')
    run(['pnpm', 'build'], '构建前端生产产物（web/dist）')

    if not dist_dir.exists():
        print('[错误] pnpm build 完成但 web/dist 不存在，请检查构建配置')
        sys.exit(1)

    # 构建成功后以构建后的磁盘状态重新记录指纹（构建过程会在 src 下生成
    # elegant-router 路由声明等文件），下次打包才能正确判定"无变化"
    fingerprint_file.write_text(compute_frontend_fingerprint(web_dir) + '\n', encoding='utf-8')


def collect_pack_options() -> dict:
    """收集打包时的可选覆盖配置。返回一个 dict 用于驱动后续文件改写。

      - vite_brand：web/.env VITE_BRAND 与 .env BRAND_VARIANT
        standard → nginx 保持 1880:80（内网默认）
        generic  → nginx 改为 80:80 + 443:443（外网 HTTPS）
    """
    print('=' * 60)
    print('打包前配置选择')
    print('=' * 60)

    vite_brand = prompt_choice(
        '选择品牌变体（决定 web/.env 的 VITE_BRAND）：',
        [
            ('standard', 'standard（默认，内网，端口 1880）'),
            ('generic',  'generic（外网，端口 80 + 443 HTTPS）'),
        ],
        default_index=0,
    )

    return {
        'vite_brand': vite_brand,
    }


def transform_docker_compose(content: str, vite_brand: str) -> str:
    """改写 docker-compose.yml 的 nginx ports 块。

    standard：保持原样（不修改）
    generic ：如果没有 443:443，则追加一行
    """
    if vite_brand != 'generic':
        return content

    lines = content.splitlines(keepends=True)
    in_nginx = False
    in_ports = False
    nginx_indent = -1
    ports_indent = -1
    ports_start = -1
    ports_end = -1
    has_443 = False

    port_443_pattern = re.compile(r'^(\s*)-\s*"443:443"\s*$')
    service_pattern = re.compile(r'^(\s*)([A-Za-z0-9_-]+):\s*$')
    ports_key_pattern = re.compile(r'^(\s*)ports:\s*$')

    for i, line in enumerate(lines):
        m = service_pattern.match(line)
        if m:
            indent = len(m.group(1))
            name = m.group(2)
            if not in_nginx:
                if name == 'nginx':
                    in_nginx = True
                    nginx_indent = indent
                    continue
            else:
                if indent <= nginx_indent:
                    in_nginx = False
                    in_ports = False

        if in_nginx:
            pm = ports_key_pattern.match(line)
            if pm:
                in_ports = True
                ports_indent = len(pm.group(1))
                ports_start = i
                continue

            if in_ports:
                # 检查是否已有 443:443
                if port_443_pattern.match(line):
                    has_443 = True

                # ports 块结束条件：缩进回到与 ports: 同级或更外
                stripped = line.strip()
                if stripped == '' or (not stripped.startswith('-') and len(line) - len(line.lstrip()) <= ports_indent):
                    ports_end = i
                    break

    if ports_start == -1:
        print('[警告] docker-compose.yml 未找到 nginx 的 ports 块，未改写')
        return content

    if ports_end == -1:
        ports_end = len(lines)

    # 如果已有 443，不做修改
    if has_443:
        return content

    # 追加 443:443
    entry_indent = ' ' * (ports_indent + 2)
    new_line = f'{entry_indent}- "443:443"\n'
    lines.insert(ports_end, new_line)
    return ''.join(lines)


def transform_web_env(content: str, vite_brand: str) -> str:
    """改写 web/.env 的 VITE_BRAND=xxx 行。"""
    lines = content.splitlines(keepends=True)
    pattern = re.compile(r'^(VITE_BRAND\s*=).*$')
    replaced = False
    for i, line in enumerate(lines):
        # 保留行尾换行
        stripped = line.rstrip('\r\n')
        ending = line[len(stripped):]
        if pattern.match(stripped):
            lines[i] = f'VITE_BRAND={vite_brand}{ending}'
            replaced = True
            break

    if not replaced:
        print('[警告] web/.env 未找到 VITE_BRAND 行，未改写')
    return ''.join(lines)


def transform_root_env(content: str, vite_brand: str) -> str:
    """改写根目录 .env 的 BRAND_VARIANT=xxx 行。"""
    lines = content.splitlines(keepends=True)
    pattern = re.compile(r'^(BRAND_VARIANT\s*=).*$')
    replaced = False
    for i, line in enumerate(lines):
        stripped = line.rstrip('\r\n')
        ending = line[len(stripped):]
        if pattern.match(stripped):
            lines[i] = f'BRAND_VARIANT={vite_brand}{ending}'
            replaced = True
            break

    if not replaced:
        print('[警告] .env 未找到 BRAND_VARIANT 行，未改写')
    return ''.join(lines)


def get_transformed_bytes(file_path: pathlib.Path, base_path: pathlib.Path, options: dict) -> bytes | None:
    """如果该文件需要按选项覆盖，返回新字节内容；否则返回 None 表示原样写入。"""
    rel = file_path.relative_to(base_path).as_posix()

    if rel == 'docker-compose.yml':
        text = file_path.read_text(encoding='utf-8')
        new_text = transform_docker_compose(text, options['vite_brand'])
        if new_text != text:
            return new_text.encode('utf-8')
        return None

    if rel == 'web/.env':
        text = file_path.read_text(encoding='utf-8')
        new_text = transform_web_env(text, options['vite_brand'])
        if new_text != text:
            return new_text.encode('utf-8')
        return None

    if rel == '.env':
        text = file_path.read_text(encoding='utf-8')
        new_text = transform_root_env(text, options['vite_brand'])
        if new_text != text:
            return new_text.encode('utf-8')
        return None

    return None


def should_exclude(file_path: pathlib.Path, base_path: pathlib.Path) -> bool:
    """判断文件是否应该被排除"""
    # 注意：目录已经在 os.walk 中被过滤，这里只需要检查文件

    # 检查文件名（精确）
    if file_path.name in EXCLUDE_FILES:
        return True

    # 检查文件名（通配符）
    for pat in EXCLUDE_FILE_GLOBS:
        if fnmatch.fnmatch(file_path.name, pat):
            return True

    # 检查扩展名
    if file_path.suffix in EXCLUDE_EXTENSIONS:
        return True

    # 检查文件模式
    for pattern in EXCLUDE_PATTERNS:
        if pattern in file_path.name:
            return True

    return False


def create_deploy_package():
    """创建部署包"""
    base_path = pathlib.Path(__file__).parent

    # 收集打包前的可选覆盖配置（仅作用于 zip 内容）
    options = collect_pack_options()

    # 本地预构建前端：zip 直接携带 web/dist，服务器零编译
    build_frontend(base_path)

    # 压缩包内的根目录名称
    root_folder_name = 'cesi-fast-admin'

    # 生成打包文件名（带时间戳）
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    zip_filename = f'deploy_package_{timestamp}.zip'
    zip_path = base_path / zip_filename

    print()
    print('=' * 60)
    print(f'开始打包项目...')
    print(f'输出文件: {zip_filename}')
    print(f'压缩包内根目录: {root_folder_name}')
    print(f'配置覆盖: VITE_BRAND={options["vite_brand"]}, BRAND_VARIANT={options["vite_brand"]}')
    print('=' * 60)

    included_count = 0
    excluded_count = 0
    excluded_dir_count = 0
    transformed_count = 0

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        # 使用 os.walk 可以在遍历时跳过整个目录
        for root, dirs, files in os.walk(base_path):
            root_path = pathlib.Path(root)

            # 特殊处理：web/ 只保留 dist（前端已在本地预构建，服务器无需源码/依赖清单）
            if root_path == base_path / 'web':
                kept = 'dist'
                removed = [d for d in dirs if d != kept]
                for d in removed:
                    print(f'[跳过目录] web/{d}')
                    excluded_dir_count += 1
                dirs[:] = [d for d in dirs if d == kept]
                for fn in files:
                    excluded_count += 1
                    print(f'[跳过] web/{fn}')
                files = []

            # 特殊处理：.agent_workspace 只保留 .agent_skills 子目录
            # 对应 .gitignore 规则：
            #   /.agent_workspace/*
            #   !/.agent_workspace/.agent_skills/
            if root_path == base_path / '.agent_workspace':
                kept = '.agent_skills'
                removed = [d for d in dirs if d != kept]
                for d in removed:
                    print(f'[跳过目录] .agent_workspace/{d}')
                    excluded_dir_count += 1
                dirs[:] = [d for d in dirs if d == kept]
                # 根目录下的散文件也不要
                for fn in files:
                    excluded_count += 1
                    print(f'[跳过] .agent_workspace/{fn}')
                files = []

            # 过滤掉需要排除的目录，避免进入遍历
            dirs_to_remove = []
            for dir_name in dirs:
                hit = dir_name in EXCLUDE_DIRS or any(
                    fnmatch.fnmatch(dir_name, pat) for pat in EXCLUDE_DIR_GLOBS
                )
                if hit:
                    dirs_to_remove.append(dir_name)
                    excluded_dir_count += 1
                    # 显示排除的目录路径
                    if root_path == base_path:
                        print(f'[跳过目录] {dir_name}')
                    else:
                        print(f'[跳过目录] {root_path.relative_to(base_path) / dir_name}')

            # 从 dirs 列表中移除，os.walk 就不会进入这些目录
            for dir_name in dirs_to_remove:
                dirs.remove(dir_name)

            # 处理文件
            for file_name in files:
                file_path = root_path / file_name

                if should_exclude(file_path, base_path):
                    excluded_count += 1
                    print(f'[跳过] {file_path.relative_to(base_path)}')
                else:
                    relative_path = file_path.relative_to(base_path)
                    # 在相对路径前加上根目录名称
                    zip_path_in_archive = pathlib.Path(root_folder_name) / relative_path

                    # 检查该文件是否需要按选项做内容覆盖
                    transformed = get_transformed_bytes(file_path, base_path, options)
                    if transformed is not None:
                        zinfo = zipfile.ZipInfo(zip_path_in_archive.as_posix())
                        zinfo.compress_type = zipfile.ZIP_DEFLATED
                        # 保留可读权限
                        zinfo.external_attr = 0o644 << 16
                        zipf.writestr(zinfo, transformed)
                        transformed_count += 1
                        print(f'[打包*] {zip_path_in_archive}  (按所选配置已覆盖)')
                    else:
                        # 确保使用UTF-8编码
                        zipf.write(file_path, zip_path_in_archive, compress_type=zipfile.ZIP_DEFLATED)
                        print(f'[打包] {zip_path_in_archive}')
                    included_count += 1

    # 获取打包文件大小
    file_size = zip_path.stat().st_size / (1024 * 1024)  # 转换为MB

    print('=' * 60)
    print(f'打包完成!')
    print(f'包含文件数: {included_count}')
    print(f'其中按配置覆盖: {transformed_count}')
    print(f'排除目录数: {excluded_dir_count}')
    print(f'排除文件数: {excluded_count}')
    print(f'打包文件: {zip_filename}')
    print(f'文件大小: {file_size:.2f} MB')
    print('=' * 60)

    # 清理历史部署包（保留最近 KEEP_PACKAGES 个）
    import glob
    all_zips = sorted(glob.glob(str(base_path / "deploy_package_*.zip")), reverse=True)
    if len(all_zips) > KEEP_PACKAGES:
        to_remove = all_zips[KEEP_PACKAGES:]
        print(f'🧹 清理历史部署包：删除 {len(to_remove)} 个，保留最近 {KEEP_PACKAGES} 个')
        for old_zip in to_remove:
            print(f'   🗑️  {pathlib.Path(old_zip).name}')
            os.remove(old_zip)

    # 创建一个必读文件说明
    readme_content = f"""部署包说明
================

打包时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
包含文件: {included_count} 个

部署（Docker 方式）:
1. 解压此文件到服务器目录
2. 确认 .env.prod 配置正确（数据库、模型 Key 等）
3. bash restart_cesi-fast-admin.sh
   （脚本自动检测依赖变更：pdm.lock / pyproject.toml 未变 → 跳过镜像重建，
   仅重启 app；有变更或首次部署 → 自动 --build。表结构由程序启动时自动创建）

后续更新:
- 重复"打包 → 上传 → bash restart_cesi-fast-admin.sh"即可，脚本自动选最快路径
- 前端已在打包时预构建（web/dist 由 nginx 直接挂载），前端更新无需重建、无需重启
- 手动强制重建: docker compose up -d --build

注意事项:
- 首次构建需要几分钟（安装依赖 + 下载 Chromium 浏览器，均已切国内镜像源）
- restart_cesi-fast-admin.sh 默认不覆盖服务器已配置的 .env.prod
"""

    readme_path = base_path / 'DEPLOY_README.txt'
    with open(readme_path, 'w', encoding='utf-8') as f:
        f.write(readme_content)

    print(f'已生成部署说明文件: DEPLOY_README.txt')
    print()


if __name__ == '__main__':
    try:
        create_deploy_package()
    except Exception as e:
        print(f'打包失败: {e}')
        import traceback
        traceback.print_exc()
