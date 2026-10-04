"""SRT 字幕校正 - 本地 Web 服务（Flask，端口 8765）

只做一件事：SRT + 原始文稿 → 校正版 SRT。
支持三种校正引擎（拼音对齐 / 重建 / AI），时间轴原样保留，只改文本。
"""
import json
import os
import re
import sys
import time
from urllib.parse import quote

from flask import Flask, jsonify, request, send_from_directory, send_file

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import draft  # noqa: E402
import srtfix  # noqa: E402
import audio_io  # noqa: E402

app = Flask(__name__, static_folder="static")

# 版本号来源：前端徽章经 /api/health 同步显示。发新版改这里 + static/version.json
# （version.json 是 GitHub 上用户本地版本的比对基准，两处必须一起改，README 更新日志同步）
APP_VERSION = "1.3.0"

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
    """上传音频 → 裁剪（头/尾秒数）→ 变速（默认 1.1x，atempo 不变调）
    → 处理链完成才返回，供后续转写/下载。body = multipart {audio, head_sec, tail_sec, speed}"""
    f = request.files.get("audio")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "没有音频文件"}), 400
    try:
        path = audio_io.save_upload(f)
        raw_dur = audio_io.audio_duration_sec(path)
        trimmed, dur, keep = audio_io.trim_audio(
            path, request.form.get("head_sec", 0), request.form.get("tail_sec", 0))
        speed = request.form.get("speed") or audio_io.DEFAULT_SPEED
        sped, sped_dur = audio_io.change_speed(trimmed, speed)
        if sped != trimmed:
            os.remove(trimmed)  # 中间产物不保留，下载/转写统一用变速后的
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except RuntimeError as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
    # 原始文件名（去扩展名）→ 下载命名用，保持与上传一致
    orig_name = os.path.splitext(os.path.basename(f.filename or "audio"))[0]
    return jsonify({"ok": True, "audio_path": sped,
                    "original_name": orig_name,
                    "duration_sec": round(raw_dur, 1),
                    "kept_sec": round(keep, 1),
                    "speed": float(speed),
                    "speed_dur_sec": round(sped_dur, 1)})


@app.route("/api/audio/transcribe", methods=["POST"])
def api_audio_transcribe():
    """处理链完成的音频（裁剪+变速）→ whisper 粗稿 SRT。
    body = {audio_path}。转写的时间轴从 0 开始（已对齐变速后音频）。"""
    import ai_subfix
    import asr_local
    body = request.get_json(force=True)
    path = body.get("audio_path", "")
    if not path or not os.path.isfile(path):
        return jsonify({"ok": False, "error": "音频不存在，请先上传"}), 400
    try:
        srt_text, lang, _ = asr_local.transcribe_to_srt(path, cfg=ai_subfix.load_config())
    except ImportError:
        return jsonify({"ok": False, "error": "未安装 faster-whisper：.venv/bin/pip install faster-whisper",
                        "need_asr_install": True}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"转写失败: {type(e).__name__}: {e}"}), 500
    return jsonify({"ok": True, "srt": srt_text, "count": srt_text.count("-->"),
                    "language": lang,
                    "kept_sec": round(asr_local.info_duration(path), 1)})


@app.route("/api/srt/default_source", methods=["GET", "POST", "DELETE"])
def api_srt_default_source():
    """① 区块的用户默认（来源 + 裁剪秒数），持久化在 config.json。
    GET 返回生效默认（丢失/非法 → draft/0）；POST {source, head_sec, tail_sec}
    记住选择；DELETE = 恢复默认（删键，等同配置丢失）。"""
    import ai_subfix
    if request.method == "POST":
        body = request.get_json(force=True) or {}
        source = body.get("source", "")
        if source:
            try:
                audio_io.set_default_source(source, ai_subfix.load_config, ai_subfix.save_config)
            except ValueError as e:
                return jsonify({"ok": False, "error": str(e)}), 400
        # 裁剪秒数：用户设定后一直保持（刷新不丢），恢复默认/配置丢失 → 0
        # 请求键是短名 head_sec/tail_sec，落盘键是 default_clip_*（与 srt-mix 通用）
        for req_key, key in (("head_sec", "default_clip_head_sec"),
                             ("tail_sec", "default_clip_tail_sec")):
            if req_key in body:
                try:
                    val = max(0.0, float(body[req_key] or 0))
                except (TypeError, ValueError):
                    val = 0.0
                cfg = ai_subfix.load_config()
                if val > 0:
                    cfg[key] = val
                else:
                    cfg.pop(key, None)  # 0 = 默认值不落盘
                ai_subfix.save_config(cfg)
    elif request.method == "DELETE":
        import ai_subfix as _a
        audio_io.set_default_source("", _a.load_config, _a.save_config)
        cfg = _a.load_config()
        cfg.pop("default_clip_head_sec", None)
        cfg.pop("default_clip_tail_sec", None)
        _a.save_config(cfg)
    cfg = ai_subfix.load_config()
    return jsonify({"ok": True,
                    "default_source": audio_io.effective_default_source(cfg),
                    "head_sec": cfg.get("default_clip_head_sec", 0),
                    "tail_sec": cfg.get("default_clip_tail_sec", 0),
                    "persisted": "default_srt_source" in cfg})


