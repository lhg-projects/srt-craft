#!/usr/bin/env bash
# ============================================================
# srt-craft 一键安装 + 启动 + 冒烟测试（macOS / Linux）
#
# 用法（任选其一，等价）：
#   bash <(curl -fsSL https://raw.githubusercontent.com/lhg-projects/srt-craft/main/install.sh)
#   git clone https://github.com/lhg-projects/srt-craft.git && cd srt-craft && bash install.sh
#
# 行为：检查 Python 3.10+ → 建 venv → 装依赖 → 后台启动服务 →
#       冒烟测试（首页 / 健康检查 / 版本接口）→ 全部通过才报成功。
#       可重复执行：已装好则跳过安装直接启动/检测。
# ============================================================
set -uo pipefail

REPO="https://github.com/lhg-projects/srt-craft.git"
PORT=8765
# 目录解析优先级：SRT_CRAFT_DIR 环境变量 > 当前目录（已是仓库）> 当前目录下的 srt-craft/
if [ -f ./app.py ]; then DIR="$PWD"; elif [ -n "${SRT_CRAFT_DIR:-}" ]; then DIR="$SRT_CRAFT_DIR"; else DIR="$PWD/srt-craft"; fi
LOG="$DIR/server.log"

say()  { printf '\033[1;36m[install]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m  ✅ %s\033[0m\n' "$*"; }
fail() { printf '\033[1;31m  ❌ %s\033[0m\n' "$*"; }
die()  { fail "$*"; fail "把上面的输出发给 AI 助手（或提 issue：https://github.com/lhg-projects/srt-craft/issues）即可获得帮助"; exit 1; }

# ---------- 0. 拿到代码：在仓库外执行则先 clone ----------
if [ ! -f "$DIR/app.py" ]; then
  say "克隆仓库到 $DIR …"
  cloned=""
  for i in 1 2 3; do
    git clone --depth 1 "$REPO" "$DIR" && cloned=1 && break
    echo "  第 $i 次克隆失败，重试…"; sleep 3
  done
  [ -n "$cloned" ] || die "git clone 连续 3 次失败（检查网络 / git 是否安装）"
fi
cd "$DIR" || die "进入目录失败：$DIR"

# ---------- 1. Python 3.10+ ----------
PY=""
for cand in python3.12 python3.11 python3.10 python3; do
  if command -v "$cand" >/dev/null 2>&1; then
    v=$("$cand" -c 'import sys; print("%d.%d" % (sys.version_info[0], sys.version_info[1]))' 2>/dev/null || true)
    major=$(echo "$v" | cut -d. -f1)
    minor=$(echo "$v" | cut -d. -f2)
    if [ "${major:-0}" -eq 3 ] && [ "${minor:-0}" -ge 10 ] 2>/dev/null; then PY="$cand"; break; fi
  fi
done
[ -n "$PY" ] || die "未找到 Python 3.10+。macOS: brew install python@3.12 ｜ Windows 请用 start_windows.bat"
ok "Python ${v:-?} ($PY)"

# ---------- 2. venv + 依赖（已装好则秒过） ----------
if [ ! -x .venv/bin/python ]; then
  say "创建虚拟环境 …"
  "$PY" -m venv .venv || die "venv 创建失败（macOS 需要系统装了 python3-venv 组件）"
fi
if ! .venv/bin/python -c "import flask, jieba, pypinyin, cryptography, litellm" >/dev/null 2>&1; then
  say "安装依赖（首次约 1-3 分钟，取决于网络）…"
  .venv/bin/python -m pip install -q --upgrade pip || die "pip 升级失败"
  .venv/bin/python -m pip install -q -r requirements.txt || die "依赖安装失败（多为网络问题，重跑本脚本即可重试）"
fi
ok "依赖完整"

# ffmpeg：可选能力，缺了只影响音频导入
if command -v ffmpeg >/dev/null 2>&1; then ok "ffmpeg 就绪（音频导入可用）"
else say "提示：未装 ffmpeg，音频导入功能不可用（brew install ffmpeg 可解锁；字幕校正不受影响）"; fi

# ---------- 3. 启动服务（已在跑则复用） ----------
probe() { curl -fsS -o /dev/null -m 3 "http://127.0.0.1:$PORT/api/health" 2>/dev/null; }
if probe; then
  ok "服务已在运行（端口 $PORT ），跳过启动"
else
  say "启动服务 …"
  nohup .venv/bin/python app.py > "$LOG" 2>&1 &
  # 冒烟前先等服务就绪，最多 15 秒
  for i in $(seq 1 30); do probe && break; sleep 0.5; done
fi

# ---------- 4. 冒烟测试：三个接口逐一验证 ----------
say "冒烟测试 …"
probe || { echo "---- server.log 末尾 ----"; tail -20 "$LOG" 2>/dev/null; die "服务未响应 http://127.0.0.1:$PORT "; }
ok "GET /api/health"

curl -fsS -o /dev/null -m 5 http://127.0.0.1:$PORT/ || die "首页不可访问"
ok "GET /（页面）"

VER=$(.venv/bin/python -c "import app; print(app.APP_VERSION)" 2>/dev/null)
[ -n "$VER" ] || die "版本号读取失败（app.py 损坏？重新 clone 后再试）"
curl -fsS -m 5 http://127.0.0.1:$PORT/api/health | grep -q "\"version\"" || die "/api/health 未返回版本字段"
curl -fsS -m 5 http://127.0.0.1:$PORT/static/version.json | grep -q "\"version\"" || die "version.json 缺失"
ok "版本 v$VER （页面徽章 + 更新提醒就绪）"

echo
printf '\033[1;32m✅ 安装完成，服务已就绪：\033[0m http://127.0.0.1:%s\n' "$PORT"
echo "   停止服务：  kill \$(lsof -ti :$PORT)     查看日志：  tail -f $LOG"
echo "   再次运行本脚本 = 重启并自检（已装好时秒级完成）"
