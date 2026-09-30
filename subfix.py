"""字幕-文稿对齐校准。

原理：剪映语音识别的字幕错字通常是同音/近音字，整段顺序基本不乱。
做法：
1. 把所有字幕拼成一个字符串 C（带每条的区间映射），
   原始文稿整理成字符串 S（去掉标点空白差异）；
2. difflib.SequenceMatcher 字符级对齐 C 和 S；
3. S 中的替换/插入片段 = 稿子对字幕的修正，按区间映射分配回对应字幕条；
4. 输出逐条修改清单（旧文本 → 新文本 + 定位）供用户审核。
"""
import difflib
import re

# 参与对齐时视为可忽略的字符（字幕与文稿的标点/空白差异不算错误）
_IGNORE = re.compile(r"[，。；：、！？（）“”\"'\s,.:;!?()\[\]—…《》\-]")


def _mask(s):
    """去掉标点空白，返回 (压缩串, 原始下标数组)"""
    chars, idx = [], []
    for i, ch in enumerate(s):
        if not _IGNORE.match(ch):
            chars.append(ch)
            idx.append(i)
    return "".join(chars), idx


# 中文数字读法 → 阿拉伯数字映射（字幕常把 5% 念成"百分之五"，对齐时两者等价；
# 应用修正时以文稿写法为准，但若字幕把数字展开朗读、而替换源是数字，则保留字幕读法）
_NUM_READINGS = [
    (re.compile(r"百分之"), "%"),
    (re.compile(r"百分之([一二三四五六七八九]?十?[一二三四五六七八九]?)点?([一二三四五六七八九]*)"), None),
    (re.compile(r"[一二三四五六七八九]+"), None),
]

_CN_DIGIT = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
             "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}


def _equivalent(a, b):
    """粗略判断两段文本是否为同一内容的两种写法（数字读法差异）。"""
    def norm(x):
        x = _IGNORE.sub("", x)
        for d, n in _CN_DIGIT.items():
            x = x.replace(d, n)
        x = x.replace("点", ".").replace("百分之", "%")
        return x
    return norm(a) == norm(b)


def subfix_next_prefix(text, n):
    """取字幕文本前 n 个非标点字符作为前缀样本。"""
    out = []
    for ch in text:
        if not _IGNORE.match(ch):
            out.append(ch)
            if len(out) >= n:
                break
    return "".join(out)


def align(subtitles, script):
    """subtitles: [{segment_id, text, start_sec, ...}]（按时间序）
    script: str 原始文稿
    返回 {segment_id: new_text} 与修改清单。只返回有改动的条。"""
    # c_text 必须与 s_text 同为去标点空间，否则 opcodes 下标与 c_map 错位，
    # 标点处会产生大量假修改（丢数字/丢字）
    c_text = "".join(s["text"] for s in subtitles)
    c_text, _ = _mask(c_text)
    c_map = []  # 压缩串下标 -> (字幕序号, 该字幕内字符位置)
    for si, s in enumerate(subtitles):
        for ci, ch in enumerate(s["text"]):
            if not _IGNORE.match(ch):
                c_map.append((si, ci))
    s_text, _ = _mask(script)

    sm = difflib.SequenceMatcher(None, c_text, s_text, autojunk=False)
    ops = sm.get_opcodes()  # (tag, c1, c2, s1, s2)

    per_sub_edits = {}  # si -> list[(ci_start, ci_end, replacement)]
    changes = []
    for tag, c1, c2, s1, s2 in ops:
        if tag == "equal":
            continue
        if c1 < len(c_map):
            si_a, _ = c_map[c1]
        else:
            si_a = c_map[-1][0] if c_map else 0
        if c2 > c1 and c2 - 1 < len(c_map):
            si_b, _ = c_map[c2 - 1]
        else:
            si_b = si_a
        repl_src = s_text[s1:s2]
        si = si_a
        ci_positions = [c_map[k][1] for k in range(c1, min(c2, len(c_map)))
                        if c_map[k][0] == si]
        if ci_positions:
            lo, hi = min(ci_positions), max(ci_positions) + 1
        else:
            lo = hi = len(subtitles[si]["text"])
        # 数字读法差异不算错误：字幕展开念数字时保留字幕写法
        old_frag = subtitles[si]["text"][lo:hi]
        if _equivalent(old_frag, repl_src):
            continue
        # 纯删除且会清空整条字幕 → 对齐错位的假修改（文稿比字幕短时常见），跳过
        if tag == "delete" and lo == 0 and hi >= len(subtitles[si]["text"].strip()):
            continue
        # 纯插入在字幕末尾时，判断插入内容是否属于下一条字幕开头的对齐偏移：
        # 若 repl 与下一条字幕开头相似（>0.6），跳过
        if tag == "insert" and lo == hi == len(subtitles[si]["text"]) \
                and si + 1 < len(subtitles) and repl_src:
            nxt = subfix_next_prefix(subtitles[si + 1]["text"], len(repl_src) + 2)
            if nxt and difflib.SequenceMatcher(None, repl_src, nxt).ratio() > 0.6:
                continue
        per_sub_edits.setdefault(si, []).append((lo, hi, repl_src))

    fixed = {}
    for si, edits in per_sub_edits.items():
        old_text = subtitles[si]["text"]
        new_text = old_text
        for lo, hi, repl in sorted(edits, key=lambda e: -e[0]):
            new_text = new_text[:lo] + repl + new_text[hi:]
        if new_text != old_text and new_text.strip():
            # 保险1：修改幅度限制——单条改动超过原文30%基本是对齐错位而非错字
            old_n = len(_IGNORE.sub('', old_text))
            new_n = len(_IGNORE.sub('', new_text))
            import difflib as _dl
            ratio = _dl.SequenceMatcher(None, old_text, new_text).ratio()
            if abs(new_n - old_n) > max(2, old_n * 0.3) or ratio < 0.7:
                continue
            # 保险2：通顺性——新文本里单字符异常重复（"它比它比"式错乱）则丢弃
            import re as _re
            compact = _IGNORE.sub('', new_text)
            if _re.search(r'(.{1,4})\1{1,}', compact) and \
                    _re.search(r'(.{1,3})\1{2,}', compact):
                continue
            # 键用 material_id（写回端按 material 匹配）；粘贴模式无 material_id 则用 segment_id
            sid = subtitles[si].get("material_id") or subtitles[si]["segment_id"]
            fixed[sid] = new_text
            changes.append({
                "segment_id": subtitles[si]["segment_id"],
                "material_id": subtitles[si].get("material_id", ""),
                "index": si + 1,
                "start_sec": subtitles[si].get("start_sec", 0),
                "old": old_text,
                "new": new_text,
            })
    return fixed, changes


