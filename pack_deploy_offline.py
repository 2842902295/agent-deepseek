#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
离线 Docker 部署打包脚本

在有网络的机器上执行：
  python pack_deploy_offline.py

流程：
  1. 构建 app 镜像（依赖在构建时装入 /opt/venv，源码运行时挂载）
  2. 拉取 nginx:1.31.2-alpine / redis:7-alpine 基础镜像
  3. docker save 导出所有镜像为 tar
  4. 构建前端（pnpm build），将 web/dist 打进包
  5. 生成 docker-compose.offline.yml（代码全部 volume 挂载）
  6. 打包 → offline_package_<timestamp>.zip

目标机器（无网络）：
  unzip -O UTF-8 offline_package_*.zip && cd cesi-fast-admin && bash deploy_offline.sh
"""
import hashlib
import os
import pathlib
import fnmatch
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime

BASE_PATH = pathlib.Path(__file__).parent
OUTPUT_DIR = BASE_PATH / 'offline_deploy'  # 所有生成文件统一放这里
IMAGES_DIR = OUTPUT_DIR / 'offline_images'

APP_IMAGE = 'cesi-fast-admin-app:latest'
# 固定版本：1.31.3+ 在老内核（CentOS 7 / kernel 3.10）上 pwrite /run/nginx.pid 报 EPERM 无法启动（nginx/docker-nginx#1059）
NGINX_IMAGE = 'nginx:1.31.2-alpine'
REDIS_IMAGE = 'redis:7-alpine'

IMAGES = [
    (APP_IMAGE, 'app.tar'),
    (NGINX_IMAGE, 'nginx.tar'),
    (REDIS_IMAGE, 'redis.tar'),
]

# ── 文件/目录忽略规则（与 .gitignore 对齐）─────────────────────────────────────
# 注意：本脚本走白名单 include_dirs，所以这里的规则只在白名单目录内部的子文件/子目录过滤
SKIP_DIR_NAMES = {
    '__pycache__', '.venv', 'venv', '.ruff_cache', '.pytest_cache', '.mypy_cache',
    '.git', '.idea', '.vscode', '.codex', '.claude',
    '.tmp_images', '.extraction_log', '.qa_workspace',
    'node_modules', '.VSCodeCounter', '.pnpm-store',
}
SKIP_DIR_GLOBS = ['*.egg-info']

SKIP_FILE_EXTS = {'.pyc', '.pyo', '.log', '.sqlite3', '.sqlite3-shm', '.sqlite3-wal', '.sqlite3-journal'}
SKIP_FILE_NAMES = {'tortoise_queries.json', 'tortoise_last.txt'}
SKIP_FILE_GLOBS = ['*.local', '.env.local', '.env.*.local', '*.suo', '*.sw?']


def _should_skip_dir(name: str) -> bool:
    if name in SKIP_DIR_NAMES:
        return True
    return any(fnmatch.fnmatch(name, p) for p in SKIP_DIR_GLOBS)


def _should_skip_file(name: str) -> bool:
    if name in SKIP_FILE_NAMES:
        return True
    if pathlib.Path(name).suffix in SKIP_FILE_EXTS:
        return True
    return any(fnmatch.fnmatch(name, p) for p in SKIP_FILE_GLOBS)

# docker-compose.offline.yml：依赖全在镜像里，代码全部挂载
DOCKER_COMPOSE_OFFLINE = """\
services:
  nginx:
    image: nginx:1.31.2-alpine
    ports:
      - "1880:80"
    volumes:
      - ./web/dist:/var/www/html/fast-soy-admin:ro
      - ./deploy/web.conf:/etc/nginx/conf.d/default.conf:ro
    depends_on:
      app:
        condition: service_started
    restart: always
    networks:
      - internal

  app:
    image: cesi-fast-admin-app:latest
    ports:
      - "9999:9999"
    working_dir: /opt/fast-soy-admin
    # browser-use / Chromium 的 sandbox 需要 SYS_ADMIN 创建 namespace，否则浏览器进程 fork 后挂死超时；
    # 容器 /dev/shm 默认仅 64MB，Chromium 共享内存不足会崩溃，显式扩到 512mb
    cap_add:
      - SYS_ADMIN
    shm_size: 512mb
    env_file:
      - .env.prod
    volumes:
      # 源码挂载，更新代码后 docker compose restart app 即可生效
      - ./app:/opt/fast-soy-admin/app
      - ./run.py:/opt/fast-soy-admin/run.py
      - ./migrations:/opt/fast-soy-admin/migrations
      - ./static:/opt/fast-soy-admin/static
      # Agent skills（只读挂载；skill 同步跨设备自动回退复制，见 qa.py::_hardlink_tree）
      - ./.agent_workspace/.agent_skills:/opt/fast-soy-admin/.agent_workspace/.agent_skills:ro
      # 持久化数据
      - ./data:/opt/fast-soy-admin/data
      - ./logs:/opt/fast-soy-admin/logs
      - ./app_system.sqlite3:/opt/fast-soy-admin/app_system.sqlite3
    networks:
      - internal
    restart: always

  redis:
    image: redis:7-alpine
    networks:
      - internal
    restart: always
    volumes:
      - redis_data:/data

