# -*- coding: utf-8 -*-
"""
桌面单机版静默停止脚本（无黑框）。由 runtime\\pythonw.exe 加载。

只杀「可执行文件路径位于本安装目录内」的 python / pythonw / redis-server 进程，
绝不触碰安装目录之外的任何进程（开发机上共存的项目 Redis / 开发后端不受影响）。

占位符 __APP_NAME__ 由 pack_desktop.py 打包时替换为实际应用名。
环境变量 DESKTOP_STOP_QUIET=1 时不弹确认框（自动化测试用）。
"""
import ctypes
import os
import subprocess

APP_NAME = '__APP_NAME__'
ROOT = os.path.dirname(os.path.abspath(__file__))
CREATE_NO_WINDOW = 0x08000000
MB_ICONINFO = 0x40

# 按可执行文件路径前缀匹配（STOP_ROOT 以反斜杠结尾，防止 D:\app 误杀 D:\app2）
PS_KILL = (
    "$d=$env:STOP_ROOT;"
    "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe' OR Name='redis-server.exe'\" |"
    "Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($d) } |"
    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
)


def main():
    env = dict(os.environ, STOP_ROOT=ROOT + '\\')
    try:
        subprocess.run(['powershell', '-NoProfile', '-Command', PS_KILL],
                       env=env, capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=60)
    except Exception:
        pass
    if os.environ.get('DESKTOP_STOP_QUIET') != '1':
        try:
            ctypes.windll.user32.MessageBoxW(0, '已停止本目录的后端服务与内置 Redis。', APP_NAME, MB_ICONINFO)
        except Exception:
            pass


if __name__ == '__main__':
    main()