def align_rebuild(subtitles, script):
    """重建式校准：把原稿按字符对齐重新分配到每条字幕，
    条数与时间轴保持不变。适用于字幕被污染/错乱、或希望整段以原稿为准。
    返回 {material_id 或 segment_id: new_text} 与变更清单。"""
    c_text = ''.join(s['text'] for s in subtitles)
    c_compact, c_idx = _mask(c_text)
    s_compact, s_idx = _mask(script)
    sm = difflib.SequenceMatcher(None, c_compact, s_compact, autojunk=False)
    c2s = {}
    for tag, a1, a2, b1, b2 in sm.get_opcodes():
        if tag == 'equal':
            for k in range(a2 - a1):
                c2s[a1 + k] = b1 + k
        elif tag == 'replace':
            n = min(a2 - a1, b2 - b1)
            for k in range(n):
                c2s[a1 + k] = b1 + k
            for k in range(n, a2 - a1):
                c2s[a1 + k] = min(b2 - 1, b1 + n)
        elif tag == 'delete':
            for k in range(a1, a2):
                c2s[k] = max(b1, min(len(s_compact) - 1, b1))

    fixed = {}
    changes = []
    si = 0
    for i, s in enumerate(subtitles):
        compact_len = len(_mask(s['text'])[0])
        c_start, c_end = si, si + compact_len
        si = c_end
        if compact_len == 0:
            continue
        s_lo = c2s.get(c_start, c2s.get(c_start - 1, 0))
        s_hi = c2s.get(c_end - 1, c2s.get(c_end, len(s_compact) - 1))
        if s_hi is None or s_hi < s_lo:
            continue
        orig_lo = s_idx[s_lo] if s_lo < len(s_idx) else len(script)
        orig_hi = s_idx[s_hi] + 1 if s_hi < len(s_idx) else len(script)
        new_text = script[orig_lo:orig_hi]
        if new_text == s['text'] or not new_text.strip():
            continue
        key = s.get('material_id') or s['segment_id']
        fixed[key] = new_text
        changes.append({
            'segment_id': s['segment_id'],
            'material_id': s.get('material_id', ''),
            'index': i + 1,
            'start_sec': s.get('start_sec', 0),
            'old': s['text'],
            'new': new_text,
        })
    return fixed, changes


