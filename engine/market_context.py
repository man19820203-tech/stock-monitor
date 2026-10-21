"""
大盤 / 族群 相對強弱計算。

「相對大盤強弱」= 個股漲跌% - 大盤漲跌%，這是本系統最重要的判斷之一，
因為它直接反映「這檔股票今天是不是比大盤更值得做」。

族群強弱 = 族群成分股平均漲跌%，用來找「今天真正在噴的族群」，
並在族群內部標出領漲/掉隊者。
"""
from config_loader import get


class MarketContext:
    def __init__(self):
        self.index_prev_close = None
        self.index_price = None
        self.index_change_pct = 0.0

    def update_index(self, tick):
        self.index_prev_close = tick.prev_close
        self.index_price = tick.price
        if tick.prev_close:
            self.index_change_pct = round((tick.price - tick.prev_close) / tick.prev_close * 100, 2)

    def relative_strength_label(self, relative_strength_pct: float) -> str:
        strong_th = get("relative_strength.strong_threshold_pct", 1.0)
        weak_th = get("relative_strength.weak_threshold_pct", -1.0)
        if relative_strength_pct is None:
            return "N/A"
        if relative_strength_pct >= strong_th:
            return "強勢"
        if relative_strength_pct <= weak_th:
            return "弱勢"
        return "中性"


def compute_group_strength(states: dict, groups_cfg: dict) -> list[dict]:
    """states: symbol -> StockDailyState. 回傳依平均漲跌%排序的族群清單，
    每個族群內含成分股明細，方便畫面顯示「誰領漲、誰掉隊」。"""
    result = []
    for group_name, symbols in groups_cfg.items():
        members = []
        for sym in symbols:
            st = states.get(sym)
            if st is None:
                continue
            members.append({
                "symbol": sym,
                "name": st.name,
                "change_pct": st.change_pct,
            })
        if not members:
            continue
        avg_change = round(sum(m["change_pct"] for m in members) / len(members), 2)
        members.sort(key=lambda m: m["change_pct"], reverse=True)
        result.append({
            "group": group_name,
            "avg_change_pct": avg_change,
            "members": members,
            "leader": members[0] if members else None,
            "laggard": members[-1] if members else None,
        })
    result.sort(key=lambda g: g["avg_change_pct"], reverse=True)
    for i, g in enumerate(result, 1):
        g["rank"] = i
    return result


def group_is_synced(group_strength_list: list[dict], group_name: str, bullish: bool) -> bool:
    """判斷某族群是否「同步」（多方看族群是否平均上漲，空方看是否平均下跌），
    給突破品質評分用。"""
    for g in group_strength_list:
        if g["group"] == group_name:
            return g["avg_change_pct"] > 0 if bullish else g["avg_change_pct"] < 0
    return False
