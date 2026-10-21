"""
突破品質評分 —— 不是「看到突破就喊」，而是把突破拆成可驗證的條件，
逐項確定性計算，最後給出「有效突破 vs 假突破風險」的百分比。

檢查項目（對應你列的清單）：
  1. 有沒有量（突破當根K棒量 / 近5分鐘均量）
  2. 突破多少%（幅度太小視為雜訊）
  3. 突破後有沒有站穩（來自 FirstRangeState.high_after_break_held）
  4. 是否立刻跌回區間（fakeout 偵測）
  5. 大盤是否同步
  6. 同族群是否同步
  7. 上方是否為昨日高點 / 整數關卡 / 近期高點（壓力接近）

輸出：0~100 的「有效突破機率」估計值（規則式加權，非黑箱），
以及扣分/加分明細，讓 AI narrator 可以直接引用理由，而不用自己判斷。
"""
from config_loader import get


def _round_number_level(price: float) -> float:
    """找出價格上方最近的整數關卡（例如 100 的倍數 / 10 的倍數，依價位級距調整）"""
    if price < 50:
        step = 5
    elif price < 200:
        step = 10
    elif price < 500:
        step = 50
    else:
        step = 100
    import math
    return math.ceil(price / step) * step


def evaluate_breakout_quality(state, direction: str, group_strength_list: list,
                               index_change_pct: float, prior_high_ref: float = None,
                               prior_low_ref: float = None) -> dict:
    """direction: 'long'（檢查突破第一高） or 'short'（檢查跌破第一低）"""
    fr = state.first_range
    cfg_bq = get("breakout_quality", {})
    result = {
        "triggered": False,
        "has_volume": False,
        "breakout_pct": None,
        "held": None,
        "index_sync": None,
        "group_sync": None,
        "near_resistance": None,
        "resistance_ref": None,
        "score_0_100": 0,
        "reasons": [],
    }
    if fr is None:
        return result

    if direction == "long":
        if fr.broke_high_at is None or fr.first_high is None:
            return result
        result["triggered"] = True
        breakout_pct = round((fr.broke_high_price - fr.first_high) / fr.first_high * 100, 2)
        result["breakout_pct"] = breakout_pct
        result["held"] = fr.high_after_break_held
        vol_mult = state.volume_multiple_recent
        result["has_volume"] = vol_mult >= cfg_bq.get("volume_multiple_good", 1.8)
        result["index_sync"] = index_change_pct is not None and index_change_pct > 0
        group_ok = True
        if state.group and cfg_bq.get("group_sync_required", True):
            group_ok = any(g["group"] == state.group and g["avg_change_pct"] > 0 for g in group_strength_list)
        result["group_sync"] = group_ok
        ref = prior_high_ref or _round_number_level(fr.first_high)
        result["resistance_ref"] = ref
        dist_pct = round((ref - state.tick.price) / state.tick.price * 100, 2) if state.tick.price else None
        result["near_resistance"] = dist_pct is not None and 0 <= dist_pct <= cfg_bq.get("distance_to_prior_high_warn_pct", 2.0)
    else:
        if fr.broke_low_at is None or fr.first_low is None:
            return result
        result["triggered"] = True
        breakout_pct = round((fr.first_low - fr.broke_low_price) / fr.first_low * 100, 2)
        result["breakout_pct"] = breakout_pct
        result["held"] = fr.low_after_break_held
        vol_mult = state.volume_multiple_recent
        result["has_volume"] = vol_mult >= cfg_bq.get("volume_multiple_good", 1.8)
        result["index_sync"] = index_change_pct is not None and index_change_pct < 0
        group_ok = True
        if state.group and cfg_bq.get("group_sync_required", True):
            group_ok = any(g["group"] == state.group and g["avg_change_pct"] < 0 for g in group_strength_list)
        result["group_sync"] = group_ok
        ref = prior_low_ref or _round_number_level(fr.first_low) - (
            10 if fr.first_low >= 100 else 5)
        result["resistance_ref"] = ref
        dist_pct = round((state.tick.price - ref) / state.tick.price * 100, 2) if state.tick.price else None
        result["near_resistance"] = dist_pct is not None and 0 <= dist_pct <= cfg_bq.get("distance_to_prior_high_warn_pct", 2.0)

    # ---- 加權計分（100分制，規則式，非AI猜測）----
    score = 0
    reasons = result["reasons"]
    if result["has_volume"]:
        score += 30
        reasons.append("突破帶量")
    else:
        reasons.append("量能不足")
    if result["held"] is True:
        score += 30
        reasons.append("突破後站穩")
    elif result["held"] is False:
        score -= 20
        reasons.append("突破後跌回區間，假突破嫌疑")
    if result["index_sync"]:
        score += 20
        reasons.append("大盤同步")
    else:
        reasons.append("大盤不同步")
    if result["group_sync"]:
        score += 15
        reasons.append("同族群同步")
    else:
        reasons.append("同族群不同步")
    if result["near_resistance"]:
        score -= 15
        reasons.append(f"接近上方關卡 {result['resistance_ref']}，壓力風險")
    score = max(0, min(100, score + 25))  # +25 基礎分，觸發即代表已具備最基本型態
    result["score_0_100"] = score
    return result
