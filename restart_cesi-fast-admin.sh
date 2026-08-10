#!/bin/bash

# 保留最近几个历史部署包，其余删除（设为 0 则全部删除，只留当前最新）
KEEP_PACKAGES=3

# 1. 查找当前目录下前缀为 deploy_package_ 的最新 zip 文件
# -a: 将匹配的文件名赋值给数组
# -r: 逆序排序（最新的在最前面）
# -t: 按修改时间排序
# -1: 只取第一个（最新的）
echo "🔍 正在查找最新的部署包..."
LATEST_ZIP=()

# 使用 nullglob 防止没有匹配文件时返回字面量
shopt -s nullglob
LATEST_ZIP=(deploy_package_*.zip)

if [ ${#LATEST_ZIP[@]} -eq 0 ]; then
    echo "❌ 错误：未找到任何 deploy_package_*.zip 文件。"
    exit 1
fi

# 按时间排序并取最新的一个
LATEST_ZIP_FILE=$(printf '%s\n' "${LATEST_ZIP[@]}" | sort -t_ -k3,3nr -k4,4nr | head -n1)

if [ ! -f "$LATEST_ZIP_FILE" ]; then
    echo "❌ 错误：未能正确解析最新的 ZIP 文件。"
    exit 1
fi

echo "✅ 找到最新文件: $LATEST_ZIP_FILE"

# 1.5 记录解压前的依赖指纹，用于判断是否需要重建镜像（首次部署为空 → 视为有变更）
OLD_LOCK_HASH=$(md5sum cesi-fast-admin/pdm.lock 2>/dev/null | awk '{print $1}')
OLD_PROJ_HASH=$(md5sum cesi-fast-admin/pyproject.toml 2>/dev/null | awk '{print $1}')

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
# -x: 排除指定文件（保留服务器上现有 .env.prod）
if ! unzip -O UTF-8 -o "$LATEST_ZIP_FILE" -x "cesi-fast-admin/.env.prod"; then
    # 个别精简系统的 unzip 不支持 -O：回退 python3（zipfile 正确识别 UTF-8 文件名标记）
    echo "⚠️  当前 unzip 不支持 -O，改用 python3 解压..."
    python3 - "$LATEST_ZIP_FILE" <<'PYEOF' || { echo "❌ 解压失败，请检查文件完整性。"; exit 1; }
import sys, zipfile
z = zipfile.ZipFile(sys.argv[1])
for m in z.infolist():
    if m.filename == "cesi-fast-admin/.env.prod" or m.is_dir():
        continue
    z.extract(m, ".")
print("解压完成")
PYEOF
fi

# 仅在 .env.prod 不存在时（首次部署）从 zip 中取出默认配置
# -n: never overwrite，已存在则跳过
unzip -n "$LATEST_ZIP_FILE" "cesi-fast-admin/.env.prod"

echo "🎉 解压完成。"

# 2.5 清理历史部署包（保留最近 KEEP_PACKAGES 个）
ALL_ZIPS=($(printf '%s\n' deploy_package_*.zip | sort -t_ -k3,3nr -k4,4nr))
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

# 对比依赖指纹：pdm.lock / pyproject.toml 未变 → 镜像无需重建
NEW_LOCK_HASH=$(md5sum pdm.lock 2>/dev/null | awk '{print $1}')
NEW_PROJ_HASH=$(md5sum pyproject.toml 2>/dev/null | awk '{print $1}')
DEP_CHANGED=1
if [ "$OLD_LOCK_HASH" = "$NEW_LOCK_HASH" ] && [ "$OLD_PROJ_HASH" = "$NEW_PROJ_HASH" ]; then
    DEP_CHANGED=0
fi

if [ "$DEP_CHANGED" = "1" ]; then
    # 依赖有变更（或首次部署无旧指纹）：重建镜像
    echo "📦 检测到依赖变更（pdm.lock / pyproject.toml）→ 重建镜像..."
    docker-compose up -d --build
elif [ -z "$(docker-compose images -q app 2>/dev/null)" ]; then
    # 镜像不存在（服务器上首次部署）：同样需要构建
    echo "🐳 未找到 app 镜像（首次部署）→ 构建镜像..."
    docker-compose up -d --build
else
    # 纯代码更新：跳过重建。源码由 volume 挂载，restart app 即生效；
    # up -d 只补齐未运行的服务（如 redis/nginx），不再 down 全停
    echo "⚡ 依赖未变更 → 跳过镜像重建，仅重启 app..."
    docker-compose up -d --no-build
    docker-compose restart app
fi

# ⚠️ nginx 必须重启（两个分支都要）：
# 前面 rm -rf cesi-fast-admin/web/dist 后由 unzip 重建了目录（新 inode），
# 而 nginx 容器的 bind mount 仍指向被删空的旧目录 inode → 容器内前端目录为空，
# 首页 try_files 落入重定向循环返回 500。restart 会重新建立 mount 指向新目录。
# （up -d --build 也不会重建 nginx：其镜像/配置无变化，compose 判定无需重建）
echo "🔄 重启 nginx 以刷新 web/dist / deploy 挂载..."
docker-compose restart nginx

# 跟踪日志输出
echo "📋 正在显示实时日志 (按 Ctrl+C 退出日志)..."

# 使用 exec 直接替换当前进程，这样脚本退出时日志也会干净地结束
exec docker-compose logs -f