def clean_script(raw):
    """整理用户文稿：合并换行为连续文本，去掉 markdown 符号。"""
    text = re.sub(r"^#+\s*", "", raw, flags=re.M)
    text = re.sub(r"\*+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _to_py(s):
    """逐字转拼音（含声调），非汉字原样保留。
    特殊处理：中文数字与阿拉伯数字读法歧义（"2万亿"er vs "两万亿"liang、
    "两"liang vs "二"er4），把 数字字符 与 常见中文数位字 统一为数字占位符，
    避免对齐链在数字处断开。"""
    from pypinyin import lazy_pinyin, Style
    DIGIT_CHARS = set("零一二两三四五六七八九十百千万亿点")
    out = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch.isdigit():
            # 阿拉伯数字串 → 单个占位符
            while i < len(s) and s[i].isdigit():
                i += 1
            out.append('#')
            continue
        if '\u4e00' <= ch <= '\u9fff':
            if ch in DIGIT_CHARS:
                # 中文数位字 → 占位符（连续数位字合并）
                j = i
                while j < len(s) and s[j] in DIGIT_CHARS:
                    j += 1
                out.append('#')
                i = j
                continue
            py = lazy_pinyin(ch, style=Style.TONE3)[0]
            if ch in '的地得':
                py = 'de'
            out.append(py)
            i += 1
        else:
            out.append(ch)
            i += 1
        # 连续占位符合并：数字/数位段无论多长统一为一个占位符
    merged = []
    for tok in out:
        if tok == '#' and merged and merged[-1] == '#':
            continue
        merged.append(tok)
    return ''.join(merged)


def align_phonetic(subtitles, script):
    """拼音对齐 v4（本地、不用 AI）：
    - token 级对齐：每个汉字 = 一个拼音 token，数字串 = 一个占位 token
    - token 相同但字不同 → 同音异形字，用文稿的字替换字幕的字
    - 数字读法歧义（2万亿/两万亿）自动归一，不再断链
    - 局限：读音不同的错误（快机山→会稽山）发现不了，请用 AI 模式
    """
    c_text = ''.join(s['text'] for s in subtitles)
    c_compact, c_idx = _mask(c_text)
    s_compact, s_idx = _mask(script)
    if not c_compact or not s_compact:
        return {}, []

    def tokenize(compact):
        from pypinyin import lazy_pinyin, Style
        toks, i = [], 0
        while i < len(compact):
            ch = compact[i]
            if ch.isdigit():
                j = i
                while j < len(compact) and compact[j].isdigit():
                    j += 1
                toks.append(('num', compact[i:j], '#'))
                i = j
            elif '\u4e00' <= ch <= '\u9fff':
                py = lazy_pinyin(ch, style=Style.TONE3)[0]
                if ch in '的地得':
                    py = 'de'
                toks.append(('hz', ch, py))
                i += 1
            else:
                toks.append(('sym', ch, ch))
                i += 1
        return toks

    c_toks = tokenize(c_compact)
    s_toks = tokenize(s_compact)
    c_keys = [t[2] for t in c_toks]
    s_keys = [t[2] for t in s_toks]
    sm = difflib.SequenceMatcher(None, c_keys, s_keys, autojunk=False)

    # token 映射：c token 索引 → s token 索引
    tok_map = {}
    for tag, a1, a2, b1, b2 in sm.get_opcodes():
        if tag in ('equal', 'replace'):
            n = min(a2 - a1, b2 - b1)
            for k in range(n):
                tok_map[a1 + k] = b1 + k

    # token 索引 → 压缩字符索引（数字串 token 占多字符，两者不是 1:1！）
    def tok_char_index(toks):
        out, pos = [], 0
        for t in toks:
            tlen = len(t[1]) if t[0] == 'num' else 1
            out.append(pos)
            pos += tlen
        return out

    c_tok2char = tok_char_index(c_toks)
    s_tok2char = tok_char_index(s_toks)

    # 同音异形替换点：token 索引 → 新字
    replacements = {}   # auto：拼音相同的异形字
    manual = {}         # 人工：拼音不同的可疑块（以 c 压缩字符索引 → (old,new) 记录）
    for ci_t, ct in enumerate(c_toks):
        if ct[0] != 'hz':
            continue
        sj_t = tok_map.get(ci_t)
        if sj_t is None:
            continue
        st = s_toks[sj_t]
        # 只替换【拼音完全相同】的同音异形字：好hao3→差cha4 拼音不同 → 拒绝
        if st[0] == 'hz' and st[2] == ct[2] and st[1] != ct[1]:
            ci_char = c_tok2char[ci_t]      # 字幕压缩字符索引
            new_ch = s_compact[s_tok2char[sj_t]]  # 文稿对应字
            replacements[ci_char] = new_ch

    # 第二轮：忽略声调的模糊匹配（已yi3→一yi1 等声调差异的错字）
    # 条件：去声调后拼音相同 + 邻近上下文多数对应（≥2/3 可用邻居 key 相同）
    for ci_t, ct in enumerate(c_toks):
        if ct[0] != 'hz':
            continue
        sj_t = tok_map.get(ci_t)
        if sj_t is None:
            continue
        st = s_toks[sj_t]
        if st[0] != 'hz' or st[1] == ct[1]:
            continue
        ci_char = c_tok2char[ci_t]
        if ci_char in replacements:
            continue
        py_c = ct[2].rstrip('01234')
        py_s = st[2].rstrip('01234')
        if not py_c or py_c != py_s:
            continue
        # 邻近上下文：前后各3个 token（跳过自身），经 tok_map 对应
        good, total = 0, 0
        for off in (-3, -2, -1, 1, 2, 3):
            ni = ci_t + off
            if ni < 0 or ni >= len(c_toks):
                continue
            nj = tok_map.get(ni)
            if nj is None or nj >= len(s_toks):
                continue
            total += 1
            nk_c = c_toks[ni][2].rstrip('01234') if c_toks[ni][0] == 'hz' else c_toks[ni][2]
            nk_s = s_toks[nj][2].rstrip('01234') if s_toks[nj][0] == 'hz' else s_toks[nj][2]
            if nk_c == nk_s:
                good += 1
        if total > 0 and good / total >= 2 / 3:
            replacements[ci_char] = s_compact[s_tok2char[sj_t]]

    # 模糊替换（auto2）：1:1 短 replace 块，块所在邻近（前后5 token）多数相同 → 通假字
    for tag, a1, a2, b1, b2 in sm.get_opcodes():
        if tag != 'replace' or a2 - a1 != b2 - b1 or a2 - a1 > 2:
            continue
        # 邻近上下文相同比例
        ctx_lo, ctx_hi = max(0, a1 - 5), min(len(c_keys), a2 + 5)
        ctx_c, ctx_s = c_keys[ctx_lo:a1] + c_keys[a2:ctx_hi], s_keys[ctx_lo:a1] + s_keys[a2:ctx_hi]
        same_ctx = sum(1 for k in range(min(len(ctx_c), len(ctx_s))) if ctx_c[k] == ctx_s[k])
        if ctx_c and same_ctx / max(1, min(len(ctx_c), len(ctx_s))) < 0.6:
            continue
        for k in range(a2 - a1):
            ct, st = c_toks[a1 + k], s_toks[b1 + k]
            if ct[0] != 'hz' or st[0] != 'hz' or ct[1] == st[1]:
                continue
            if ct[1] not in [c[1] for c in [repl_t for repl_t in []]]:
                pass
            ci_char = c_tok2char[a1 + k]
            if ci_char in replacements:
                continue
            manual[ci_char] = (ct[1], st[1])

    # 应用到每条字幕（保留原断句/标点，只换同音异形字）
    fixed, changes = {}, []
    si = 0
    for i, s in enumerate(subtitles):
        compact_len = len(_mask(s['text'])[0])
        chars = list(s['text'])
        auto_changed = False
        has_manual = False
        for k in range(compact_len):
            ci = si + k
            is_manual_pt = ci in manual
            new_ch = manual[ci][1] if is_manual_pt else replacements.get(ci)
            if new_ch is None:
                continue
            seen = 0
            for mi, mc in enumerate(chars):
                if _IGNORE.match(mc):
                    continue
                if seen == k:
                    if mc != new_ch:
                        chars[mi] = new_ch
                        if is_manual_pt:
                            has_manual = True
                        else:
                            auto_changed = True
                    break
                seen += 1
        si += compact_len
        new_text = ''.join(chars) if (auto_changed or has_manual) else s['text']
        if auto_changed and not has_manual:
            # 纯自动修改：相似度保险后进 fixed
            old_compact = _IGNORE.sub('', s['text'])
            new_compact = _IGNORE.sub('', new_text)
            sim = difflib.SequenceMatcher(None, old_compact, new_compact).ratio()
            if sim < 0.6:
                continue
            key = s.get('material_id') or s['segment_id']
            fixed[key] = new_text
            changes.append({
                'segment_id': s['segment_id'], 'material_id': s.get('material_id', ''),
                'index': i + 1, 'start_sec': s.get('start_sec', 0),
                'old': s['text'], 'new': new_text,
            })
        elif has_manual:
            # 含人工点：同样自动写回（不再要求逐条人工确认）
            old_compact = _IGNORE.sub('', s['text'])
            new_compact = _IGNORE.sub('', new_text)
            sim = difflib.SequenceMatcher(None, old_compact, new_compact).ratio()
            if sim >= 0.6:
                key = s.get('material_id') or s['segment_id']
                fixed[key] = new_text
            changes.append({
                'segment_id': s['segment_id'], 'material_id': s.get('material_id', ''),
                'index': i + 1, 'start_sec': s.get('start_sec', 0),
                'old': s['text'], 'new': new_text,
            })
    return fixed, changes
