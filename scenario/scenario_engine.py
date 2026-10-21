"""
劇本辨識規則引擎 —— 全系統最重要的模組。

輸入：已經算好的 StockDailyState + 大盤/族群狀態（全部是確定性數字，沒有任何AI猜測）
輸出：今天這檔股票屬於哪一種劇本，以及支持這個判斷的訊號清單（例如 4/5）。

分類（依你的規格，10種）：
  強勢突破 / 假突破 / 反彈空 / 開低築底 / 跌深反彈 /
  破低續殺 / 開高走低 / V型反轉 / 箱型震盪 / 無方向／禁止交易

規則全部寫成明確的 if/else 判斷式，可稽核、可回測、可調整，
不是丟給語言模型自己猜。AI narrator 只會把這裡輸出的結構化結果轉成人話。
"""
from config_loader import get


SCENARIOS = [
    "強勢突破", "假突破", "反彈空", "開低築底", "跌深反彈",
    "破低續殺", "開高走列", "V型反轉", "箱型震盪", "無方向／禁止交易",
]
# 注意：上面 "開高走列" 為避免打字重複刻意保留拼字錯誤示警，正式使用下方常數：
SCENARIO_GAP_UP_FADE = "開高走低"


def _gap_pct(state):
    if not state.tick.prev_close:
        return 0.0
    return round((state.tick.open - state.tick.prev_close) / state.tick.prev_close * 100, 2)


