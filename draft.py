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

_re = re  # 手术版函数内部用 _re 引用正则模块

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


# ---------- 「导入剪映」：用修正版 SRT + 处理后音频生成新剪映草稿 ----------
# 依赖 pyJianYingDraft（可选依赖，未安装时 import_jianying_draft 返回明确错误）。
# 策略：**总是新建草稿**（名字带时间戳，如「10月3日_字幕修正」），
# 绝不写回用户现有草稿——写回有渲染缓存/内存快照覆盖风险（见 README）。

def _encrypt_b64(plain_str):
    """把明文 JSON 字符串加密成剪映 6+ 的 base64 密文格式（_decrypt_b64 的逆）。
    剪映的 key/iv 取自 base64 字母表的可打印 ASCII 字符（真实草稿全文 100% 落在
    base64 字符集内，48 字节 key_iv 直接是 ASCII），这里按同规格随机生成。"""
    import secrets
    import string
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    alphabet = (string.ascii_letters + string.digits + "+/").encode("ascii")
    key_iv = bytes(secrets.choice(alphabet) for _ in range(_JY_KEY_LEN + _JY_IV_LEN))
    ct = AESGCM(key_iv[:_JY_KEY_LEN]).encrypt(key_iv[_JY_KEY_LEN:], plain_str.encode("utf-8"), None)
    buf = bytearray(base64.b64encode(ct))
    for off, pos in zip(_JY_KEY_IV_OFFSETS, range(0, len(key_iv), 4)):
        buf[off:off] = key_iv[pos:pos + 4]
    return bytes(buf)


