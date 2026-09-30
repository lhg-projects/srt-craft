"""剪映草稿读取：SRT 导出用（解析加密草稿、提取字幕文本与时间轴）。

只读。支持剪映 6+ 加密草稿的本地解密（AES-GCM，密钥/IV 内嵌于密文固定偏移，
解密完全在本机完成，不联网）。
"""
import base64
import binascii
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from platforms import DRAFT_ROOT, jianying_running  # noqa: E402

# 剪映 6+ 加密草稿：key/IV 以 4 字节碎片散布在 base64 文本的固定偏移处。
_JY_KEY_IV_OFFSETS = (0, 7, 20, 33, 40, 47, 59, 66, 76, 89, 99, 127)
_JY_KEY_LEN = 32
_JY_IV_LEN = 16


class DraftEncryptedError(Exception):
    pass


def _is_b64_text(data):
    head = data[:400]
    return all(chr(b).isalnum() or chr(b) in "+/= \r\n-" for b in head)


def _load_any_file(info_path):
    """读取草稿内容：明文直接读；base64 加密格式本地解密；
    新二进制格式（剪映 11.5）读不了时回退 bak。返回 (dict, 来源路径)。"""
    bak = info_path + ".bak"
    with open(info_path, "rb") as f:
        data = f.read()
    if data.lstrip().startswith((b"{", b"[")):
        return json.loads(data.decode("utf-8-sig")), info_path
    if _is_b64_text(data):
        return _decrypt_b64(data), info_path
    if os.path.isfile(bak):
        with open(bak, "rb") as f:
            bdata = f.read()
        if bdata.lstrip().startswith((b"{", b"[")):
            return json.loads(bdata.decode("utf-8-sig")), bak
        if _is_b64_text(bdata):
            return _decrypt_b64(bdata), bak
    raise DraftEncryptedError(
        "草稿为剪映 11.5 新二进制格式且备份不可用；"
        "若剪映正在运行，请先在剪映中关闭该草稿后重试")


def _decrypt_b64(data):
    """解密 base64 文本格式（剪映 6~11 兼容格式）。"""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.exceptions import InvalidTag
    except ImportError as e:
        raise DraftEncryptedError("需要 cryptography 库: pip install cryptography") from e
    payload = b"".join(data.split())
    if len(payload) < 131:
        raise DraftEncryptedError("加密草稿内容过短")
    key_iv = b"".join(payload[o:o + 4] for o in _JY_KEY_IV_OFFSETS)
    if len(key_iv) != _JY_KEY_LEN + _JY_IV_LEN:
        raise DraftEncryptedError("密钥/IV 数据不完整")
    enc = bytearray(payload)
    for o in sorted(_JY_KEY_IV_OFFSETS, reverse=True):
        del enc[o:o + 4]
    try:
        ct = base64.b64decode(bytes(enc), validate=True)
        plain = AESGCM(key_iv[:_JY_KEY_LEN]).decrypt(key_iv[_JY_KEY_LEN:], ct, None)
    except (binascii.Error, InvalidTag, ValueError) as e:
        raise DraftEncryptedError(f"解密失败（可能是不支持的剪映版本）: {e}") from e
    return json.loads(plain)


def _strip_tags(s):
    return re.sub(r"<[^>]+>", "", s or "")


def _parse_text_content(content):
    """字幕 content 是 JSON 字符串，取出纯文本。"""
    try:
        return _strip_tags(json.loads(content).get("text", ""))
    except Exception:
        return _strip_tags(content)


def list_drafts():
    """返回草稿列表，按修改时间倒序。"""
    items = []
    if not os.path.isdir(DRAFT_ROOT):
        return items
    for name in os.listdir(DRAFT_ROOT):
        info = os.path.join(DRAFT_ROOT, name, "draft_info.json")
        if not os.path.isfile(info):
            continue
        mtime = os.path.getmtime(info)
        tm = ""
        meta = os.path.join(DRAFT_ROOT, name, "draft_meta_info.json")
        if os.path.isfile(meta):
            try:
                with open(meta, encoding="utf-8") as f:
                    tm = json.load(f).get("tm_draft_update", "")
            except Exception:
                pass
        items.append({"name": name, "mtime": mtime, "tm_draft_update": tm})
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


