"""ASR：faster-whisper 把音频转成带时间轴的粗稿 SRT。

从 srt-mix 的 asr.py 移植（模型懒加载 + 全局复用）。输出 SRT 交给
前端填入 ① SRT 字幕框，后续校正链路（拼音对齐 / AI）不变。
首次运行会下载 whisper 模型（默认 small），耗时取决于网络。
"""
import os

_MODEL = None


def _get_model(cfg):
    global _MODEL
    if _MODEL is None:
        from faster_whisper import WhisperModel
        model_size = cfg.get("whisper_model", "small")
        _MODEL = WhisperModel(model_size_or_path=model_size,
                              device=cfg.get("asr_device", "cpu"),
                              compute_type=cfg.get("asr_compute", "int8"))
    return _MODEL


def _ts(sec):
    ms = max(0, int(round(sec * 1000)))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


_DEFAULT_MAX_CHARS = 16  # 每条字幕上限；config.json: asr_max_chars 可调
DEFAULT_MAX_CHARS = _DEFAULT_MAX_CHARS
_STRIP_RE = None
_DOT_RE = None


def strip_punct(text):
    """去标点：字幕更美观（时间轴靠字符比例内插，与断句解耦）。
    两步走：先去标点类（不含点号），再去非法位置的点——
    数字间的小数点（0.2%）保留，句尾/词界的点去掉。"""
    global _STRIP_RE, _DOT_RE
    if _STRIP_RE is None:
        import re
        _STRIP_RE = re.compile(
            r"[。！？；，、：""''""…—·\-\[\]\(\)（）【】《》<>\"'!?,;:~～`]+")
        _DOT_RE = re.compile(r"(?<!\d)\.(?!\d)|\.(?=\s|$)")
    return _DOT_RE.sub("", _STRIP_RE.sub("", text)).strip()
    return _STRIP_RE.sub("", text).strip()


def split_segment(text, start, end, max_chars=DEFAULT_MAX_CHARS):
    """把 whisper 的一个 VAD 长段切成一条条分句。
    规则：遇到标点就切（。！？；，、：等，每个分句一条），
    没有优先级合并；无标点且超过 max_chars 的子句按字数硬切兜底。
    返回 [(文本, 起秒, 止秒), ...]；时间按字符数线性内插。"""
    import re
    pieces = []
    for p in re.split(r"[。！？；，、：,;:…]+", text.strip()):
        p = p.strip()
        if not p:
            continue
        while len(p) > max_chars:  # 无标点超长兜底
            pieces.append(p[:max_chars])
            p = p[max_chars:]
        if p:
            pieces.append(p)
    if not pieces:
        return []
    # 字符数比例 → 时间内插
    total = sum(len(p) for p in pieces)
    out, t = [], start
    for p in pieces:
        dur = (end - start) * len(p) / total
        out.append((p, t, t + dur))
        t += dur
    return out


def info_duration(wav_path):
    """音频时长（秒）：转写响应里展示用，避免把 whisper 再跑一遍。"""
    import json as _json
    import subprocess as _sp
    out = _sp.run(["ffprobe", "-v", "quiet", "-print_format", "json",
                   "-show_format", wav_path], capture_output=True, text=True).stdout
    return float(_json.loads(out or "{}").get("format", {}).get("duration", 0))


def build_srt_from_segments(segments, max_chars=DEFAULT_MAX_CHARS):
    """whisper segments（(文本, 起, 止) 迭代器）→ 断句 + 去标点 → SRT 文本。
    返回 (srt 文本, 条数)。"""
    parts = []
    i = 0
    for text, seg_start, seg_end in segments:
        # 长段按标点断句（whisper VAD 一口气给 25s+ 的段，导入剪映糊满全屏），
        # 切完去掉标点（字幕更美观，断句语义边界不受影响）
        for t, s, e in split_segment(text.strip(), seg_start, seg_end, max_chars):
            t = strip_punct(t)
            if not t:
                continue
            i += 1
            parts.append(f"{i}\n{_ts(s)} --> {_ts(e)}\n{t}")
    return "\n\n".join(parts) + ("\n" if parts else ""), i


def transcribe_to_srt(wav_path, cfg=None, initial_prompt=None):
    """音频 → 粗稿 SRT。返回 (srt 文本, 语言, 总时长)。
    initial_prompt 缺省给金融口播热词（本工具的目标场景），显著减少
    "市盈率→适应率""三季报→3计报"这类领域词错字。"""
    cfg = cfg or {}
    model = _get_model(cfg)
    _default_prompt = ("以下是普通话财经口播内容，包含金融术语：市盈率、业绩预增、"
                       "三季报、季报、市值、营收、同比增长。")
    segments, info = model.transcribe(
        wav_path, beam_size=5,
        initial_prompt=initial_prompt or _default_prompt,
        vad_filter=True)
    max_chars = int(cfg.get("asr_max_chars", DEFAULT_MAX_CHARS) or DEFAULT_MAX_CHARS)
    srt_text, _count = build_srt_from_segments(
        ((seg.text, seg.start, seg.end) for seg in segments), max_chars=max_chars)
    return srt_text, info.language, info.duration
