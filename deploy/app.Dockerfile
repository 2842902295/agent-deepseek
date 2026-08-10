# 唯一后端镜像：依赖与浏览器全部构建期烘焙，运行时零安装（与 web.Dockerfile 同范式）。
# - 依赖：pdm install --frozen-lockfile → /opt/venv（项目目录之外，不被运行时 volume 覆盖）
# - Crawl4AI：预装 Playwright Chromium 浏览器 + 系统依赖库（版本自动匹配 pdm.lock，无需写死）
# 在线 docker-compose（app / cron_reset）与离线打包（pack_deploy_offline.py）共用。
#
# ⚠️ 层顺序按"更新频率从低到高"排列，充分利用 Docker 层缓存：
#   apt/字体(极稳定) → pdm → browser-use(版本锁死、与主依赖无关) → officecli(同上)
#   → 主依赖 pyproject/pdm.lock(构建中最常变的输入) → playwright(版本跟随 pdm.lock，必须在其后)
#   → chromium-debug.sh(独立小文件放最后)。
# pdm.lock 变更时只从"主依赖"层开始重建，browser-use / officecli 等重型层全部命中缓存。
FROM python:3.12-slim-bookworm
WORKDIR /opt/fast-soy-admin
ENV PDM_CHECK_UPDATE=false

# ===== 稳定层 1：系统依赖与字体（极少变动）=====
# 切清华 Debian 源加速 apt（兼容老式 sources.list 与 bookworm 的 deb822 格式）；
# 中文字体（wqy-microhei / wqy-zenhei / DroidSansFallbackFull）供出图/PDF 等场景；tzdata 供 TZ 生效
RUN find /etc/apt -type f \( -name '*.list' -o -name '*.sources' \) -exec \
        sed -i \
            -e 's|http://deb.debian.org|http://mirrors.tuna.tsinghua.edu.cn|g' \
            -e 's|http://security.debian.org|http://mirrors.tuna.tsinghua.edu.cn|g' \
            {} + \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        fonts-wqy-microhei \
        fonts-wqy-zenhei \
        fonts-droid-fallback \
        fontconfig \
        tzdata \
    && fc-cache -fv \
    && rm -rf /var/lib/apt/lists/*

# ===== 稳定层 2：pdm 本身 =====
# 安装 pdm（相当于旧 seven45/pdm-ci 基座 = python 官方镜像 + pdm，但基座升级到 Debian 12）
RUN python -m pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple pdm

# ===== 稳定层 3：与主项目依赖(pdm.lock)无关的独立工具，放在主依赖之前 =====
# pdm.lock 变更重建时这些层全部命中缓存，不会连带重装

# browser-use CLI（浏览器自动化 skill，Bash 独立调用，与主程序无共享 import）。
# 其依赖全是 "==" 全量锁定（pydantic==2.12.5 / anthropic==0.76.0 / openai==2.16.0 / mcp==1.26.0 等），
# 与 pdm.lock 锁定的主 venv 版本大面积冲突，直接装进主 venv 会回滚依赖、破坏 FastAPI/langchain
# → 装入独立 venv，仅把 CLI 可执行文件软链进 /usr/local/bin。版本写死，保证多次构建结果一致
# （此时主 venv 尚未创建，直接用基础镜像 python 3.12 建 venv，解释器版本一致）
RUN python -m venv /opt/browser-use-venv \
    && /opt/browser-use-venv/bin/pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple browser-use==0.13.6 \
    && for cmd in browser-use browseruse bu browser; do ln -s /opt/browser-use-venv/bin/$cmd /usr/local/bin/$cmd; done \
    && /opt/browser-use-venv/bin/python -c "import browser_use, browser_harness, cdp_use"

# officecli：Office 文档（.docx/.xlsx/.pptx）全能 CLI，单二进制无依赖。
# agent 的 office-cli skill 依赖此 CLI 完成所有 Office 文档操作
RUN curl -fsSL https://d.officecli.ai/install.sh | bash \
    && (command -v officecli >/dev/null || ln -sf "$HOME/.local/bin/officecli" /usr/local/bin/officecli) \
    && officecli --version

# ===== 易变层：主项目依赖（pyproject.toml / pdm.lock 是构建中最常变动的输入）=====
# 依赖烘焙进 /opt/venv：venv 放在项目目录之外的固定路径，避免被运行时 volume mount 覆盖
# （沿用旧 app.offline.Dockerfile 的约定）。pdm 会复用 VIRTUAL_ENV 指向的 venv。
COPY pyproject.toml pdm.lock ./
RUN python -m venv /opt/venv
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pdm config pypi.url https://pypi.tuna.tsinghua.edu.cn/simple/ \
    && pdm install --frozen-lockfile --no-editable --no-self

# Crawl4AI 依赖：Playwright Chromium 浏览器 + 系统依赖库。
# 用 venv 自带的 playwright 安装，版本与 pdm.lock 天然匹配（必须放在 pdm install 之后）；
# PLAYWRIGHT_DOWNLOAD_HOST 走 npmmirror 国内镜像加速（构建机可直连官方 CDN 时可去掉）
ENV PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright
RUN playwright install --with-deps chromium

# 构建期自检：依赖或浏览器装错直接让构建失败，不留到运行时才暴露
RUN python -c "import crawl4ai, playwright"

# browser-harness 只通过 BH_CHROME_PATH / CHROME_PATH 或 PATH（google-chrome* / chromium*）找浏览器，
# 不认 playwright 缓存目录。把上面装好的 playwright chromium（版本匹配 pdm.lock，glob 避免写死修订号）
# 软链到 /usr/local/bin/chromium：agent 可直接 `chromium --headless ...` 拉起，doctor 探测也能命中
RUN ln -s "$(ls -d /root/.cache/ms-playwright/chromium-*/chrome-linux*/chrome | head -1)" /usr/local/bin/chromium \
    && test -x /usr/local/bin/chromium

# ===== 最易变的独立小文件放最后：变动不牵连上面任何重型层 =====
# headless chromium 幂等启动器（browser-use 用）：chromium-debug [port]，默认开 CDP 9222
COPY deploy/chromium-debug.sh /usr/local/bin/chromium-debug
RUN chmod +x /usr/local/bin/chromium-debug

ENV LANG=zh_CN.UTF-8
ENV TZ=Asia/Shanghai
EXPOSE 9999
CMD ["python", "run.py"]
