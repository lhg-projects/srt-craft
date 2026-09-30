"""AI 辅助字幕校准：经 litellm（开源多供应商网关）统一调用各家大模型。

支持的供应商预设见 PROVIDERS（OpenAI / DeepSeek / 月之暗面 / 智谱 / 火山方舟 /
Anthropic / Ollama 本地 / 自定义 OpenAI 兼容端点），也可在页面「⚙️ AI 设置」切换。
配置存本机 config.json，密钥不上传。

litellm 模型串约定：provider 前缀 + 模型名（如 deepseek/deepseek-chat、
openai/glm-5.3-flash、ollama/qwen2.5）；预设会自动补前缀。
"""
import json
import os
import re

_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(_DIR, "config.json")

# 供应商预设：id → {label, base_url（留空=litellm 原生路由）, model（含 litellm 前缀）}
PROVIDERS = {
    "openai":    {"label": "OpenAI",              "base_url": "",                                    "model": "gpt-4o-mini"},
    "deepseek":  {"label": "DeepSeek",            "base_url": "https://api.deepseek.com",            "model": "deepseek/deepseek-chat"},
    "moonshot":  {"label": "月之暗面 Kimi",        "base_url": "https://api.moonshot.cn/v1",          "model": "moonshot/moonshot-v1-8k"},
    "zhipu":     {"label": "智谱 GLM",            "base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "zhipuai/glm-4-flash"},
    "ark":       {"label": "火山方舟（豆包）",      "base_url": "",                                    "model": "openai/…"},
    "anthropic": {"label": "Anthropic Claude",    "base_url": "",                                    "model": "claude-3-5-haiku-latest"},
    "ollama":    {"label": "Ollama（本地）",       "base_url": "http://localhost:11434",              "model": "ollama/qwen2.5"},
    "custom":    {"label": "自定义（OpenAI 兼容）", "base_url": "",                                    "model": "openai/…"},
}

SYSTEM_PROMPT = """你是字幕校准助手。给你两份文本：
1.【字幕】语音识别的结果，可能有同音错字、漏字、多字、字符错位
2.【文稿】正确的原始文稿（与字幕内容一致，可能有少量后期改写）

任务：把【字幕】逐条修正为与【文稿】一致、通顺正确的文本。严格规则：
- 输出行数必须与输入字幕行数完全相同，按原顺序逐行输出
- 只修正文字错误；若某行已经正确则原样输出
- 每行内容以该行对应的语音为准，参考文稿判断正字（如同音错字、专名错写）
- 不要把多行合并成一行，也不要把一行拆成多行
- 直接输出修正后的各行，不要行号、不要解释、不要输出文稿"""

BATCH_SIZE = 12  # 每批送多少行，行间上下文足够且可控


def load_config():
    cfg = {}
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            pass
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _model_str(cfg):
    """把配置里的 provider/model/base_url 规范成 litellm 模型串。
    兼容旧配置：无 provider 但有 base_url → 按 OpenAI 兼容端点处理。"""
    provider = cfg.get("ai_provider", "")
    model = (cfg.get("ai_model") or "").strip()
    base_url = (cfg.get("ai_base_url") or "").strip()
    if not model:
        return "", base_url
    if "/" in model.split("-", 1)[0] or model.startswith(("claude-", "gpt-", "o1", "o3")):
        return model, base_url          # 已带 litellm 前缀或原生模型名
    preset = PROVIDERS.get(provider, {})
    prefix = preset.get("model", "").split("/")[0] + "/" if "/" in preset.get("model", "") else ""
    if provider in ("openai", "custom", "ark") or (base_url and not prefix):
        return "openai/" + model, base_url   # OpenAI 兼容端点
    if prefix:
        return prefix + model, base_url      # 深度求索/月之暗面/智谱/Ollama 等
    return model, base_url


def _chat(cfg, messages, max_tokens=16000, timeout=600):
    from litellm import completion
    model, base_url = _model_str(cfg)
    if not model:
        raise RuntimeError("请先在「⚙️ AI 设置」选择供应商并填写模型名")
    kwargs = {"model": model, "messages": messages,
              "max_tokens": max_tokens, "temperature": 0.1, "timeout": timeout}
    key = (cfg.get("ai_api_key") or "").strip()
    if key:
        kwargs["api_key"] = key
    if base_url:
        kwargs["api_base"] = base_url
    r = completion(**kwargs)
    content = r.choices[0].message.content or ""
    # 思考型模型会把推理放在 <think> 标签里
    return re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()


def ai_align(subtitles, script, cfg=None):
    """subtitles: [{segment_id, material_id, text, start_sec,...}]
    script: 整理后的原稿字符串
    返回 (fixed, changes, notes)，结构与 subfix.align 一致。"""
    cfg = cfg or load_config()
    if not (cfg.get("ai_api_key") or "").strip() and \
       cfg.get("ai_provider") != "ollama":
        raise RuntimeError("请先在「⚙️ AI 设置」填写 API 密钥（Ollama 本地模式除外）")

    lines = [s["text"] for s in subtitles]
    fixed_lines = []
    notes = []
    for start in range(0, len(lines), BATCH_SIZE):
        batch = lines[start:start + BATCH_SIZE]
        batch_block = "\n".join(batch)
        prompt = (f"【字幕】\n{batch_block}\n\n【文稿】\n{script}\n\n"
                  f"请输出修正后的 {len(batch)} 行字幕。")
        try:
            out = _chat(cfg, [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt}])
        except Exception as e:
            notes.append(f"批次 {start // BATCH_SIZE + 1} 调用失败: {e}")
            fixed_lines.extend(batch)  # 失败批次原样保留
            continue
        out_lines = [ln.strip() for ln in out.split("\n") if ln.strip()]
        if len(out_lines) != len(batch):
            notes.append(f"批次 {start // BATCH_SIZE + 1} 行数不匹配"
                         f"(期望{len(batch)},得{len(out_lines)})，该批原样保留")
            fixed_lines.extend(batch)
            continue
        fixed_lines.extend(out_lines)

    fixed = {}
    changes = []
    for i, (s, old, new) in enumerate(zip(subtitles, lines, fixed_lines)):
        if new != old and new:
            key = s.get("material_id") or s["segment_id"]
            fixed[key] = new
            changes.append({
                "segment_id": s["segment_id"],
                "material_id": s.get("material_id", ""),
                "index": i + 1,
                "start_sec": s.get("start_sec", 0),
                "old": old,
                "new": new,
            })
    return fixed, changes, notes
