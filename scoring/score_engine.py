"""
當沖分數引擎：流動性20 + 趨勢20 + 量能20 + 相對強弱20 + 突破品質20 = 100分。
分別算出「多方分數」與「空方分數」（同一檔股票，多空是兩個獨立的分數）。

所有門檻/breakpoint 都從 config 讀，方便之後根據100個交易日的回測結果調整權重。
"""
from config_loader import get
from scenario.breakout_quality import evaluate_breakout_quality


def _score_by_breakpoints(value: float, breakpoints: list) -> float:
    for bp in breakpoints:
        if value >= bp["min"]:
            return bp["score"]
    return 0


def liquidity_score(state) -> float:
    bps = get("liquidity.turnover_score_breakpoints", [])
    return _score_by_breakpoints(state.turnover_billion, bps)


def volume_score(state) -> float:
    bps = get("volume.ratio_vs_yesterday_thresholds", [])
    return _score_by_breakpoints(state.volume_ratio_vs_yesterday or 0, bps)


def trend_score(state, direction: str) -> float:
    """多方：現價>MA5>MA20>MA60 完全多頭排列給滿分，部分符合給部分分。
    空方：反向排列。乖離過大（追價風險）扣分。
    鎖漲停視為完全多頭排列、鎖跌停視為完全空頭排列：鎖住時價格不動，均線會追上來跟現價一樣，
    「現價 > MA5」這種嚴格大於的條件反而全部不成立，兩邊都變 0 分，跟實際的強弱相反。"""
    price = state.tick.price
    ma5, ma20, ma60 = state.ma5, state.ma20, state.ma60
    if ma5 is None:
        return 10.0  # 資料不足，給中性分數

    if state.at_limit_up or state.at_limit_down:
        favored = "long" if state.at_limit_up else "short"
        base = 20.0 if direction == favored else 0.0
    else:
        conditions_long = [price > ma5]
        conditions_short = [price < ma5]
        if ma20 is not None:
            conditions_long.append(ma5 > ma20)
            conditions_short.append(ma5 < ma20)
        if ma60 is not None:
            conditions_long.append(ma20 > ma60 if ma20 else False)
            conditions_short.append(ma20 < ma60 if ma20 else False)

        conds = conditions_long if direction == "long" else conditions_short
        base = 20 * (sum(conds) / len(conds))

    # 乖離率過大 -> 追價風險扣分
    dev_pct = abs((price - ma5) / ma5 * 100) if ma5 else 0
    warn = get("chase_risk.ma5_deviation_warn_pct", 3.0)
    danger = get("chase_risk.ma5_deviation_danger_pct", 5.0)
    if dev_pct >= danger:
        base -= 8
    elif dev_pct >= warn:
        base -= 4
    return round(max(0, base), 1)


def relative_strength_score(state, direction: str) -> float:
    """多方：相對大盤越強分越高（大盤跌、個股不跌 -> 高分）。
    空方：相對大盤越弱分越高（大盤漲、個股漲不動甚至跌 -> 高分）。"""
    rs = state.relative_strength_pct
    if rs is None:
        return 10.0
    if direction == "long":
        val = rs
    else:
        val = -rs
    # 線性映射：rs <= -2% -> 0分, rs >= +4% -> 20分（門檻可再細調）
    strong_th = get("relative_strength.strong_threshold_pct", 1.0)
    score = (val - (-2.0)) / (4.0 - (-2.0)) * 20
    return round(max(0, min(20, score)), 1)


def breakout_quality_score(state, direction: str, group_strength_list: list, index_change_pct: float) -> tuple:
    bq = evaluate_breakout_quality(state, direction, group_strength_list, index_change_pct)
    score = round(bq["score_0_100"] / 100 * 20, 1)
    return score, bq


def compute_scores(state, group_strength_list: list, index_change_pct: float) -> dict:
    liq = liquidity_score(state)
    vol = volume_score(state)

    long_trend = trend_score(state, "long")
    long_rs = relative_strength_score(state, "long")
    long_bq, long_bq_detail = breakout_quality_score(state, "long", group_strength_list, index_change_pct)
    long_total = round(liq + long_trend + vol + long_rs + long_bq, 1)

    short_trend = trend_score(state, "short")
    short_rs = relative_strength_score(state, "short")
    short_bq, short_bq_detail = breakout_quality_score(state, "short", group_strength_list, index_change_pct)
    short_total = round(liq + short_trend + vol + short_rs + short_bq, 1)

    return {
        "liquidity": liq,
        "volume": vol,
        "long": {
            "trend": long_trend, "relative_strength": long_rs,
            "breakout_quality": long_bq, "breakout_detail": long_bq_detail,
            "total": long_total,
        },
        "short": {
            "trend": short_trend, "relative_strength": short_rs,
            "breakout_quality": short_bq, "breakout_detail": short_bq_detail,
            "total": short_total,
        },
    }
