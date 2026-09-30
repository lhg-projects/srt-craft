"""macOS / Windows 双平台适配层：剪映路径、进程探测、窗口标题。

所有 OS 相关判断收口到这里，其余模块只调本模块接口。
Windows 适配说明（2026-09-30）：
- 草稿目录: %LOCALAPPDATA%\\JianyingPro\\User Data\\Projects\\com.lveditor.draft
- 主程序进程: JianyingPro.exe（macOS 为 VideoFusion-macOS.app）
- 窗口标题: PowerShell Get-Process（无需额外依赖）
- lsof 句柄探测仅 macOS 有；Windows 依赖心跳 + 窗口标题两路信号
- ffmpeg/ffprobe 需在 PATH（与 macOS 相同要求）
"""
import os
import sys
import subprocess

IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"


def jianying_draft_root():
    """剪映草稿根目录：返回第一个真实存在的候选路径（都不存在时返回默认，便于首跑）。"""
    home = os.path.expanduser("~")
    candidates = []
    if IS_WIN:
        local = os.environ.get("LOCALAPPDATA", os.path.join(home, "AppData", "Local"))
        candidates.append(os.path.join(local, "JianyingPro", "User Data",
                                       "Projects", "com.lveditor.draft"))
        candidates.append(os.path.join(home, "AppData", "Local", "JianyingPro",
                                       "User Data", "Projects", "com.lveditor.draft"))
    else:
        candidates.append(os.path.join(home, "Movies", "JianyingPro", "User Data",
                                       "Projects", "com.lveditor.draft"))
    for c in candidates:
        if os.path.isdir(c):
            return c
    return candidates[0]


DRAFT_ROOT = jianying_draft_root()

# 编辑器主程序进程名（Windows 任务管理器 / macOS 可执行名）
_EDITOR_WIN_EXE = "JianyingPro.exe"
_EDITOR_MAC_RE = r"VideoFusion-macOS\.app/Contents/MacOS/|JianyingPro\.app/Contents/MacOS/"


def _run(cmd, timeout=10):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None


def jianying_running():
    """剪映编辑器主程序是否在运行。托盘/后台组件（macOS TrayHelper、Windows
    后台服务进程）不算——它们不持草稿时间线数据。"""
    if IS_WIN:
        r = _run(["tasklist", "/FI", f"IMAGENAME eq {_EDITOR_WIN_EXE}", "/NH"])
        return bool(r and _EDITOR_WIN_EXE.lower() in (r.stdout or "").lower())
    r = _run(["pgrep", "-f", _EDITOR_MAC_RE])
    return bool(r and r.stdout.strip())


def editor_window_titles():
    """编辑器主窗口标题列表（用于探测"草稿是否正被编辑器打开"）。"""
    titles = []
    if IS_WIN:
        ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
              "(Get-Process JianyingPro -ErrorAction SilentlyContinue | "
              "ForEach-Object {$_.MainWindowTitle}) -join '|'")
        r = _run(["powershell", "-NoProfile", "-Command", ps], timeout=15)
        if r and r.stdout.strip():
            titles = [t for t in r.stdout.strip().split("|") if t]
    else:
        r = _run(["osascript", "-e",
                  'tell application "System Events" to get name of every window of '
                  'process "VideoFusion-macOS"'], timeout=10)
        if r and r.stdout.strip():
            titles = [t.strip() for t in r.stdout.strip().split(",") if t.strip()]
    return titles


def editor_holds_draft_files(name):
    """编辑器进程是否持有该草稿的时间线文件句柄（仅 macOS 可查；Windows 恒 False）。
    Windows 上剪映不保留可枚举句柄，探测依赖心跳与窗口标题两路信号。"""
    if IS_WIN:
        return False
    r = _run(["pgrep", "-f", "VideoFusion|JianyingPro"])
    danger = (f"com.lveditor.draft/{name}/draft_info.json",
              f"com.lveditor.draft/{name}/Timelines/")
    for pid in (r.stdout.split() if r else []):
        out = _run(["lsof", "-p", pid], timeout=5)
        text = out.stdout if out else ""
        for df in danger:
            if df in text:
                return True
    return False
