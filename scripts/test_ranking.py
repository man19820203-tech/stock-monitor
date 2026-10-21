"""
儀表板多方／空方欄分欄測試：

    python scripts/test_ranking.py

不需要連線：每檔只出現在分數較高的那一欄，「最佳多方／空方」也依分欄計算。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import state_store
from web.app import api_ranking


def put(sym, long_score, short_score):
    state_store.upsert(sym, {"symbol": sym, "name": sym, "long_score": long_score,
                             "short_score": short_score, "scenario": "-", "direction": None})


put("A", 98, 40)    # 強烈偏多
put("B", 64, 50)    # 偏多
put("C", 30, 85)    # 強烈偏空
put("D", 50, 50)    # 同分：放多方欄（不會兩邊都出現）

r = api_ranking()
longs = [s["symbol"] for s in r["long_top"]]
shorts = [s["symbol"] for s in r["short_top"]]
assert longs == ["A", "B", "D"], longs
assert shorts == ["C"], shorts
assert not set(longs) & set(shorts), "同一檔不能兩欄都出現"
assert r["summary"] == {"best_long_count": 1, "best_short_count": 1}, r["summary"]

# 全部偏多時空方欄是空的（以前會把偏多股票依空方分數排進來）
for s in ["C", "D"]:
    state_store.remove(s)
r = api_ranking()
assert [s["symbol"] for s in r["short_top"]] == []

print("=== PASS: 多方／空方分欄全部正確 ===")
