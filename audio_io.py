"""音频导入：上传保存、头部/尾部裁剪（ffmpeg）、默认 SRT 来源配置。

从 srt-mix 的 audio.py 借鉴：裁剪设计（剪掉开头/结尾各 X 秒去除口误起头、
寒暄结尾）与默认参数持久化。差异：默认来源收口在 ai_subfix 的 config.json
（单一配置源，避免双 config.json 分叉），键名 default_srt_source，
丢失/非法时回退 "draft"，不写入。
"""
import json
import os
import subprocess

_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(_DIR, "uploads")

VALID_SOURCES = ("draft", "audio")
DEFAULT_SOURCE = "draft"


def audio_duration_sec(path):
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
        capture_output=True, text=True).stdout
    return float(json.loads(out or "{}").get("format", {}).get("duration", 0))


def save_upload(file_storage):
    """保存上传的原始音频 → uploads/<ts>_<rand>.<ext>，返回路径。"""
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    import time
    import uuid
    ext = os.path.splitext(file_storage.filename or "audio.wav")[1] or ".wav"
    path = os.path.join(UPLOAD_DIR, time.strftime("%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6] + ext)
    file_storage.save(path)
    return path


def trim_audio(src_path, head_sec=0.0, tail_sec=0.0):
    """剪掉头部 head_sec 秒和尾部 tail_sec 秒，输出 16k 单声道 wav（ASR 友好）。
    返回 (输出路径, 原时长, 修剪后时长)。"""
    dur = audio_duration_sec(src_path)
    head = max(0.0, min(float(head_sec or 0), dur / 2))
    tail = max(0.0, min(float(tail_sec or 0), dur / 2))
    keep = dur - head - tail
    if keep <= 0.1:
        raise ValueError(f"裁剪参数过大：原音频 {dur:.1f}s，头 {head}s + 尾 {tail}s 后没有剩余内容")
    out_path = os.path.splitext(src_path)[0] + ".trimmed.wav"
    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{head:.3f}", "-i", src_path,
           "-t", f"{keep:.3f}", "-ar", "16000", "-ac", "1", out_path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg 裁剪失败: {r.stderr[:200]}")
    return out_path, dur, audio_duration_sec(out_path)


def trim_audio_for_download(src_path, head_sec=0.0, tail_sec=0.0):
    """为「下载裁剪后音频」裁剪：保持原扩展名/声道/采样率（导入剪映与
    原视频对轨用），与 trim_audio（16k mono，喂 ASR）职责不同。
    返回 (输出路径, 原时长, 修剪后时长)。"""
    dur = audio_duration_sec(src_path)
    head = max(0.0, min(float(head_sec or 0), dur / 2))
    tail = max(0.0, min(float(tail_sec or 0), dur / 2))
    keep = dur - head - tail
    if keep <= 0.1:
        raise ValueError(f"裁剪参数过大：原音频 {dur:.1f}s，头 {head}s + 尾 {tail}s 后没有剩余内容")
    root, ext = os.path.splitext(src_path)
    if not ext:
        ext = ".wav"
    out_path = root + ".cut" + ext
    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{head:.3f}", "-i", src_path,
           "-t", f"{keep:.3f}", "-c:a", _audio_codec(ext), "-b:a", "192k", out_path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg 裁剪失败: {r.stderr[:200]}")
    return out_path, dur, audio_duration_sec(out_path)


def _audio_codec(ext):
    return {".mp3": "libmp3lame", ".m4a": "aac", ".aac": "aac",
            ".flac": "flac", ".ogg": "libvorbis"}.get(ext.lower(), "pcm_s16le")


def effective_default_source(cfg):
    """读默认来源：键丢失/非法（配置文件丢失同理）→ 回退 draft，不落盘。"""
    v = (cfg or {}).get("default_srt_source", DEFAULT_SOURCE)
    return v if v in VALID_SOURCES else DEFAULT_SOURCE


def set_default_source(source, load_config, save_config):
    """持久化默认来源到 config.json（经 ai_subfix 读写，保持单一配置源）。
    source 为空串 = 恢复默认（删除键）。"""
    if source and source not in VALID_SOURCES:
        raise ValueError(f"非法来源: {source}（可选 {VALID_SOURCES}）")
    cfg = load_config()
    if source in ("", DEFAULT_SOURCE):
        cfg.pop("default_srt_source", None)  # 默认值不落盘，等同"恢复默认"
    else:
        cfg["default_srt_source"] = source
    save_config(cfg)