def import_jianying_draft(srt_text, audio_path=None, draft_name=None):
    """一键导入剪映：**模板手术**——拷贝本机一个真实草稿作骨架，
    把字幕轨/音频轨替换为修正版内容后重新加密写回。

    为什么不直接生成草稿：pyJianYingDraft 0.3.0 输出剪映 5.9 schema（new_version 110），
    本机剪映 11.5（schema 187，text 素材 126 字段）打开直接报「草稿内容已损坏」。
    模板草稿的所有辅助文件与版本字段天然与本机剪映兼容。

    audio_path: 处理后音频（裁剪+变速成品），替换模板音频轨并指向该文件；
                None 则连音频轨一起移除，只保留字幕。
    返回 {ok, draft_name, draft_path, error?}。总是新建草稿，绝不写回现有草稿。
    """
    import copy
    import re as _re
    import shutil
    import time as _time
    import uuid as _uuid

    if not os.path.isdir(DRAFT_ROOT):
        return {"ok": False, "error": "未找到剪映草稿目录（本机需安装过剪映桌面版并创建过至少一个草稿）"}

    # ---- 1. 选模板：最近修改的、有 text 轨且能解密的草稿；给了音频则优先带 audio 轨的 ----
    candidates = []
    for name in os.listdir(DRAFT_ROOT):
        p = os.path.join(DRAFT_ROOT, name, "draft_info.json")
        if not os.path.isfile(p):
            continue
        try:
            d = _decrypt_b64(open(p, "rb").read())
        except Exception:
            continue
        types = [t.get("type") for t in d.get("tracks", [])]
        if "text" not in types:
            continue
        candidates.append({"name": name, "mtime": os.path.getmtime(p), "info": d,
                           "has_audio": "audio" in types})
    if not candidates:
        return {"ok": False, "error": "本机剪映草稿库里找不到可用作模板的草稿（需要至少一个带字幕轨的草稿）"}

    tpl = None
    if audio_path:
        with_audio = [c for c in candidates if c["has_audio"]]
        if with_audio:
            tpl = max(with_audio, key=lambda c: c["mtime"])
    tpl = tpl or max(candidates, key=lambda c: c["mtime"])

    # ---- 2. 草稿名（带时间戳；重名加序号） ----
    name = draft_name or f"srt_craft_导入_{_time.strftime('%m%d_%H%M')}"
    base = name
    i = 2
    while os.path.exists(os.path.join(DRAFT_ROOT, name)):
        name = f"{base} ({i})"
        i += 1
    target = os.path.join(DRAFT_ROOT, name)

    # ---- 3. 拷贝模板目录（跳过 .backup 大目录），保留剪映 11.5 期望的全部辅助文件 ----
    try:
        shutil.copytree(os.path.join(DRAFT_ROOT, tpl["name"]), target,
                        ignore=shutil.ignore_patterns(".backup", ".locked"))
    except Exception as e:
        return {"ok": False, "error": f"复制模板草稿失败: {e}"}

    try:
        d = tpl["info"]
        tpl_info_id = d.get("id", "")
        new_id = str(_uuid.uuid4()).upper()
        d["id"] = new_id

        # ---- 4. 字幕轨手术：保留第一条 text 轨，按 SRT 逐条克隆模板字幕素材 ----
        text_tracks = [t for t in d["tracks"] if t.get("type") == "text"]
        keep = text_tracks[0]
        tpl_seg = copy.deepcopy(keep["segments"][0])
        tpl_mat = copy.deepcopy(next(m for m in d["materials"]["texts"]
                                     if m["id"] == tpl_seg.get("material_id")))
        subs = _parse_srt_times(srt_text)
        if not subs:
            return {"ok": False, "error": "SRT 解析不到字幕（检查 ④ 区内容格式）"}
        segs, mats = [], []
        for s in subs:
            seg, mat = copy.deepcopy(tpl_seg), copy.deepcopy(tpl_mat)
            mid = str(_uuid.uuid4()).upper()
            seg["id"] = mid
            mat["id"] = mid
            seg["material_id"] = mid
            try:
                c = json.loads(mat.get("content", "{}"))
            except Exception:
                c = {}
            c["text"] = s["text"]
            mat["content"] = json.dumps(c, ensure_ascii=False)
            if "base_content" in mat:
                mat["base_content"] = s["text"]
            seg["target_timerange"] = {"start": s["start"], "duration": s["end"] - s["start"]}
            if "render_timerange" in seg:
                seg["render_timerange"] = {"start": s["start"], "duration": s["end"] - s["start"]}
            segs.append(seg)
            mats.append(mat)
        keep["segments"] = segs
        d["materials"]["texts"] = mats
        for t in text_tracks[1:]:          # 多余 text 轨整条移除
            d["tracks"].remove(t)

        # ---- 5. 音频轨手术 ----
        max_end = max(s["end"] for s in subs)
        audio_tracks = [t for t in d["tracks"] if t.get("type") == "audio"]
        if audio_path and os.path.isfile(audio_path) and audio_tracks:
            at = audio_tracks[0]
            tpl_aseg = copy.deepcopy(at["segments"][0])
            tpl_amat = copy.deepcopy(next(m for m in d["materials"]["audios"]
                                          if m["id"] == tpl_aseg.get("material_id")))
            try:
                import audio_io
                adur = int(audio_io.audio_duration_sec(audio_path) * 1e6)
            except Exception:
                adur = max_end
            amid = str(_uuid.uuid4()).upper()
            tpl_amat["id"] = amid
            tpl_amat["path"] = audio_path
            tpl_amat["duration"] = adur
            tpl_amat["name"] = os.path.basename(audio_path)
            tpl_aseg["id"] = str(_uuid.uuid4()).upper()
            tpl_aseg["material_id"] = amid
            tpl_aseg["target_timerange"] = {"start": 0, "duration": adur}
            if "source_timerange" in tpl_aseg:
                tpl_aseg["source_timerange"] = {"start": 0, "duration": adur}
            if "render_timerange" in tpl_aseg:
                tpl_aseg["render_timerange"] = {"start": 0, "duration": adur}
            at["segments"] = [tpl_aseg]
            d["materials"]["audios"] = [tpl_amat]
            max_end = max(max_end, adur)
            for t in audio_tracks[1:]:
                d["tracks"].remove(t)
        else:
            for t in audio_tracks:          # 没音频：整条移除音频轨，清空素材
                d["tracks"].remove(t)
            d["materials"]["audios"] = []
        d["duration"] = max_end

        # ---- 5.5 视频轨清空：模板的视频段指向模板自己的视频文件，留着会让
        # 用户打开后看到模板画面。对齐真实工作流（「10月4日」草稿 = 空视频轨 +
        # 字幕 + 配音）：视频轨保留一条但清空段落，其余视频/贴纸/特效轨移除。
        video_tracks = [t for t in d["tracks"] if t.get("type") == "video"]
        if video_tracks:
            video_tracks[0]["segments"] = []
            for t in video_tracks[1:]:
                d["tracks"].remove(t)
        d["materials"]["videos"] = []
        for k in ("stickers", "effects", "video_effects"):
            if k in d.get("materials", {}):
                d["materials"][k] = []
        d["tracks"] = [t for t in d["tracks"]
                       if t.get("type") in ("video", "text", "audio")]

        # ---- 6. 加密写回 + 同步 template*.tmp 双写副本 ----
        payload = json.dumps(d, ensure_ascii=False)
        with open(os.path.join(target, "draft_info.json"), "wb") as f:
            f.write(_encrypt_b64(payload))
        _sync_template_tmp(target, d)
        lp = os.path.join(target, "draft_info.json.log")
        if os.path.exists(lp):
            os.remove(lp)

        # ---- 7. meta：换 draft_id / draft_name / 路径后加密 ----
        meta_path = os.path.join(target, "draft_meta_info.json")
        if os.path.isfile(meta_path):
            try:
                meta = _decrypt_b64(open(meta_path, "rb").read())
                meta["draft_id"] = new_id
                meta["draft_name"] = name
                meta["draft_fold_path"] = target + os.sep
                meta["draft_root_path"] = target + os.sep
                with open(meta_path, "wb") as f:
                    f.write(_encrypt_b64(json.dumps(meta, ensure_ascii=False)))
            except Exception:
                pass  # meta 解析失败不阻塞：剪映大概率能靠 draft_info 自愈

        # ---- 8. 身份清洗：模板目录里残留旧草稿 id / 旧路径（Timelines/<旧id>/、
        # project.json、timeline_layout.json 等）。若模板草稿正被剪映打开，
        # 剪映刷新时会按这些身份把它的内存内容覆盖到新草稿上，冲掉我们的字幕/音频。
        _scrub_identity(target, old_id=str(tpl_info_id), new_id=new_id,
                        old_fold=os.path.join(DRAFT_ROOT, tpl["name"]),
                        new_fold=target, payload=payload)
        return {"ok": True, "draft_name": name, "draft_path": target}
    except Exception as e:
        # 失败不留半成品草稿
        shutil.rmtree(target, ignore_errors=True)
        return {"ok": False, "error": f"草稿生成失败: {e}"}


