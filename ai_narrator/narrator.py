"""
AI 解讀層 —— 嚴格遵守你要求的架構：

    行情API → 程式計算 → 規則引擎 → AI解讀

AI 在這裡拿到的永遠是「已經算好的結構化結果」（劇本、突破品質、分數明細），
不會拿到原始 tick 資料，也不負責算任何價格/指標，更不會說「一定會漲」。

預設用規則式模板（不需要任何 API key，離線也能跑），
若有設定 ANTHROPIC_API_KEY 環境變數，會改用 Claude 把同一份結構化資料
潤飾成更自然的句子（仍然只是「翻譯」，不會產生新的數字）。
"""
import os


def _template_narrative(state, scenario_result: dict, scores: dict) -> str:
    fr = state.first_range
    direction = scenario_result["direction"]
    scenario = scenario_result["scenario"]
    sig_true = scenario_result["signals_true"]
    sig_total = scenario_result["signals_total"]

    parts = [f"{state.name}（{state.symbol}）目前判定為「{scenario}」劇本。"]

    if fr and fr.first_high and fr.first_low:
        parts.append(f"09:00起第一觀察區間形成第一高 {fr.first_high}、第一低 {fr.first_low}。")

    if direction == "long" and fr and fr.broke_high_at:
        vol_mult = state.volume_multiple_recent
        held_txt = "已站穩" if fr.high_after_break_held else (
            "尚未確認是否站穩" if fr.high_after_break_held is None else "未能站穩、已跌回區間")
        parts.append(
            f"現價突破第一高，成交量為近5分鐘均量的{vol_mult}倍，{held_txt}。"
        )
        bq = scores["long"]["breakout_detail"]
        if bq.get("near_resistance"):
            parts.append(f"惟上方接近關卡 {bq.get('resistance_ref')}，壓力尚在。")
        parts.append(f"突破品質評估：{'有效突破機率' if fr.high_after_break_held else '假突破風險'} "
                      f"{bq.get('score_0_100', 0)}%。")

    if direction == "short" and fr and fr.broke_low_at:
        vol_mult = state.volume_multiple_recent
        held_txt = "已站穩" if fr.low_after_break_held else (
            "尚未確認是否站穩" if fr.low_after_break_held is None else "未能站穩、已拉回區間")
        parts.append(
            f"現價跌破第一低，成交量為近5分鐘均量的{vol_mult}倍，{held_txt}。"
        )
        bq = scores["short"]["breakout_detail"]
        parts.append(f"跌破品質評估：{'有效跌破機率' if fr.low_after_break_held else '假跌破風險'} "
                      f"{bq.get('score_0_100', 0)}%。")

    if state.relative_strength_pct is not None:
        if state.relative_strength_pct > 0:
            parts.append(f"相對大盤 +{state.relative_strength_pct}%，走勢偏強。")
        elif state.relative_strength_pct < 0:
            parts.append(f"相對大盤 {state.relative_strength_pct}%，走勢偏弱。")

    parts.append(f"目前劇本符合訊號 {sig_true}/{sig_total} 項。")
    parts.append(f"多方分數 {scores['long']['total']} / 空方分數 {scores['short']['total']}（僅供排序參考，非買賣建議）。")

    return "".join(parts)


def _claude_narrative(structured_payload: dict) -> str:
    """選用：把結構化資料丟給 Claude API 潤飾成更口語的一段話。
    注意：只傳結構化摘要，不傳原始tick，也不讓模型做任何數字判斷。"""
    try:
        import anthropic
    except ImportError:
        return None
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    client = anthropic.Anthropic(api_key=api_key)
    prompt = (
        "你是台股當沖助手。以下是程式已經確定性算好的結構化資料（劇本分類、突破品質、分數），"
        "請把它翻譯成一段150字以內、口語但精確的繁體中文摘要。"
        "禁止新增任何數字、禁止預測漲跌、禁止說『一定』『保證』等字眼，"
        "只能重新敘述給定的事實與機率估計。\n\n"
        f"結構化資料：{structured_payload}"
    )
    msg = client.messages.create(
        model="claude-3-5-haiku-20241022",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text if msg.content else None


def narrate(state, scenario_result: dict, scores: dict, use_claude: bool = False) -> str:
    if use_claude:
        payload = {
            "symbol": state.symbol,
            "name": state.name,
            "scenario": scenario_result,
            "scores": {
                "long_total": scores["long"]["total"],
                "short_total": scores["short"]["total"],
                "breakout_long": scores["long"]["breakout_detail"],
                "breakout_short": scores["short"]["breakout_detail"],
            },
            "relative_strength_pct": state.relative_strength_pct,
            "first_high": state.first_range.first_high if state.first_range else None,
            "first_low": state.first_range.first_low if state.first_range else None,
        }
        text = _claude_narrative(payload)
        if text:
            return text
    return _template_narrative(state, scenario_result, scores)
