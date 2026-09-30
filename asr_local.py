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
    parts = []
    for i, seg in enumerate(segments, 1):
        parts.append(f"{i}\n{_ts(seg.start)} --> {_ts(seg.end)}\n{seg.text.strip()}")
    srt_text = "\n\n".join(parts) + ("\n" if parts else "")
    return srt_text, info.language, info.duration