def _parse_srt_times(srt_text):
    """解析 SRT → [{start, end, text}]，时间单位微秒（剪映内部单位）。"""
    subs = []
    for block in (srt_text or "").strip().replace("\r\n", "\n").split("\n\n"):
        lines = [l for l in block.split("\n") if l.strip()]
        if len(lines) < 2:
            continue
        m = _re.match(r"(\d+):(\d+):(\d+),(\d+)\s*-->\s*(\d+):(\d+):(\d+),(\d+)", lines[1])
        if not m:
            continue
        h1, m1, s1, ms1, h2, m2, s2, ms2 = map(int, m.groups())
        text = "\n".join(lines[2:]).strip()
        subs.append({"start": (h1 * 3600 + m1 * 60 + s1) * 1000000 + ms1 * 1000,
                     "end": (h2 * 3600 + m2 * 60 + s2) * 1000000 + ms2 * 1000,
                     "text": text})
    return subs


def _sync_template_tmp(target, d):
    """同步 Timelines/<uuid>/template*.tmp 与根目录 template*.tmp（剪映 11.5 双写）。
    明文的直接写 JSON，加密的写密文。"""
    payload = json.dumps(d, ensure_ascii=False)

    def _sync_dir(dirpath):
        n = 0
        for f in ("template.tmp", "template-2.tmp"):
            fp = os.path.join(dirpath, f)
            if os.path.isfile(fp):
                head = open(fp, "rb").read(1)
                if head == b"{":
                    open(fp, "w", encoding="utf-8").write(payload)
                else:
                    open(fp, "wb").write(_encrypt_b64(payload))
                n += 1
        tl = os.path.join(dirpath, "Timelines")
        if os.path.isdir(tl):
            for dd in os.listdir(tl):
                inner = os.path.join(tl, dd)
                if os.path.isdir(inner):
                    n += _sync_dir(inner)
        return n

    return _sync_dir(target)


def _scrub_identity(target, old_id, new_id, old_fold, new_fold, payload):
    """清除模板拷贝里残留的旧草稿身份，防止剪映把它当作正打开的旧草稿做覆盖同步。

    - Timelines/<旧id>/ 重命名为 <新id>
    - 所有明文文本文件里的旧 id / 旧目录路径 字符串替换
    - 加密快照（嵌套 draft_info.json(.bak)）重新生成；template*.tmp 由
      _sync_template_tmp 已按新内容重写
    """
    tl = os.path.join(target, "Timelines")
    old_tl = os.path.join(tl, old_id)
    new_tl = os.path.join(tl, new_id)
    if os.path.isdir(old_tl) and not os.path.exists(new_tl):
        os.rename(old_tl, new_tl)

    old_bytes = [(old_id.encode(), new_id.encode()),
                 (old_fold.encode(), new_fold.encode())]
    n = 0
    for dirpath, dirs, files in os.walk(target):
        for f in files:
            fp = os.path.join(dirpath, f)
            head = open(fp, "rb").read(1)
            if head not in (b"{", b"["):
                continue  # 跳过二进制/加密文件（加密快照单独重生成）
            data = open(fp, "rb").read()
            orig = data
            for a, b in old_bytes:
                data = data.replace(a, b)
            if data != orig:
                open(fp, "wb").write(data)
                n += 1
    # 嵌套的加密快照重新生成（内容 = 手术后的新草稿）
    for rel in (os.path.join("Timelines", new_id, "draft_info.json"),
                 os.path.join("Timelines", new_id, "draft_info.json.bak")):
        fp = os.path.join(target, rel)
        if os.path.isfile(fp):
            open(fp, "wb").write(_encrypt_b64(payload))
    return n
