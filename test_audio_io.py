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


class TestTrimForDownload(unittest.TestCase):
    """④ 区「下载裁剪后音频」：格式跟随原文件（重编码保证切点精确）。"""

    @unittest.skipUnless(_ffmpeg_available(), "需要 ffmpeg")
    def test_cut_wav_duration(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "a.wav")
            _make_wav(src, 6)
            out, dur, keep = audio_io.trim_audio_for_download(src, 1.0, 0.5)
            self.assertTrue(out.endswith(".wav"))
            self.assertAlmostEqual(keep, 4.5, delta=0.3)

    @unittest.skipUnless(_ffmpeg_available(), "需要 ffmpeg")
    def test_cut_too_much_raises(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "a.wav")
            _make_wav(src, 4)
            with self.assertRaises(ValueError):
                audio_io.trim_audio_for_download(src, 3.5, 3.5)

    @unittest.skipUnless(_ffmpeg_available(), "需要 ffmpeg")
    def test_cut_keeps_extension(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "a.wav")
            _make_wav(src, 4)
            for target_ext in (".mp3", ".m4a"):
                dst = os.path.join(d, "b" + target_ext)
                shutil.copy(src, dst)
                out, _, _ = audio_io.trim_audio_for_download(dst, 1, 0)
                self.assertTrue(out.endswith(target_ext), f"{target_ext} 应保持原扩展名")


class TestSplitSegment(unittest.TestCase):
    """whisper 长段按标点断句：字幕条过长导入剪映会糊满全屏。"""

    def test_split_long_by_punct(self):
        from asr_local import split_segment
        # 60 字长段，含句号/逗号 → 应切成多条 ≤25 字
        text = ("晚上八点半，一份数据让整个紧缩预期松了扣。美国8月核心PCE环比0.2%，"
                "预期是0.3%；同比3.0%，预期3.3%，前值同样是3.3%。")
        parts = split_segment(text, start=0.0, end=25.0, max_chars=25)
        self.assertGreater(len(parts), 1)
        for t, s, e in parts:
            self.assertLessEqual(len(t), 25, f"超长: {t}")
            self.assertGreater(len(t), 0)
        # 时间单调递增且覆盖全段
        self.assertAlmostEqual(parts[0][1], 0.0, places=2)
        self.assertAlmostEqual(parts[-1][2], 25.0, places=2)
        for a, b in zip(parts, parts[1:]):
            self.assertLessEqual(a[2], b[1] + 1e-6)

    def test_short_untouched(self):
        from asr_local import split_segment
        parts = split_segment("大家好，晚上八点半。", start=0.0, end=4.0, max_chars=25)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0][0], "大家好，晚上八点半。")

    def test_no_punct_hard_split(self):
        from asr_local import split_segment
        text = "这一段完全没有标点符号就是一口气念下来的非常长的内容需要强制切分"
        parts = split_segment(text, start=0.0, end=10.0, max_chars=20)
        self.assertGreater(len(parts), 1)
        for t, s, e in parts:
            self.assertLessEqual(len(t), 20)


class TestSpeedChange(unittest.TestCase):
    """音频变速（默认 1.1x）：atempo 变速不变调，裁剪后、转写前执行。"""

    @unittest.skipUnless(_ffmpeg_available(), "需要 ffmpeg")
    def test_speed_up_shortens_duration(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "a.wav")
            _make_wav(src, 10)
            out, dur = audio_io.change_speed(src, 1.1)
            new_dur = audio_io.audio_duration_sec(out)
            self.assertAlmostEqual(new_dur, 10 / 1.1, delta=0.3)
            self.assertTrue(out.endswith(".wav"))

    @unittest.skipUnless(_ffmpeg_available(), "需要 ffmpeg")
    def test_speed_one_returns_original(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "a.wav")
            _make_wav(src, 4)
            out, dur = audio_io.change_speed(src, 1.0)
            self.assertEqual(out, src)  # 1.0 不处理，原样返回

    def test_speed_bounds(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "a.wav")
            _make_wav(src, 4)
            with self.assertRaises(ValueError):
                audio_io.change_speed(src, 0.4)  # 低于 0.5 拒绝
            with self.assertRaises(ValueError):
                audio_io.change_speed(src, 3.0)  # 高于 2.0 拒绝


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
