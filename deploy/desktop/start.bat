@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
chcp 65001 >nul

set "PORT=__APP_PORT__"
set "REDIS_PORT=6379"

if not exist logs mkdir logs
if not exist data mkdir data

echo ============================================
echo   CesiFastAdmin 桌面版启动
echo ============================================

REM ── 1. 启动 Redis（幂等：已在跑则跳过）──
redis\redis-cli.exe -p %REDIS_PORT% ping 2>nul | findstr /i "PONG" >nul
if %errorlevel%==0 (
  echo [跳过] Redis 已在运行
) else (
  echo [启动] Redis ...
  start "CesiFastAdmin-Redis" /min redis\redis-server.exe redis\redis.windows.conf
  set /a n=0
  :redis_wait
  timeout /t 1 /nobreak >nul
  redis\redis-cli.exe -p %REDIS_PORT% ping 2>nul | findstr /i "PONG" >nul
  if %errorlevel%==0 goto redis_ok
  set /a n+=1
  if !n! lss 15 goto redis_wait
  echo [警告] Redis 启动超时，仍尝试启动后端
  :redis_ok
)

REM ── 2. 启动后端 ──
echo [启动] 后端服务 http://localhost:%PORT% ...
start "CesiFastAdmin-Backend" /min cmd /c "runtime\python.exe run.py >> logs\backend.log 2>&1"

REM ── 3. 等待就绪并打开浏览器 ──
echo [等待] 服务就绪（最多约 120 秒）...
set /a n=0
:app_wait
timeout /t 1 /nobreak >nul
powershell -NoProfile -Command "try { (Invoke-WebRequest -UseBasicParsing -Uri http://localhost:%PORT%/ -TimeoutSec 2).StatusCode } catch { 0 }" 2>nul | findstr "200" >nul
if %errorlevel%==0 goto app_ok
set /a n+=1
if !n! lss 120 goto app_wait
echo [错误] 服务启动超时，请查看 logs\backend.log
pause
goto end
:app_ok

echo [完成] 正在打开浏览器 http://localhost:%PORT%
start "" http://localhost:%PORT%

:end
endlocal
