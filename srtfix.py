"""SRT 字幕文件：解析 / 生成 / 校正（复用 subfix 的对齐引擎）。

链路：剪映导出 SRT（或从草稿生成）→ 贴入原稿 → 校正 → 下载修正版 SRT → 导入剪映。
SRT 是剪映自己的导入格式，修正直接生效，完全绕开草稿写回与渲染缓存同步。
"""
import re

_TC = re.compile(
    r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})[,.](\d{1,3})\s*-->\s*"
    r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})[,.](\d{1,3})")


def _sec(g):
    """时间分组 → 秒；小时组可缺省（WebVTT 的 mm:ss.mmm 形态）。"""
    h = int(g[0] or 0)
    return h * 3600 + int(g[1]) * 60 + int(g[2]) + int(g[3]) / 1000


def parse_srt(text):
    """SRT 文本 → [{index, tc_line, start_sec, end_sec, text}]。
    容错 BOM / CRLF / 缺序号行；一条字幕的多行文本拼成一行（对齐用）。"""
    text = text.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    entries = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.split("\n")
        for i, ln in enumerate(lines):
            m = _TC.search(ln)
            if not m:
                continue
            g = m.groups()
            start = _sec(g[:4])
            end = _sec(g[4:])
            body_lines = lines[i + 1:]
            raw = "\n".join(body_lines)   # 逐字节保留（含行内/行尾空白），未修改条目 round-trip 不变
            body = " ".join(x.strip() for x in body_lines if x.strip())
            if body:
                entries.append({"index": len(entries) + 1,
                                "tc_line": m.group(0),
                                "start_sec": round(start, 3),
                                "end_sec": round(end, 3),
                                "text": body,
                                "raw": raw})
            break
    return entries


def _tc(sec):
    ms = max(0, int(round(sec * 1000)))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(entries):
    """[{index, tc_line?, start_sec, end_sec, text}] → SRT 文本。
    条目带 tc_line（原始时间轴行）则逐字节复用，保证时间码零漂移。"""
    out = []
    for i, e in enumerate(entries, 1):
        tc = e.get("tc_line") or f"{_tc(e['start_sec'])} --> {_tc(e['end_sec'])}"
        out.append(f"{i}\n{tc}\n{e['text']}")
    return "\n\n".join(out) + "\n"


def calibrate_srt(srt_text, script, phonetic=True, ai=False):
    """SRT + 原稿 → (修正版 SRT 文本, 修改清单, 应用条数, notes)。
    时间轴原样保留，只改文本；fix 按序号(1-based)落到对应条目。"""
    import subfix
    entries = parse_srt(srt_text)
    if not entries:
        raise ValueError("没有解析到任何字幕，请确认是标准 SRT 格式")
    subs = [{"segment_id": str(e["index"]), "text": e["text"],
             "start_sec": e["start_sec"], "end_sec": e["end_sec"]}
            for e in entries]
    clean = subfix.clean_script(script)
    notes = []
    if ai:
        import ai_subfix
        fixed, changes, notes = ai_subfix.ai_align(subs, clean)
    else:
        fixed, changes = subfix.align_phonetic(subs, clean)
    applied = 0
    for e in entries:
        new = fixed.get(str(e["index"]))
        if new and new != e["text"]:
            e["text"] = new
            applied += 1
        else:
            # 未修改的条目逐字节保留原始排版（多行字幕保持多行、尾随空白不丢）
            e["text"] = e.get("raw", e["text"])
    return build_srt(entries), changes, applied, notes
