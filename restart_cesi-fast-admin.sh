#!/bin/bash

# 保留最近几个历史部署包，其余删除（设为 0 则全部删除，只留当前最新）
KEEP_PACKAGES=3

# ── 部署模式选择（↑/↓ 键移动高亮，回车确认；直接按 1/2 数字键也可选择）──
# 1 = 自动判断：依赖（pdm.lock / pyproject.toml）或构建配置有变更才重建镜像 —— 默认
# 2 = 强制重建镜像：没变但确实需要重建时用（典型场景：上一次构建中途失败，
#     新的 pdm.lock 已解压落盘，自动判断会误判为"依赖未变更"而跳过重建）
# 兼容自动化场景：FORCE_BUILD=1 等价于选 2 并跳过询问
MODE=1
if [ "${FORCE_BUILD:-0}" = "1" ]; then
    MODE=2
    echo "🔧 FORCE_BUILD=1 → 强制重建镜像模式"
else
    # stdin 不是终端（如 面板执行 / ssh host 'cmd'）时，尝试借用控制终端 /dev/tty
    # 完成交互；连控制终端都没有（cron / 纯后台）→ 跳过询问走默认模式
    if [ ! -t 0 ]; then
        if (exec </dev/tty) 2>/dev/null; then
            exec </dev/tty
        fi
    fi
    if [ -t 0 ]; then
        MODE_OPTIONS=(
            "自动判断（依赖/构建配置有变更才重建镜像）— 默认"
            "强制重建镜像（上次构建失败 / 镜像异常时用）"
        )
        MODE_SEL=0
        echo ""
        echo "请选择部署模式（↑/↓ 移动，回车确认；直接按 1/2 也行）："
        # 固定绘制 ${#MODE_OPTIONS[@]} 行；按键后先把光标上移同样行数再重绘覆盖
        draw_mode_menu() {
            local i
            for i in "${!MODE_OPTIONS[@]}"; do
                if [ "$i" -eq "$MODE_SEL" ]; then
                    printf '  \033[7m❯ %d) %s\033[0m\n' $((i + 1)) "${MODE_OPTIONS[$i]}"
                else
                    printf '    %d) %s\n' $((i + 1)) "${MODE_OPTIONS[$i]}"
                fi
            done
        }
        draw_mode_menu
        while true; do
            IFS= read -rsn1 MODE_KEY || { echo ""; exit 130; }   # EOF / Ctrl+C → 退出
            case "$MODE_KEY" in
                $'\e')
                    # ESC 序列：再读两字符（[A=上，[B=下）；单独按 ESC 会超时得到空串
                    read -rsn2 -t 0.1 MODE_KEY || MODE_KEY=""
                    case "$MODE_KEY" in
                        '[A') [ "$MODE_SEL" -gt 0 ] && MODE_SEL=$((MODE_SEL - 1)) ;;
                        '[B') [ "$MODE_SEL" -lt $((${#MODE_OPTIONS[@]} - 1)) ] && MODE_SEL=$((MODE_SEL + 1)) ;;
                    esac
                    printf '\033[%dA' "${#MODE_OPTIONS[@]}"
                    draw_mode_menu
                    ;;
                1) MODE=1; break ;;
                2) MODE=2; break ;;
                '') MODE=$((MODE_SEL + 1)); break ;;   # 回车 = 确认当前高亮项
            esac
        done
        if [ "$MODE" = "2" ]; then
            echo "✅ 已选择：强制重建镜像"
        else
            echo "✅ 已选择：自动判断"
        fi
    else
        echo "⚡ 非交互环境（无终端）→ 按默认模式部署（自动判断）"
    fi
fi

# 本脚本自身也在部署包里（cesi-fast-admin/restart_cesi-fast-admin.sh）。
# 批量解压时必须把它排除：原地覆盖（同 inode truncate+write）会置换 bash 正在执行的本文件，
# bash 后续读取按旧字节偏移读进新文件内容 → 尾部部署命令被解析成垃圾 → 要跑第二遍才生效。
# 脚本末尾会单独把它解到临时目录再用 mv 覆盖（rename 只换目录项，旧 inode 继续供 bash 读取）。
SELF_NAME="restart_cesi-fast-admin.sh"
SELF_IN_ZIP="cesi-fast-admin/$SELF_NAME"

# 1. 查找当前目录下前缀为 deploy_package_ 的最新 zip 文件（按文件修改时间，最新上传/生成的优先）
echo "🔍 正在查找最新的部署包..."
LATEST_ZIP=()

# 使用 nullglob 防止没有匹配文件时返回字面量
shopt -s nullglob
LATEST_ZIP=(deploy_package_*.zip)

if [ ${#LATEST_ZIP[@]} -eq 0 ]; then
    echo "❌ 错误：未找到任何 deploy_package_*.zip 文件。"
    exit 1
fi

# 按 mtime 取最新的一个（ls -t）。
# 不能按文件名下划线分列排序：文件名含品牌变体段（deploy_package_standard_20260817_…），
# 新旧格式混排时变体段会被误当时间戳列参与比较，选错包。
LATEST_ZIP_FILE=$(ls -1t "${LATEST_ZIP[@]}" 2>/dev/null | head -n1)

if [ ! -f "$LATEST_ZIP_FILE" ]; then
    echo "❌ 错误：未能正确解析最新的 ZIP 文件。"
    exit 1
fi

echo "✅ 找到最新文件: $LATEST_ZIP_FILE"
# 绝对化：后面会 cd 进 cesi-fast-admin/，末尾的脚本自更新步骤仍要引用这个 zip
LATEST_ZIP_FILE="$(pwd)/$LATEST_ZIP_FILE"

# 依赖指纹：去除 \r 后再取 md5。
# ⚠️ 本检查是"文件字节是否变化"，不是"依赖声明是否变化"：
# pdm.lock 在 Windows 上打包带 CRLF 换行，而服务器侧任何重写该文件的操作
# （如容器内跑过 pdm install，容器挂载的就是本目录）都会变成 LF。
# 换行符差异不是依赖变更，按原始字节比较会永久误报"依赖变更"、每次都全量重建。
norm_hash() {
    if [ -f "$1" ]; then
        tr -d '\r' < "$1" | md5sum | awk '{print $1}'
    else
        echo ""
    fi
}

# 1.5 记录解压前的依赖指纹，用于判断是否需要重建镜像（首次部署为空 → 视为有变更）。
#     除 pdm.lock / pyproject.toml 外，Dockerfile / compose 文件变化同样需要重建
#     （曾发生：Dockerfile 改了，但依赖没变 → 脚本跳过重建，改动永远不生效）
OLD_LOCK_HASH=$(norm_hash cesi-fast-admin/pdm.lock)
OLD_PROJ_HASH=$(norm_hash cesi-fast-admin/pyproject.toml)
OLD_DEPLOY_HASH=$(cat cesi-fast-admin/deploy/app.Dockerfile cesi-fast-admin/deploy/web.Dockerfile cesi-fast-admin/docker-compose.yml 2>/dev/null | tr -d '\r' | md5sum | awk '{print $1}')

# 2.0 清理包内全覆盖的代码目录，防止已删除的文件在服务器上残留
#     （unzip -o 只覆盖不删除；残留的已删页面会被 elegant-router 扫描进构建导致失败）。
#     首次部署时这些目录可能不存在，rm -rf 无副作用；.env.prod / data / 运行时数据不在清理范围内。
echo "🧹 清理旧版代码文件..."
rm -rf cesi-fast-admin/app \
       cesi-fast-admin/web/src \
       cesi-fast-admin/web/packages \
       cesi-fast-admin/web/dist \
       cesi-fast-admin/deploy \
       cesi-fast-admin/.agent_workspace/.agent_skills

# 2. 解压文件（保留已存在的 .env.prod）
echo "📦 正在解压 $LATEST_ZIP_FILE ..."
# -O UTF-8: 包内含中文文件名（skill 目录等），unzip 默认按本地编码解出来会是乱码
# -o: 覆盖已存在的文件而无需询问
# -x: 排除指定文件（保留服务器上现有 .env.prod；部署脚本自身延迟到末尾用 rename 方式安全更新，
#     见文件头部 SELF_NAME 注释）
if ! unzip -O UTF-8 -o "$LATEST_ZIP_FILE" -x "cesi-fast-admin/.env.prod" "$SELF_IN_ZIP"; then
    # 个别精简系统的 unzip 不支持 -O：回退 python3（zipfile 正确识别 UTF-8 文件名标记）
    echo "⚠️  当前 unzip 不支持 -O，改用 python3 解压..."
    python3 - "$LATEST_ZIP_FILE" <<'PYEOF' || { echo "❌ 解压失败，请检查文件完整性。"; exit 1; }
import sys, zipfile
# .env.prod 保留服务器现有配置；restart_cesi-fast-admin.sh 是可能正在执行的本脚本，
# 同样不在此处原地覆盖（由 shell 末尾的 rename 自更新步骤接管）
_SKIP = {"cesi-fast-admin/.env.prod", "cesi-fast-admin/restart_cesi-fast-admin.sh"}
z = zipfile.ZipFile(sys.argv[1])
for m in z.infolist():
    if m.filename in _SKIP or m.is_dir():
        continue
    z.extract(m, ".")
print("解压完成")
PYEOF
fi

# 仅在 .env.prod 不存在时（首次部署）从 zip 中取出默认配置
# -n: never overwrite，已存在则跳过
unzip -n "$LATEST_ZIP_FILE" "cesi-fast-admin/.env.prod"

echo "🎉 解压完成。"

# 2.5 清理历史部署包（保留最近 KEEP_PACKAGES 个，按 mtime 排序，同上）
ALL_ZIPS=(deploy_package_*.zip)
if [ ${#ALL_ZIPS[@]} -gt 0 ]; then
    mapfile -t ALL_ZIPS < <(ls -1t "${ALL_ZIPS[@]}" 2>/dev/null)
fi
if [ ${#ALL_ZIPS[@]} -gt $KEEP_PACKAGES ]; then
    REMOVE_COUNT=$(( ${#ALL_ZIPS[@]} - KEEP_PACKAGES ))
    echo "🧹 清理历史部署包：删除 $REMOVE_COUNT 个，保留最近 $KEEP_PACKAGES 个"
    for (( i=KEEP_PACKAGES; i<${#ALL_ZIPS[@]}; i++ )); do
        echo "   🗑️  ${ALL_ZIPS[$i]}"
        rm -f "${ALL_ZIPS[$i]}"
    done
fi

# 3. 执行 Docker Compose 部署
# 假设这些命令是在当前目录或特定路径下执行
echo "🐳 正在执行容器部署..."

# 进入目录并执行命令 (请确保路径正确)
cd cesi-fast-admin || { echo "❌ 无法进入 cesi-fast-admin 目录"; exit 1; }

# 对比依赖指纹：全部未变（忽略换行符差异）→ 镜像无需重建
NEW_LOCK_HASH=$(norm_hash pdm.lock)
NEW_PROJ_HASH=$(norm_hash pyproject.toml)
NEW_DEPLOY_HASH=$(cat deploy/app.Dockerfile deploy/web.Dockerfile docker-compose.yml 2>/dev/null | tr -d '\r' | md5sum | awk '{print $1}')
DEP_CHANGED=1
if [ "$OLD_LOCK_HASH" = "$NEW_LOCK_HASH" ] && [ "$OLD_PROJ_HASH" = "$NEW_PROJ_HASH" ] && [ "$OLD_DEPLOY_HASH" = "$NEW_DEPLOY_HASH" ]; then
    DEP_CHANGED=0
fi

# 强制重建模式优先于指纹判断
if [ "$MODE" = "2" ]; then
    DEP_CHANGED=1
    echo "🔧 已选强制重建 → 忽略依赖指纹，重建镜像"
fi

if [ "$DEP_CHANGED" = "1" ]; then
    # 依赖/构建配置有变更（或首次部署无旧指纹）：重建镜像。
    # 打印具体变更的文件，便于排查误报（如换行符以外的意外差异）
    echo "📦 检测到依赖/构建配置变更 → 重建镜像..."
    if [ -z "$OLD_LOCK_HASH" ] || [ -z "$OLD_PROJ_HASH" ]; then
        echo "   ↳ 原因：旧指纹缺失（首次部署，或解压前目录里缺少对应文件）"
    fi
    if [ -n "$OLD_LOCK_HASH" ] && [ "$OLD_LOCK_HASH" != "$NEW_LOCK_HASH" ]; then
        echo "   ↳ 原因：pdm.lock 内容变更"
    fi
    if [ -n "$OLD_PROJ_HASH" ] && [ "$OLD_PROJ_HASH" != "$NEW_PROJ_HASH" ]; then
        echo "   ↳ 原因：pyproject.toml 内容变更"
    fi
    if [ -n "$OLD_DEPLOY_HASH" ] && [ "$OLD_DEPLOY_HASH" != "$NEW_DEPLOY_HASH" ]; then
        echo "   ↳ 原因：Dockerfile / docker-compose.yml 内容变更"
    fi
    docker-compose up -d --build || { echo "❌ 镜像构建/启动失败，请检查上方报错"; exit 1; }
    # ⚠️ 重建分支必须补 restart app（2026-08-28 241 事故教训）：
    # 源码是 volume 挂载，新代码必须重启容器才加载。docker build 缓存全命中时
    # （纯代码变更场景）镜像内容不变 → compose 判定 app 服务无变化 → 不重建
    # 容器 → 旧代码留在内存，表现为"zip 传上去了但程序没更新"。
    # 无条件 restart app 兜底（镜像真变时多花几秒重启，可接受）。
    echo "🔄 重启 app 确保新代码生效（源码挂载不会自动加载）..."
    docker-compose restart app || { echo "❌ 重启 app 失败，代码不会生效，请检查上方报错"; exit 1; }
elif [ -z "$(docker-compose images -q app 2>/dev/null)" ]; then
    # 镜像不存在（服务器上首次部署）：同样需要构建
    echo "🐳 未找到 app 镜像（首次部署）→ 构建镜像..."
    docker-compose up -d --build || { echo "❌ 首次构建失败，请检查上方报错"; exit 1; }
else
    # 纯代码更新：跳过重建。源码由 volume 挂载，restart app 即生效；
    # up -d 只补齐未运行的服务（如 redis/nginx），不再 down 全停
    echo "⚡ 依赖未变更 → 跳过镜像重建，仅重启 app..."
    docker-compose up -d --no-build || { echo "❌ 启动服务失败，请检查上方报错"; exit 1; }
    # restart 失败必须显式报错退出：源码是挂载目录，容器不重启 → 进程仍跑旧代码，
    # 表现为"部署成功但代码没生效"（不能静默继续）
    docker-compose restart app || { echo "❌ 重启 app 失败，代码不会生效，请检查上方报错"; exit 1; }
fi

# ⚠️ nginx 必须重启（两个分支都要）：
# 前面 rm -rf cesi-fast-admin/web/dist 后由 unzip 重建了目录（新 inode），
# 而 nginx 容器的 bind mount 仍指向被删空的旧目录 inode → 容器内前端目录为空，
# 首页 try_files 落入重定向循环返回 500。restart 会重新建立 mount 指向新目录。
# （up -d --build 也不会重建 nginx：其镜像/配置无变化，compose 判定无需重建）
echo "🔄 重启 nginx 以刷新 web/dist / deploy 挂载..."
docker-compose restart nginx || echo "⚠️  nginx 重启失败，前端可能仍是旧文件，请手动执行: docker-compose restart nginx"

# ── 更新部署脚本自身（必须放在脚本最末尾）──────────────────────────────────────
# 从 zip 中把新版脚本解到临时目录，再用 mv 覆盖。mv 是 rename(2)：只把目录项
# 换到新 inode，即使目标文件正是 bash 正在执行的本脚本，bash 仍继续读旧 inode，
# 本次运行安全跑完，下次运行自动用新版。
# （绝不能 unzip -o / cp 原地覆盖：同 inode truncate+write 会破坏正在运行的脚本，
#   表现为"部署包解压成功但容器没重启，要跑第二遍才生效"。）
# 失败（如旧包里没有脚本条目）静默跳过，不影响已完成的部署。
echo "🔁 更新部署脚本自身..."
SELF_TMP="../.self_update_tmp.$$"
if mkdir -p "$SELF_TMP" && unzip -o "$LATEST_ZIP_FILE" "$SELF_IN_ZIP" -d "$SELF_TMP" >/dev/null 2>&1 \
   && [ -f "$SELF_TMP/$SELF_IN_ZIP" ]; then
    chmod +x "$SELF_TMP/$SELF_IN_ZIP" 2>/dev/null
    mv -f "$SELF_TMP/$SELF_IN_ZIP" "./$SELF_NAME"
    # 父目录若也有入口脚本（脚本与 zip 并排放在上层的常见布局），同步更新
    if [ -f "../$SELF_NAME" ] && unzip -o "$LATEST_ZIP_FILE" "$SELF_IN_ZIP" -d "$SELF_TMP" >/dev/null 2>&1 \
       && [ -f "$SELF_TMP/$SELF_IN_ZIP" ]; then
        chmod +x "$SELF_TMP/$SELF_IN_ZIP" 2>/dev/null
        mv -f "$SELF_TMP/$SELF_IN_ZIP" "../$SELF_NAME"
    fi
    echo "   ✅ 部署脚本已更新（下次运行生效）"
else
    echo "   ⏭️  包内无脚本条目，跳过自更新"
fi
rm -rf "$SELF_TMP" 2>/dev/null

# 跟踪日志输出
echo "📋 正在显示实时日志 (按 Ctrl+C 退出日志)..."

# --tail=100: 只显示每个服务最近 100 行再开始跟随。
# 不带 --tail 会把容器创建以来的全部历史日志先刷一遍
# （docker restart 不会清空容器日志文件，只有重建容器才会）。
# 使用 exec 直接替换当前进程，这样脚本退出时日志也会干净地结束
exec docker-compose logs -f --tail=100
