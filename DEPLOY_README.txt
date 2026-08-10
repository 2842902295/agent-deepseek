部署包说明
================

打包时间: 2026-08-10 08:46:52
包含文件: 1907 个

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
