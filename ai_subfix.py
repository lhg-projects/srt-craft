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

# 每家供应商的配置说明（借鉴 MoneyPrinterTurbo 的 provider tips 卡）：
# key 去哪申请、base_url 规则、默认模型。前端「⚙️ AI 设置」右侧说明卡展示。
PROVIDER_TIPS = {
    "openai": "API Key 在 platform.openai.com/api-keys 创建。Base Url 留空即官方地址；"
              "国内访问需要代理。默认模型 gpt-4o-mini，可换成账号支持的其它模型。",
    "deepseek": "API Key 在 platform.deepseek.com 创建。Base Url 用 https://api.deepseek.com"
                "（已预填）。默认 deepseek-chat，价格低、中文效果好。",
    "moonshot": "API Key 在 platform.moonshot.cn 创建。Base Url 用 https://api.moonshot.cn/v1"
                "（已预填）。默认 moonshot-v1-8k，长字幕可换 moonshot-v1-32k。",
    "zhipu": "API Key 在 open.bigmodel.cn 创建（个人可免费领 glm-4-flash 额度）。"
             "Base Url 用 https://open.bigmodel.cn/api/paas/v4（已预填）。",
    "ark": "火山方舟（豆包）：在 console.volcengine.com/ark 开通模型并创建 API Key。"
           "Base Url 填你的接入点地址（形如 https://ark.cn-beijing.volces.com/api/v3）；"
           "模型名填你在方舟开通的模型 ID 或推理接入点 ID。",
    "anthropic": "API Key 在 console.anthropic.com 创建。Base Url 留空即官方地址；"
                 "国内访问需要代理。默认 claude-3-5-haiku（快且便宜）。",
    "ollama": "本地运行、免密钥：先安装 ollama 并 `ollama pull qwen2.5`，"
              "Base Url 保持 http://localhost:11434。模型名填已 pull 的模型。",
    "custom": "任何 OpenAI 兼容服务（MiniMax / 阶跃 / siliconflow / 中转站等）："
              "填服务方的 Base Url（通常以 /v1 结尾）和密钥，模型名以服务方文档为准。"
              "请确保 API Key 与 Base Url 来自同一平台，否则会鉴权失败。",
}


def test_connection(cfg=None):
    """最小请求验证 AI 链路真实可用（借鉴 MoneyPrinterTurbo 的 test_connection：
    复用与正式校正完全相同的调用路径，但只发一条 "Reply with exactly: OK"）。
    返回 (成功, 错误信息, 耗时秒)。"""
    import time
    cfg = cfg or load_config()
    started = time.perf_counter()
    try:
        out = _chat(cfg, [{"role": "user", "content": "Reply with exactly: OK"}],
                    max_tokens=16, timeout=60)
        elapsed = time.perf_counter() - started
        if not out.strip():
            return False, "模型返回了空响应", elapsed
        return True, "", elapsed
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:200]}", time.perf_counter() - started

SYSTEM_PROMPT = """你是字幕校准助手。给你两份文本：
1.【字幕】语音识别的结果，可能有同音错字、漏字、多字、字符错位
2.【文稿】正确的原始文稿（与字幕内容一致，可能有少量后期改写）

任务：把【字幕】逐条修正为与【文稿】一致、通顺正确的文本。严格规则：
- 输出行数必须与输入字幕行数完全相同，按原顺序逐行输出
- 只修正文字错误；若某行已经正确则原样输出
- 每行内容以该行对应的语音为准，参考文稿判断正字（如同音错字、专名错写）
- 不要把多行合并成一行，也不要把一行拆成多行
- 直接输出修正后的各行，不要行号、不要解释、不要输出文稿"""

# 无原稿模式：纯校对，没有参照系，规则必须更保守（AI 可能把对的改成错的）
NO_SCRIPT_SYSTEM_PROMPT = """你是字幕校对助手。给你一份语音识别的字幕（无原始文稿）。

任务：只修正【确定性错误】，严格规则：
- 只修：同音错字（结合上下文判断，如"印尼孽股"→"印尼镍钴"）、重复字词（如"的的"）、
  明显的标点/数字格式问题、上下文能唯一确定的专名错写
- 不修：任何需要猜测的表达、口误、语气词、口语化说法——保持原样
- 输出行数必须与输入字幕行数完全相同，按原顺序逐行输出
- 不要把多行合并成一行，也不要把一行拆成多行，不要改写句式
- 宁可少改，不可改错；没有把握就原样输出
- 直接输出修正后的各行，不要行号、不要解释"""

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
    script: 整理后的原稿字符串；**为空时进入"无原稿校对"模式**（纯 AI 语义纠错，
    提示词更保守：只修确定性错误）。
    返回 (fixed, changes, notes)，结构与 subfix.align 一致。"""
    cfg = cfg or load_config()
    if not (cfg.get("ai_api_key") or "").strip() and \
       cfg.get("ai_provider") != "ollama":
        raise RuntimeError("请先在「⚙️ AI 设置」填写 API 密钥（Ollama 本地模式除外）")

    no_script = not script.strip()
    system_prompt = NO_SCRIPT_SYSTEM_PROMPT if no_script else SYSTEM_PROMPT
    lines = [s["text"] for s in subtitles]
    fixed_lines = []
    notes = []
    for start in range(0, len(lines), BATCH_SIZE):
        batch = lines[start:start + BATCH_SIZE]
        batch_block = "\n".join(batch)
        if no_script:
            # 无原稿：全量字幕作为上下文（无参照系时前后文是唯一的纠错线索）
            prompt = (f"【字幕全文（上下文）】\n{chr(10).join(lines)}\n\n"
                      f"【待校对】（第 {start + 1}~{start + len(batch)} 行）\n{batch_block}\n\n"
                      f"请只输出【待校对】部分修正后的 {len(batch)} 行字幕。")
        else:
            prompt = (f"【字幕】\n{batch_block}\n\n【文稿】\n{script}\n\n"
                      f"请输出修正后的 {len(batch)} 行字幕。")
        try:
            out = _chat(cfg, [
                {"role": "system", "content": system_prompt},
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