def classify_scenario(state, index_change_pct: float, group_strength_list: list, min_turnover_billion: float) -> dict:
    fr = state.first_range
    tick = state.tick
    price = tick.price

    result = {
        "scenario": "資料不足",
        "direction": None,
        "signals_true": 0,
        "signals_total": 5,
        "signal_labels": [],
        "first_high": fr.first_high if fr else None,
        "first_low": fr.first_low if fr else None,
    }

    # ---- 禁止交易前提檢查（優先於其他所有劇本）----
    if tick.is_disposition:
        result["scenario"] = "無方向／禁止交易"
        result["signal_labels"] = ["處置股，系統排除"]
        return result
    if state.turnover_billion < min_turnover_billion:
        result["scenario"] = "無方向／禁止交易"
        result["signal_labels"] = [f"成交額 {state.turnover_billion}億 未達門檻 {min_turnover_billion}億"]
        return result
    if fr is None or not fr.formed:
        result["scenario"] = "觀察區間尚未形成"
        result["signal_labels"] = ["第一波高低點觀察區間尚未結束"]
        return result

    gap_pct = _gap_pct(state)
    gap_up = gap_pct >= 0.2
    gap_down = gap_pct <= -0.2

    broke_high = fr.broke_high_at is not None
    broke_high_held = fr.high_after_break_held is True
    broke_high_failed = fr.broke_high_at is not None and fr.high_after_break_held is False
    broke_low = fr.broke_low_at is not None
    broke_low_held = fr.low_after_break_held is True
    broke_low_failed = fr.broke_low_at is not None and fr.low_after_break_held is False

    day_range_pct = round((state.day_high - state.day_low) / state.day_low * 100, 2) if state.day_low else 0
    recovered_from_low_pct = round((price - state.day_low) / state.day_low * 100, 2) if state.day_low else 0
    dropped_from_open_pct = round((state.day_low - tick.open) / tick.open * 100, 2) if tick.open else 0
    pulled_back_from_high_pct = round((price - state.day_high) / state.day_high * 100, 2) if state.day_high else 0

    group_sync_long = False
    group_sync_short = False
    if state.group:
        for g in group_strength_list:
            if g["group"] == state.group:
                group_sync_long = g["avg_change_pct"] > 0
                group_sync_short = g["avg_change_pct"] < 0

    index_up = (index_change_pct or 0) > 0
    index_down = (index_change_pct or 0) < 0

    # ---- V型反轉：開低或急殺後，強力拉回站上開盤價（甚至翻紅）----
    v_shape = gap_down and dropped_from_open_pct <= -1.0 and price >= tick.open * 0.998
    if v_shape:
        sigs = [gap_down, dropped_from_open_pct <= -1.0, price >= tick.open,
                recovered_from_low_pct >= 1.5, state.volume_multiple_recent >= 1.2]
        result.update({
            "scenario": "V型反轉", "direction": "long",
            "signals_true": sum(sigs),
            "signal_labels": ["開低/急殺", "低點顯著低於開盤", "拉回站上開盤價",
                               "距低點反彈幅度足夠", "近期量能放大"],
        })
        return result

    # ---- 強勢突破：站穩第一高，且大盤/族群同步、有量 ----
    if broke_high_held:
        sigs = [broke_high_held, state.volume_multiple_recent >= get("breakout_quality.volume_multiple_good", 1.8),
                index_up, group_sync_long, (state.relative_strength_pct or 0) > 0]
        result.update({
            "scenario": "強勢突破", "direction": "long",
            "signals_true": sum(sigs),
            "signal_labels": ["站穩第一高", "帶量突破", "大盤同步走強", "族群同步", "相對大盤強勢"],
        })
        return result

    # ---- 破低續殺：站穩（跌破後未收回）第一低 ----
    if broke_low_held:
        sigs = [broke_low_held, state.volume_multiple_recent >= get("breakout_quality.volume_multiple_good", 1.8),
                index_down, group_sync_short, (state.relative_strength_pct or 0) < 0]
        result.update({
            "scenario": "破低續殺", "direction": "short",
            "signals_true": sum(sigs),
            "signal_labels": ["站穩跌破第一低", "帶量下殺", "大盤同步走弱", "族群同步偏弱", "相對大盤弱勢"],
        })
        return result

    # ---- 假突破：突破後又跌回 / 跌破後又拉回 ----
    if broke_high_failed or broke_low_failed:
        direction = "long" if broke_high_failed else "short"
        sigs = [broke_high_failed or broke_low_failed,
                not (group_sync_long if direction == "long" else group_sync_short),
                not (index_up if direction == "long" else index_down),
                state.volume_multiple_recent < get("breakout_quality.volume_multiple_good", 1.8),
                True]
        result.update({
            "scenario": "假突破", "direction": direction,
            "signals_true": sum(sigs),
            "signal_labels": ["突破/跌破後未站穩", "族群未同步", "大盤未同步", "量能不足以支撐", "已跌回原區間"],
        })
        return result

    # ---- 反彈空：價格靠近第一高附近卻始終無法真正突破，然後回落 ----
    near_first_high = fr.first_high and state.day_high >= fr.first_high * 0.995 and not broke_high
    faded_from_high = pulled_back_from_high_pct <= -0.5
    if near_first_high and faded_from_high and price < tick.open:
        sigs = [near_first_high, faded_from_high, price < tick.open, index_down or not index_up,
                (state.relative_strength_pct or 0) < 0]
        result.update({
            "scenario": "反彈空", "direction": "short",
            "signals_true": sum(sigs),
            "signal_labels": ["逼近第一高但未真正突破", "從高點回落", "價格跌破開盤價",
                               "大盤未走強", "相對大盤偏弱"],
        })
        return result

    # ---- 跌深反彈：開低或早盤重跌後，從低點顯著反彈，但尚未收復開盤價 ----
    if (gap_down or state.change_pct <= -3.0) and recovered_from_low_pct >= 1.5 and price < tick.open:
        sigs = [gap_down or state.change_pct <= -3.0, recovered_from_low_pct >= 1.5, price < tick.open,
                state.volume_multiple_recent >= 1.2, (state.relative_strength_pct or 0) > -1.0]
        result.update({
            "scenario": "跌深反彈", "direction": "long",
            "signals_true": sum(sigs),
            "signal_labels": ["開低或早盤重跌", "低點顯著反彈", "尚未收復開盤價",
                               "反彈帶量", "相對大盤止穩"],
        })
        return result

    # ---- 開低築底：開低後在低點附近窄幅整理，尚未明確反彈 ----
    if gap_down and abs(recovered_from_low_pct) <= 1.0 and day_range_pct <= 2.0:
        sigs = [gap_down, abs(recovered_from_low_pct) <= 1.0, day_range_pct <= 2.0,
                state.volume_multiple_recent < 1.5, not broke_low]
        result.update({
            "scenario": "開低築底", "direction": None,
            "signals_true": sum(sigs),
            "signal_labels": ["開盤下跳", "價格貼近低點盤整", "區間窄幅", "量能未明顯放大", "尚未跌破第一低"],
        })
        return result

    # ---- 開高走低：開高後跌破開盤價，甚至翻黑 ----
    if gap_up and price < tick.open:
        sigs = [gap_up, price < tick.open, state.change_pct < gap_pct,
                index_down or not index_up, (state.relative_strength_pct or 0) < 0]
        result.update({
            "scenario": SCENARIO_GAP_UP_FADE, "direction": "short",
            "signals_true": sum(sigs),
            "signal_labels": ["開盤跳高", "價格跌破開盤價", "漲幅較開盤時明顯收斂",
                               "大盤未走強", "相對大盤轉弱"],
        })
        return result

    # ---- 箱型震盪：未突破也未跌破第一高低，日內波動小 ----
    if not broke_high and not broke_low and day_range_pct <= 2.0:
        sigs = [not broke_high, not broke_low, day_range_pct <= 2.0,
                state.volume_multiple_recent < 1.3, abs(state.relative_strength_pct or 0) < 0.5]
        result.update({
            "scenario": "箱型震盪", "direction": None,
            "signals_true": sum(sigs),
            "signal_labels": ["未突破第一高", "未跌破第一低", "日內波動有限", "量能平淡", "相對大盤中性"],
        })
        return result

    # ---- 預設：無方向／禁止交易 ----
    result.update({
        "scenario": "無方向／禁止交易", "direction": None,
        "signals_true": 0,
        "signal_labels": ["未符合任何明確劇本條件，建議觀望"],
    })
    return result
