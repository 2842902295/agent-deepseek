@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
REM 安装目录（%~dp0 以反斜杠结尾），作为进程路径锚点，避免误杀其它 python/redis
set "IDIR=%~dp0"

echo [停止] 后端服务...
powershell -NoProfile -Command "$d=$env:IDIR; Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.Path -and $_.Path.StartsWith($d) } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

echo [停止] Redis...
redis\redis-cli.exe -p 6379 shutdown nosave >nul 2>nul
powershell -NoProfile -Command "$d=$env:IDIR; Get-CimInstance Win32_Process -Filter \"Name='redis-server.exe'\" | Where-Object { $_.Path -and $_.Path.StartsWith($d) } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

echo 已停止。
endlocal
