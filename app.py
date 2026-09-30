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
import audio_io  # noqa: E402

app = Flask(__name__, static_folder="static")

ARCHIVE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calibration_history")


@app.route("/")
def index():
    return send_from_directory("static", "srt.html")


@app.route("/srt.html")
def srt_page():
    return send_from_directory("static", "srt.html")


@app.route("/favicon.ico")
def favicon_ico():
    """浏览器默认请求 /favicon.ico；没有它标签页图标时有时无（日志刷 404）。"""
    return send_from_directory("static", "favicon.svg", mimetype="image/svg+xml")


# ---------- 音频导入生成 SRT（借鉴 srt-mix：裁剪 + whisper 转写） ----------

@app.route("/api/audio/prepare", methods=["POST"])
def api_audio_prepare():
    """上传音频 → 保存原文件 → 返回时长。body = multipart {audio}。
    裁剪不在这一步做：头/尾秒数以点击「生成 SRT」时的输入为准。"""
    f = request.files.get("audio")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "没有音频文件"}), 400
    try:
        path = audio_io.save_upload(f)
        dur = audio_io.audio_duration_sec(path)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
    return jsonify({"ok": True, "audio_path": path, "duration_sec": round(dur, 1)})


@app.route("/api/audio/transcribe", methods=["POST"])
def api_audio_transcribe():
    """按当前头/尾秒数裁剪 → whisper 粗稿 SRT。
    body = {audio_path, head_sec, tail_sec}。裁剪在本步执行，转写的
    时间轴从裁剪后音频的 0 开始（填入 ① 即是剪好的稿）。"""
    import ai_subfix
    import asr_local
    body = request.get_json(force=True)
    path = body.get("audio_path", "")
    if not path or not os.path.isfile(path):
        return jsonify({"ok": False, "error": "音频不存在，请先上传"}), 400
    try:
        trimmed, dur, keep = audio_io.trim_audio(
            path, body.get("head_sec", 0), body.get("tail_sec", 0))
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except RuntimeError as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    try:
        srt_text, lang, _ = asr_local.transcribe_to_srt(trimmed, cfg=ai_subfix.load_config())
    except ImportError:
        return jsonify({"ok": False, "error": "未安装 faster-whisper：.venv/bin/pip install faster-whisper",
                        "need_asr_install": True}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"转写失败: {type(e).__name__}: {e}"}), 500
    return jsonify({"ok": True, "srt": srt_text, "count": srt_text.count("-->"),
                    "language": lang, "original_sec": round(dur, 1),
                    "kept_sec": round(keep, 1)})


@app.route("/api/srt/default_source", methods=["GET", "POST", "DELETE"])
def api_srt_default_source():
    """① 区块输入来源的用户默认（draft/audio），持久化在 config.json。
    GET 返回生效默认（丢失/非法 → draft）；POST {source} 记住选择；
    DELETE = 恢复默认（删键，等同配置丢失）。"""
    import ai_subfix
    if request.method == "POST":
        source = (request.get_json(force=True) or {}).get("source", "")
        try:
            audio_io.set_default_source(source, ai_subfix.load_config, ai_subfix.save_config)
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
    elif request.method == "DELETE":
        audio_io.set_default_source("", ai_subfix.load_config, ai_subfix.save_config)
    cfg = ai_subfix.load_config()
    return jsonify({"ok": True, "default_source": audio_io.effective_default_source(cfg),
                    "persisted": "default_srt_source" in cfg})


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
                       "model": v["model"],
                       "tips": ai_subfix.PROVIDER_TIPS.get(k, "")}
                      for k, v in ai_subfix.PROVIDERS.items()],
    })


@app.route("/api/ai/test", methods=["POST"])
def api_ai_test():
    """测试模型连接：用**已保存**的配置发一次最小请求（"Reply with exactly: OK"），
    返回 (成功, 错误信息, 耗时秒)。前端流程 = 先保存再测试，保证测的就是表单里填的。"""
    import ai_subfix
    ok, err, elapsed = ai_subfix.test_connection()
    return jsonify({"ok": ok, "error": err, "elapsed": round(elapsed, 2)})


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
    if not script.strip():
        # 判定依据：无原稿 = 无对齐参照系，拼音对齐不成立，必须走 AI 语义校对。
        # 前端会自动勾选 AI（UI 强制），这里服务端再强制一次（双保险，防绕过）。
        body["ai"] = True
        body["phonetic"] = False
    try:
        srt_fixed, changes, applied, notes = srtfix.calibrate_srt(
            srt_text, script,
            phonetic=body.get("phonetic", True),
            ai=body.get("ai", False))
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except RuntimeError as e:
        # AI 未配置（无原稿模式下无法工作）→ 400 + 引导配置，而非 500
        return jsonify({"ok": False, "error": str(e), "need_ai_config": True}), 400
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
