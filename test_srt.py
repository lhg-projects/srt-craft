#!/usr/bin/env python3
"""srtfix 单元测试 + SRT API 端点测试（unittest，标准库无额外依赖）。
运行: services/jianying-tool/.venv/bin/python services/jianying-tool/test_srt.py -v"""
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import srtfix  # noqa: E402

SRT_OK = ("1\n00:00:00,500 --> 00:00:02,500\n他的事迹很感人\n\n"
          "2\n00:00:03,000 --> 00:00:04,000\n答案取决于镍价\n")
SCRIPT_OK = "他的事迹很感人。答案取决于镍价。"


class TestParseSrt(unittest.TestCase):
    def test_standard(self):
        es = srtfix.parse_srt(SRT_OK)
        self.assertEqual([e["text"] for e in es], ["他的事迹很感人", "答案取决于镍价"])

    def test_bom_crlf(self):
        es = srtfix.parse_srt("\ufeff" + SRT_OK.replace("\n", "\r\n"))
        self.assertEqual(len(es), 2)

    def test_missing_index_line(self):
        es = srtfix.parse_srt("00:00:00,500 --> 00:00:02,500\n你好\n\n00:00:03,000 --> 00:00:04,000\n世界\n")
        self.assertEqual([e["text"] for e in es], ["你好", "世界"])
        self.assertEqual([e["index"] for e in es], [1, 2])

    def test_out_of_order_reindexed(self):
        es = srtfix.parse_srt("5\n00:00:00,500 --> 00:00:02,500\n你好\n\n9\n00:00:03,000 --> 00:00:04,000\n世界\n")
        self.assertEqual([e["index"] for e in es], [1, 2])

    def test_empty_text_dropped(self):
        self.assertEqual(srtfix.parse_srt("1\n00:00:00,500 --> 00:00:02,500\n\n"), [])

    def test_multiline_joined(self):
        es = srtfix.parse_srt("1\n00:00:00,500 --> 00:00:02,500\n第一行\n第二行\n")
        self.assertEqual(es[0]["text"], "第一行 第二行")

    def test_vtt_tolerated(self):
        """WebVTT（WEBVTT 头 + 点毫秒 + 可省小时）应能解析（2026-09-30 迭代加入）。"""
        vtt = ("WEBVTT\n\n00:00.500 --> 00:02.500\n你好\n\n"
               "00:00:03.000 --> 00:00:04.000\n世界\n")
        es = srtfix.parse_srt(vtt)
        self.assertEqual([e["text"] for e in es], ["你好", "世界"])
        self.assertAlmostEqual(es[0]["start_sec"], 0.5, places=2)
        self.assertAlmostEqual(es[1]["start_sec"], 3.0, places=2)


class TestCalibrateSrt(unittest.TestCase):
    # 台账 2026-09-30：重建模式按用户指示删除，覆盖其场景的重建用例一并移除（异音/多字用 AI 模式）

    def test_homophone_fixed(self):
        srt_typo = SRT_OK.replace("事迹", "事绩")
        out, changes, applied, _ = srtfix.calibrate_srt(srt_typo, SCRIPT_OK, phonetic=True)
        self.assertEqual(applied, 1)
        self.assertIn("他的事迹很感人", out)

    def test_timecodes_unchanged(self):
        out, *_ = srtfix.calibrate_srt(SRT_OK.replace("事迹", "事绩"), SCRIPT_OK, phonetic=True)
        self.assertEqual([ln for ln in out.split("\n") if "-->" in ln],
                         [ln for ln in SRT_OK.split("\n") if "-->" in ln])

    def test_own_text_idempotent(self):
        entries = srtfix.parse_srt(SRT_OK)
        own = "".join(e["text"] for e in entries)
        out, changes, applied, _ = srtfix.calibrate_srt(SRT_OK, own, phonetic=True)
        self.assertEqual(applied, 0)
        real = [c for c in changes if c["old"].strip() != c["new"].strip()]
        self.assertEqual(real, [])

    def test_empty_srt_raises(self):
        with self.assertRaises(ValueError):
            srtfix.calibrate_srt("这不是srt", "x")

    def test_ai_mode(self):
        srt_typo = SRT_OK.replace("事迹", "事绩").replace("镍价", "虐价")
        out, changes, applied, notes = srtfix.calibrate_srt(srt_typo, SCRIPT_OK, ai=True)
        self.assertEqual(applied, 2)
        self.assertIn("事迹", out)
        self.assertIn("镍价", out)


class TestSrtApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import app
        app.config["TESTING"] = True
        cls.client = app.test_client()

    def test_export_nonexistent_404(self):
        """不存在的草稿应返回 404 + 明确中文提示（迭代前是 500）。"""
        r = self.client.get("/api/srt/export?draft=" +
                            __import__("urllib.parse", fromlist=["quote"]).quote("不存在的草稿zz"))
        self.assertEqual(r.status_code, 404)
        self.assertFalse(r.get_json()["ok"])
        self.assertIn("不存在", r.get_json()["error"])

    def test_calibrate_empty_script_400(self):
        r = self.client.post("/api/srt/calibrate", json={"srt": SRT_OK, "script": ""})
        self.assertEqual(r.status_code, 400)

    def test_calibrate_bad_srt_400(self):
        r = self.client.post("/api/srt/calibrate", json={"srt": "这不是srt", "script": "x"})
        self.assertEqual(r.status_code, 400)

    def test_export_real_draft(self):
        import draft as draft_mod
        names = [d["name"] for d in draft_mod.list_drafts()]
        target = None
        for n in names:
            try:
                if draft_mod.load_subtitles(n):
                    target = n
                    break
            except Exception:
                continue
        if not target:
            self.skipTest("无可读草稿")
        r = self.client.get("/api/srt/export?draft=" +
                            __import__("urllib.parse", fromlist=["quote"]).quote(target))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
