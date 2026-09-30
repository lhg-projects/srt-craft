@echo off
chcp 65001 >nul
cd /d %~dp0
title 剪映工具箱

where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 未找到 Python。请安装 Python 3.10+，安装时勾选 "Add Python to PATH"。
  pause
  exit /b 1
)

if not exist .venv (
  echo [初始化] 创建虚拟环境并安装依赖（首次约 1-2 分钟）...
  python -m venv .venv
  .venv\Scripts\python -m pip install -q --upgrade pip
  .venv\Scripts\python -m pip install -q -r requirements.txt
  if errorlevel 1 (
    echo [错误] 依赖安装失败，请检查网络后重试。
    pause
    exit /b 1
  )
)

echo [启动] 剪映工具箱: http://127.0.0.1:8765  （Ctrl+C 停止）
.venv\Scripts\python app.py
pause
