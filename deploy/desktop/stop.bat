@echo off
chcp 936 >nul
setlocal
cd /d "%~dp0"
REM 以安装目录（%~dp0，结尾带反斜杠）作为进程路径锚点，
REM 避免误杀其它位置的 python / redis 进程。
REM 本文件必须以 GBK 编码保存（无 BOM），原因见 start.bat 头部注释。
set "IDIR=%~dp0"

echo [停止] 正在停止后端服务 ...
powershell -NoProfile -Command "$d=$env:IDIR; Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($d) } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

echo [停止] 正在停止 Redis ...
redis\redis-cli.exe -p 6379 shutdown nosave >nul 2>nul
powershell -NoProfile -Command "$d=$env:IDIR; Get-CimInstance Win32_Process -Filter \"Name='redis-server.exe'\" | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($d) } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

echo [完成] 已停止。
endlocal