@app.route("/api/audio/download_cut", methods=["POST"])
def api_audio_download_cut():
    """下载处理链完成的音频（prepare 已裁剪+变速），body = {audio_path, original_name?}。
    与 SRT 同源 → 时间轴对齐，SRT 和音频可一起导入剪映。
    文件名用上传时的原名 + 处理标记，保持一致可认。"""
    body = request.get_json(force=True)
    path = body.get("audio_path", "")
    if not path or not os.path.isfile(path):
        return jsonify({"ok": False, "error": "音频不存在，请先上传"}), 400
    ext = os.path.splitext(path)[1] or ".wav"
    speed = re.search(r"\.x(\d+(?:\.\d+)?)(?=\.\w+$|$)", os.path.basename(path))
    label = f"_处理{speed.group(1)}x" if speed else "_处理"
    # 原始文件名优先（前端回传），否则从服务端路径尽量还原
    orig = (body.get("original_name") or "").strip()
    if not orig:
        basename = os.path.splitext(os.path.basename(path))[0]
        orig = re.sub(r"\.\d{4}_\d{6}_[0-9a-f]{6}$", "", basename)  # 去服务端时间戳
        orig = re.sub(r"\.(trimmed|x[\d.]+)", "", orig) or "audio"
    orig = re.sub(r'[\\/:*?"<>|\s]+', "_", orig)[:80] or "audio"
    return jsonify({"ok": True, "download_url": f"/api/audio/file?path={quote(path)}",
                    "filename": f"{orig}{label}{ext}",
                    "kept_sec": round(audio_io.audio_duration_sec(path), 1)})


@app.route("/api/audio/download_all", methods=["POST"])
def api_audio_download_all():
    """一键打包下载：修正版 SRT + 处理后音频 → zip。
    body = {audio_path, srt, name, original_name?}；
    zip 内容 <音频原名>_处理<倍率>x.<ext> + <名>_修正.srt。"""
    import io
    import zipfile
    body = request.get_json(force=True)
    path = body.get("audio_path", "")
    srt_text = body.get("srt", "")
    if not srt_text.strip():
        return jsonify({"ok": False, "error": "还没有修正版 SRT，请先完成校正"}), 400
    if not path or not os.path.isfile(path):
        return jsonify({"ok": False, "error": "音频不存在，请先上传"}), 400
    name = re.sub(r'[\\/:*?"<>|\s]+', "_", body.get("name") or "subtitles")[:60] or "subtitles"
    ext = os.path.splitext(path)[1] or ".wav"
    speed = re.search(r"\.x(\d+(?:\.\d+)?)(?=\.\w+$|$)", os.path.basename(path))
    label = f"_处理{speed.group(1)}x" if speed else "_处理"
    # 音频沿用上传时的原名（前端回传），SRT 用草稿/默认名
    orig = (body.get("original_name") or "").strip()
    if not orig:
        basename = os.path.splitext(os.path.basename(path))[0]
        orig = re.sub(r"\.\d{4}_\d{6}_[0-9a-f]{6}$", "", basename)
        orig = re.sub(r"\.(trimmed|x[\d.]+)", "", orig) or "audio"
    orig = re.sub(r'[\\/:*?"<>|\s]+', "_", orig)[:80] or "audio"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{name}_修正.srt", srt_text)
        z.write(path, f"{orig}{label}{ext}")
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f"{name}_字幕音频打包.zip",
                     mimetype="application/zip")


@app.route("/api/jianying/import", methods=["POST"])
def api_jianying_import():
    """一键导入剪映：用修正版 SRT（+可选处理后音频）在剪映草稿库新建草稿。
    body = {srt, audio_path?}。总是新建草稿，绝不写回现有草稿。"""
    import draft as draft_mod
    body = request.get_json(force=True)
    srt_text = body.get("srt", "")
    if not srt_text.strip():
        return jsonify({"ok": False, "error": "还没有修正版 SRT，请先完成校正"}), 400
    audio_path = body.get("audio_path") or None
    if audio_path and not os.path.isfile(audio_path):
        audio_path = None  # 音频缺失不阻塞：只导字幕轨
    r = draft_mod.import_jianying_draft(srt_text, audio_path=audio_path)
    return jsonify(r), (200 if r.get("ok") else 500)


@app.route("/api/jianying/status", methods=["GET"])
def api_jianying_status():
    """剪映导入前置状态：剪映安装/运行、草稿目录可写、pyJianYingDraft 依赖。"""
    import importlib.util
    import draft as draft_mod
    from platforms import jianying_running
    return jsonify({
        "ok": True,
        "jianying_running": jianying_running(),
        "draft_root_exists": os.path.isdir(draft_mod.DRAFT_ROOT),
        "draft_root": str(draft_mod.DRAFT_ROOT),
        "pyjyd_installed": importlib.util.find_spec("pyJianYingDraft") is not None,
    })


@app.route("/api/audio/file")
def api_audio_file():
    """下载服务端生成的音频文件（仅限 uploads/ 目录，防路径穿越）。"""
    from urllib.parse import unquote
    path = unquote(request.args.get("path", ""))
    updir = os.path.abspath(audio_io.UPLOAD_DIR)
    if not os.path.abspath(path).startswith(updir + os.sep) or not os.path.isfile(path):
        return jsonify({"ok": False, "error": "文件不存在"}), 404
    return send_file(path, as_attachment=True)


@app.route("/api/health")
def api_health():
    """草稿格式健康检查 + 剪映运行状态（供"从草稿生成 SRT"判断可用性）。"""
    try:
        info = draft.health_check()
        info["version"] = APP_VERSION
        return jsonify(info)
    except Exception as e:
        return jsonify({"ok": False, "message": f"健康检查失败: {e}", "drafts": [], "version": APP_VERSION})


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