def _extract_subtitles(d):
    texts = {t["id"]: t for t in d["materials"].get("texts", [])}
    subs = []
    for t in d.get("tracks", []):
        if t.get("type") != "text":
            continue
        for s in t.get("segments", []):
            mat = texts.get(s.get("material_id"))
            if not mat:
                continue
            tr = s.get("target_timerange", {})
            subs.append({
                "segment_id": s["id"],
                "material_id": mat["id"],
                "text": _parse_text_content(mat.get("content", "")),
                "start_sec": round(tr.get("start", 0) / 1e6, 3),
                "end_sec": round((tr.get("start", 0) + tr.get("duration", 0)) / 1e6, 3),
            })
    subs.sort(key=lambda x: x["start_sec"])
    return subs


def load_subtitles(name):
    """读取草稿的字幕（文本轨），按时间排序返回
    [{segment_id, material_id, text, start_sec, end_sec}]。
    支持加密草稿（自动本地解密）。"""
    path = os.path.join(DRAFT_ROOT, name, "draft_info.json")
    d, _src = _load_any_file(path)
    return _extract_subtitles(d)


def _strip_punct(s):
    return re.sub(r"[，。；：、！？（）“”‘’《》【】·—…\s]", "", s or "")


def _is_plain(path):
    with open(path, "rb") as f:
        return f.read(1) == b"{"


def _jianying_running_raw():
    from platforms import jianying_running
    return jianying_running()


def health_check(max_probe=5):
    """探测本机剪映草稿格式是否仍被本工具支持（SRT 导出依赖草稿读取）。"""
    drafts = list_drafts()[:max_probe]
    jy_running = _jianying_running_raw()
    results = []
    for d in drafts:
        name = d["name"]
        info_path = os.path.join(DRAFT_ROOT, name, "draft_info.json")
        entry = {"name": name, "mtime": d["mtime"], "status": "unknown",
                 "source": "", "error": ""}
        if not os.path.isfile(info_path):
            entry["status"] = "no_file"
            results.append(entry)
            continue
        try:
            data, src = _load_any_file(info_path)
            if not isinstance(data, dict) or "materials" not in data or "tracks" not in data:
                entry["status"] = "unsupported"
                entry["error"] = "结构异常（缺少 materials/tracks）"
            else:
                entry["status"] = "read_plain" if _is_plain(info_path) else "read_b64"
                entry["source"] = os.path.relpath(src, DRAFT_ROOT)
        except DraftEncryptedError as e:
            entry["status"] = "unsupported"
            entry["error"] = str(e)[:120]
        except Exception as e:
            entry["status"] = "unsupported"
            entry["error"] = f"{type(e).__name__}: {str(e)[:100]}"
        results.append(entry)

    unsupported = [r for r in results if r["status"] == "unsupported"]
    readable = [r for r in results if r["status"].startswith("read")]
    if not results:
        ok, msg = True, "未找到任何草稿（正常，如果你还没用过剪映）"
    elif unsupported and not readable:
        ok, msg = False, ("剪映草稿格式已变，当前工具无法读取任何草稿。"
                          "很可能是剪映版本升级引入了新格式，需要更新本工具。")
    elif unsupported:
        ok, msg = True, (f"{len(unsupported)} 个草稿无法读取（可能被新版剪映锁定或格式升级），"
                         f"其余 {len(readable)} 个读取正常。")
    else:
        ok, msg = True, f"全部 {len(readable)} 个草稿读取正常，格式兼容。"
    if jy_running:
        msg += " 注意：剪映正在运行（不影响导出 SRT，导出为只读操作）。"
    return {"ok": ok, "jianying_running": jy_running, "drafts": results,
            "unsupported_count": len(unsupported), "message": msg}
