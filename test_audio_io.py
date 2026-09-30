#!/usr/bin/env python3
"""audio_io 单元测试（裁剪逻辑 + 默认源配置）。
运行: services/jianying-tool/.venv/bin/python services/jianying-tool/test_audio_io.py -v
裁剪/时长用例依赖 ffmpeg；没有 ffmpeg 时自动跳过。"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audio_io  # noqa: E402


def _ffmpeg_available():
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _make_wav(path, sec=6):
    """生成 sec 秒的静音 wav（ffmpeg lavfi，无网络依赖）。"""
    import subprocess
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "anullsrc=r=16000:cl=mono", "-t", str(sec), path],
        check=True, capture_output=True)


class TestTrimAudio(unittest.TestCase):
    @unittest.skipUnless(_ffmpeg_available(), "需要 ffmpeg")
    def test_trim_head_tail(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "a.wav")
            _make_wav(src, 6)
            out, dur, keep = audio_io.trim_audio(src, 1.5, 1.0)
            self.assertAlmostEqual(dur, 6.0, delta=0.3)
            self.assertAlmostEqual(keep, 3.5, delta=0.3)

    @unittest.skipUnless(_ffmpeg_available(), "需要 ffmpeg")
    def test_trim_too_much_raises(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "a.wav")
            _make_wav(src, 4)
            with self.assertRaises(ValueError):
                audio_io.trim_audio(src, 3.0, 3.0)

    @unittest.skipUnless(_ffmpeg_available(), "需要 ffmpeg")
    def test_negative_clamped(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "a.wav")
            _make_wav(src, 4)
            out, dur, keep = audio_io.trim_audio(src, -5, 99)
            self.assertGreater(keep, 0)
            self.assertLessEqual(keep, 2.0)  # 尾部被钳到 dur/2


class TestDefaultSourceConfig(unittest.TestCase):
    """默认来源收口在 ai_subfix 的 config.json（单一配置源）。"""

    def _cfg(self):
        import ai_subfix
        return ai_subfix.load_config()

    def _save(self, cfg):
        import ai_subfix
        ai_subfix.save_config(cfg)

    def test_default_source_key_roundtrip(self):
        old = self._cfg().get("default_srt_source")
        try:
            audio_io.set_default_source("audio", self._cfg, self._save)
            self.assertEqual(self._cfg().get("default_srt_source"), "audio")
        finally:
            audio_io.set_default_source(old or "", self._cfg, self._save)

    def test_invalid_source_rejected(self):
        with self.assertRaises(ValueError):
            audio_io.set_default_source("bogus", self._cfg, self._save)

    def test_fallback_when_missing(self):
        cfg = {"ai_provider": "custom"}  # 没有 default_srt_source 键
        self.assertEqual(audio_io.effective_default_source(cfg), "draft")


if __name__ == "__main__":
    unittest.main(verbosity=2)