volumes:
  redis_data:

networks:
  internal:
    driver: bridge
"""

DEPLOY_SCRIPT = """\
#!/bin/bash
set -e
echo "=== 离线 Docker 部署 ==="

echo "[1/2] 加载镜像..."
for tar in offline_images/*.tar; do
  echo "  加载 $tar"
  docker load -i "$tar"
done

echo "[2/2] 启动服务..."
docker compose -f docker-compose.offline.yml up -d

echo ""
echo "部署完成！"
echo "  前端:       http://<服务器IP>:1880"
echo "  后端 API:   http://<服务器IP>:9999"
echo "  SQLite 管理: http://<服务器IP>:4568"
echo ""
echo "更新代码后执行: docker compose -f docker-compose.offline.yml restart app"
echo "更新前端后执行: 替换 web/dist/ 目录内容，nginx 自动生效（无需重启）"
"""


def run(cmd: list[str], desc: str):
    print(f'\n[执行] {desc}')
    print(f'  $ {" ".join(cmd)}')
    # Windows 下 .cmd 脚本（pnpm、docker 等）需要 shell=True 才能找到
    result = subprocess.run(cmd, shell=(sys.platform == 'win32'))
    if result.returncode != 0:
        print(f'[错误] 命令失败，退出码 {result.returncode}')
        sys.exit(1)


def build_app_image():
    print('\n=== Step 1: 构建后端镜像（依赖与 Chromium 浏览器装入镜像，代码运行时挂载）===')
    # 只校验不重新解析：依赖真相源是仓库提交的 pdm.lock
    # （如需更新依赖，请先在本地 pdm lock 并提交，再重新打包）
    run(['pdm', 'lock', '--check'], '校验 pdm.lock 与 pyproject.toml 同步')
    run(
        ['docker', 'build', '-f', 'deploy/app.Dockerfile', '-t', APP_IMAGE, '.'],
        f'构建 {APP_IMAGE}'
    )


def pull_base_images():
    print('\n=== Step 2: 拉取基础镜像 ===')
    for image, _ in IMAGES[1:]:
        run(['docker', 'pull', image], f'拉取 {image}')


def save_images():
    print('\n=== Step 3: 导出镜像为 tar ===')
    if IMAGES_DIR.exists():
        shutil.rmtree(IMAGES_DIR)
    IMAGES_DIR.mkdir(parents=True)

    for image_name, tar_name in IMAGES:
        tar_path = IMAGES_DIR / tar_name
        run(['docker', 'save', '-o', str(tar_path), image_name], f'导出 {image_name} → {tar_name}')
        size_mb = tar_path.stat().st_size / (1024 * 1024)
        print(f'  大小: {size_mb:.1f} MB')


# 前端源码指纹文件（存 web/ 根下，不进包）：源码无变化则跳过构建，复用现有 web/dist
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


def build_frontend():
    print('\n=== Step 4: 构建前端 ===')
    web_dir = BASE_PATH / 'web'
    dist_dir = web_dir / 'dist'

    if not (web_dir / 'package.json').exists():
        print('  [跳过] 未找到 web/package.json')
        return

    fingerprint_file = web_dir / FRONTEND_FINGERPRINT_NAME
    print('  计算前端源码指纹，判断是否需要构建...')
    fingerprint = compute_frontend_fingerprint(web_dir)
    last_fingerprint = ''
    if fingerprint_file.exists():
        last_fingerprint = fingerprint_file.read_text(encoding='utf-8').strip()

    if dist_dir.exists() and last_fingerprint and last_fingerprint == fingerprint:
        print('  [跳过] 前端源码无变化，复用现有 web/dist，跳过构建')
        return

    run(['pnpm', '--dir', str(web_dir), 'install'], '安装前端依赖')
    run(['pnpm', '--dir', str(web_dir), 'build'], '构建前端')

    if not dist_dir.exists():
        print('[错误] pnpm build 完成但 web/dist 不存在，请检查构建配置')
        sys.exit(1)

    # 构建成功后以构建后的磁盘状态重新记录指纹（构建过程会在 src 下生成
    # elegant-router 路由声明等文件），下次打包才能正确判定"无变化"
    fingerprint_file.write_text(compute_frontend_fingerprint(web_dir) + '\n', encoding='utf-8')
    print(f'  前端构建完成: web/dist/')


def write_offline_files():
    print('\n=== Step 5: 生成离线配置文件 ===')
    OUTPUT_DIR.mkdir(exist_ok=True)
    (OUTPUT_DIR / 'docker-compose.offline.yml').write_text(DOCKER_COMPOSE_OFFLINE, encoding='utf-8')
    print('  生成 offline_deploy/docker-compose.offline.yml')
    (OUTPUT_DIR / 'deploy_offline.sh').write_text(DEPLOY_SCRIPT, encoding='utf-8')
    print('  生成 offline_deploy/deploy_offline.sh')


def create_zip():
    print('\n=== Step 6: 打包 ===')
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    zip_filename = f'offline_package_{timestamp}.zip'
    zip_path = OUTPUT_DIR / zip_filename
    root_name = 'cesi-fast-admin'

    include_files = [
        ('offline_deploy/docker-compose.offline.yml', 'docker-compose.offline.yml'),
        ('offline_deploy/deploy_offline.sh', 'deploy_offline.sh'),
        ('.env.prod', '.env.prod'),
        ('run.py', 'run.py'),
        ('pyproject.toml', 'pyproject.toml'),
        ('pdm.lock', 'pdm.lock'),
    ]
    include_dirs = [
        ('offline_deploy/offline_images', 'offline_images'),
        ('app', 'app'),
        ('web/dist', 'web/dist'),
        ('migrations', 'migrations'),
        ('static', 'static'),
        ('deploy', 'deploy'),
        ('.agent_workspace/.agent_skills', '.agent_workspace/.agent_skills'),
    ]

    count = 0
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for src_name, arc_name in include_files:
            fpath = BASE_PATH / src_name
            if fpath.exists():
                zipf.write(fpath, f'{root_name}/{arc_name}')
                print(f'  [+] {arc_name}')
                count += 1
            else:
                print(f'  [跳过] {src_name} 不存在')

        for src_dname, arc_dname in include_dirs:
            dpath = BASE_PATH / src_dname
            if not dpath.exists():
                print(f'  [跳过] {src_dname}/ 不存在')
                continue
            for root, dirs, files in os.walk(dpath):
                # 原地过滤，避免进入忽略目录
                dirs[:] = [d for d in dirs if not _should_skip_dir(d)]
                for f in files:
                    if _should_skip_file(f):
                        continue
                    fp = pathlib.Path(root) / f
                    rel = fp.relative_to(dpath)
                    arc_path = f'{root_name}/{arc_dname}/{rel}'
                    zipf.write(fp, arc_path)
                    print(f'  [+] {arc_dname}/{rel}')
                    count += 1

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f'\n打包完成: offline_deploy/{zip_filename}  ({size_mb:.1f} MB，共 {count} 个文件)')
    return zip_filename


UPDATE_SCRIPT = """\
#!/bin/bash
set -e
echo "=== 更新代码 ==="
echo "[1/2] 清理旧版代码并解压新包..."
# 先清理包内全覆盖的代码目录，防止已删除的文件残留（unzip -o 只覆盖不删除）
rm -rf app web/dist migrations
# 覆盖解压（-O UTF-8：包内含中文文件名；更新包不含 .env.prod，无需排除）
if ! unzip -O UTF-8 -o "$1" -d .; then
    echo "⚠️  当前 unzip 不支持 -O，改用 python3 解压..."
    python3 - "$1" <<'PYEOF'
import sys, zipfile
zipfile.ZipFile(sys.argv[1]).extractall(".")
print("解压完成")
PYEOF
fi

echo "[2/2] 重启后端..."
docker compose -f docker-compose.offline.yml restart app
echo "更新完成！前端已自动生效，后端已重启。"
"""


def create_update_zip():
    print('\n=== 打包代码更新包 ===')
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    zip_filename = f'update_package_{timestamp}.zip'
    zip_path = OUTPUT_DIR / zip_filename
    root_name = 'cesi-fast-admin'

    include_files = [('run.py', 'run.py')]
    include_dirs = [
        ('app', 'app'),
        ('web/dist', 'web/dist'),
        ('migrations', 'migrations'),
    ]

    count = 0
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for src_name, arc_name in include_files:
            fpath = BASE_PATH / src_name
            if fpath.exists():
                zipf.write(fpath, f'{root_name}/{arc_name}')
                print(f'  [+] {arc_name}')
                count += 1

        for src_dname, arc_dname in include_dirs:
            dpath = BASE_PATH / src_dname
            if not dpath.exists():
                print(f'  [跳过] {src_dname}/ 不存在')
                continue
            for root, dirs, files in os.walk(dpath):
                dirs[:] = [d for d in dirs if not _should_skip_dir(d)]
                for f in files:
                    if _should_skip_file(f):
                        continue
                    fp = pathlib.Path(root) / f
                    rel = fp.relative_to(dpath)
                    zipf.write(fp, f'{root_name}/{arc_dname}/{rel}')
                    count += 1
            print(f'  [+] {arc_dname}/')

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f'\n打包完成: offline_deploy/{zip_filename}  ({size_mb:.1f} MB，共 {count} 个文件)')
    return zip_filename


def _read_key_unix() -> str:
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
    if os.name == 'nt':
        return _read_key_windows()
    return _read_key_unix()


def _supports_arrow_keys() -> bool:
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
    print()
    print(question)

    if not sys.stdin.isatty():
        value, label = options[default_index]
        for i, (_, lbl) in enumerate(options, start=1):
            mark = ' (默认)' if (i - 1) == default_index else ''
            print(f'  {i}) {lbl}{mark}')
        print(f'[非交互模式] 使用默认: {label}')
        return value

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

    cursor = default_index
    print('（↑/↓ 选择，Enter 确认，q/Esc 取消使用默认）')

    def render(first: bool):
        if not first:
            sys.stdout.write(f'\x1b[{len(options)}A')
        for i, (_, lbl) in enumerate(options):
            prefix = '> ' if i == cursor else '  '
            line = f'{prefix}{lbl}'
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


if __name__ == '__main__':
    OUTPUT_DIR.mkdir(exist_ok=True)

    print('=' * 60)
    print('离线 Docker 部署打包工具')
    print('=' * 60)

    mode = prompt_choice(
        '选择打包模式：',
        [
            ('update', '仅打包代码（日常更新，不重新构建镜像）'),
            ('full',   '完整打包（首次部署，含 Docker 镜像）'),
        ],
        default_index=0,
    )

    if mode == 'update':
        print('\n=== 代码更新打包 ===')
        build_frontend()
        zip_name = create_update_zip()
        (OUTPUT_DIR / 'update.sh').write_text(UPDATE_SCRIPT, encoding='utf-8')
        print(f'\n传到目标机器后：')
        print(f'  bash update.sh offline_deploy/{zip_name}')
    else:
        print('\n=== 离线 Docker 部署打包脚本 ===')
        build_app_image()
        pull_base_images()
        save_images()
        build_frontend()
        write_offline_files()
        zip_name = create_zip()
        print(f'\n全部完成！将 offline_deploy/{zip_name} 传到目标机器后：')
        print('  unzip -O UTF-8 offline_package_*.zip && cd cesi-fast-admin && bash deploy_offline.sh')
