"""SRT 字幕校正 - 本地 Web 服务（Flask，端口 8765）

只做一件事：SRT + 原始文稿 → 校正版 SRT。
支持三种校正引擎（拼音对齐 / 重建 / AI），时间轴原样保留，只改文本。
"""
import json
import os
import re
import sys
import time

from flask import Flask, jsonify, request, send_from_directory

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import draft  # noqa: E402
import srtfix  # noqa: E402

app = Flask(__name__, static_folder="static")

ARCHIVE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calibration_history")


@app.route("/")
def index():
    return send_from_directory("static", "srt.html")


@app.route("/srt.html")
def srt_page():
    return send_from_directory("static", "srt.html")


@app.route("/api/health")
def api_health():
    """草稿格式健康检查 + 剪映运行状态（供"从草稿生成 SRT"判断可用性）。"""
    try:
        return jsonify(draft.health_check())
    except Exception as e:
        return jsonify({"ok": False, "message": f"健康检查失败: {e}", "drafts": []})


@app.route("/api/drafts")
def api_drafts():
    return jsonify(draft.list_drafts())


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    """AI 校正的可选配置（经 litellm 接入多家供应商）。不配置则 AI 模式不可用。"""
    import ai_subfix
    if request.method == "POST":
        cfg = ai_subfix.load_config()
        body = request.get_json(force=True)
        for k in ("ai_provider", "ai_base_url", "ai_api_key", "ai_model"):
            if k in body:
                cfg[k] = body[k].strip()
        ai_subfix.save_config(cfg)
    cfg = ai_subfix.load_config()
    return jsonify({
        "ai_provider": cfg.get("ai_provider", ""),
        "ai_base_url": cfg.get("ai_base_url", ""),
        "ai_model": cfg.get("ai_model", ""),
        "ai_api_key_set": bool(cfg.get("ai_api_key")),
        "providers": [{"id": k, "label": v["label"], "base_url": v["base_url"],
                       "model": v["model"]} for k, v in ai_subfix.PROVIDERS.items()],
    })


# ---------- SRT 校正 ----------

@app.route("/api/srt/export")
def api_srt_export():
    """把本机剪映草稿的字幕导出为 SRT 文本。可选增强：未装剪映时不可用。"""
    name = request.args.get("draft", "")
    if not name:
        return jsonify({"ok": False, "error": "缺少 draft 参数"}), 400
    try:
        subs = draft.load_subtitles(name)
    except draft.DraftEncryptedError:
        return jsonify({"ok": False, "encrypted": True,
                        "error": "草稿已加密，无法读取字幕"}), 200
    except FileNotFoundError:
        return jsonify({"ok": False, "error": f"草稿不存在: {name}"}), 404
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    entries = [{"index": i + 1, "start_sec": s["start_sec"], "end_sec": s["end_sec"],
                "text": s["text"]} for i, s in enumerate(subs)]
    return jsonify({"ok": True, "name": name, "count": len(entries),
                    "srt": srtfix.build_srt(entries)})


@app.route("/api/srt/calibrate", methods=["POST"])
def api_srt_calibrate():
    """SRT + 原稿 → 修正版 SRT。body = {srt, script, phonetic/rebuild/ai}
    时间轴原样保留，只改文本；返回修改清单供核对。"""
    body = request.get_json(force=True)
    srt_text = body.get("srt", "")
    script = body.get("script", "")
    if not srt_text.strip():
        return jsonify({"ok": False, "error": "请先提供 SRT 字幕"}), 400
    if not script.strip() and not body.get("ai"):
        return jsonify({"ok": False,
                        "error": "没有原稿时请使用 AI 校对模式（⚙️ 先配置 AI 服务）；"
                                 "或补写文稿后用拼音对齐"}), 400
    try:
        srt_fixed, changes, applied, notes = srtfix.calibrate_srt(
            srt_text, script,
            phonetic=body.get("phonetic", True),
            ai=body.get("ai", False))
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500

    # AI 模式存档（AI vs 拼音双版本比对，用于反查拼音对齐缺陷）
    if body.get("ai"):
        try:
            _archive_calibration(body, script, srt_text, srt_fixed, changes, notes)
        except Exception:
            pass
    return jsonify({"ok": True, "changes": changes, "applied": applied,
                    "srt": srt_fixed, "notes": notes})


def _archive_calibration(body, script, srt_in, srt_out, changes, notes):
    """保存一次校正记录（草稿名/模式/条数/修改清单），便于回溯。"""
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    tag = re.sub(r'[\\/:*?"<>|\s]+', "_", body.get("name") or "paste")[:40]
    record = {"time": time.strftime("%Y-%m-%d %H:%M:%S"),
              "draft": body.get("name", ""), "mode": "ai",
              "changes": changes, "notes": notes}
    with open(os.path.join(ARCHIVE_DIR, f"{ts}_{tag}.json"), "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=1)


@app.route("/api/calibration_history")
def api_calibration_history():
    out = []
    if os.path.isdir(ARCHIVE_DIR):
        for f in sorted(os.listdir(ARCHIVE_DIR)):
            if not f.endswith(".json"):
                continue
            try:
                with open(os.path.join(ARCHIVE_DIR, f), encoding="utf-8") as fh:
                    rec = json.load(fh)
                out.append({"file": f, "summary": {
                    "time": rec.get("time", ""), "draft": rec.get("draft", ""),
                    "count": len(rec.get("changes", []))}})
            except Exception:
                continue
        out.reverse()
    return jsonify({"records": out})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8765"))
    print(f"SRT 字幕校正: http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, debug=False)
