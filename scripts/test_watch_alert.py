"""
關注提醒（watch_alert）篩選條件測試：

    python scripts/test_watch_alert.py

不需要連線、不需要開盤，直接用假資料驗證「哪些股票會跳提醒」。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from watch_alert import match_watch_alerts

CFG = {"enabled": True, "scenarios": ["強勢突破", "V型反轉", "跌深反彈"], "min_score": 70}


def s(symbol, scenario, direction, long_score, short_score):
    return {"symbol": symbol, "scenario": scenario, "direction": direction,
            "long_score": long_score, "short_score": short_score}


def symbols(hits):
    return sorted(h["symbol"] for h in hits)


states = [
    s("A", "強勢突破", "long", 85, 20),        # 符合
    s("B", "V型反轉", "long", 70, 30),         # 剛好等於門檻 → 符合
    s("C", "跌深反彈", "long", 69.9, 30),      # 分數差一點 → 不符合
    s("D", "破低續殺", "short", 20, 95),       # 空方劇本 → 不符合
    s("E", "無方向／禁止交易", None, 90, 10),  # 劇本不在清單 → 不符合
    s("F", "假突破", "long", 88, 10),          # 劇本不在清單 → 不符合
]

# 1. 基本條件：劇本在清單內 且 多方分數 ≥ 門檻
assert symbols(match_watch_alerts(states, CFG)) == ["A", "B"], symbols(match_watch_alerts(states, CFG))

# 2. 空方劇本用空方分數判斷
cfg_short = {"enabled": True, "scenarios": ["破低續殺"], "min_score": 70}
assert symbols(match_watch_alerts(states, cfg_short)) == ["D"]

# 3. 關閉功能 / 沒有設定 → 一律不提醒
assert match_watch_alerts(states, {**CFG, "enabled": False}) == []
assert match_watch_alerts(states, None) == []
assert match_watch_alerts(states, {}) == []

# 4. 門檻調低時，分數差一點的也會進來
assert symbols(match_watch_alerts(states, {**CFG, "min_score": 60})) == ["A", "B", "C"]

# 5. 回傳內容帶有提醒要用的分數欄位（依劇本方向），並依分數高到低排序
hits = match_watch_alerts(states, CFG)
assert [h["alert_score"] for h in hits] == [85, 70]

print("=== PASS: watch_alert 篩選條件全部正確 ===")
