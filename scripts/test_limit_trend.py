"""
漲跌停價（依台股升降單位）與「鎖漲停視為多頭、鎖跌停視為空頭」測試：

    python scripts/test_limit_trend.py

不需要連線。
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.indicators import tw_limit_prices
from market_time import TW_TZ
from models import StockDailyState, Tick
from scoring.score_engine import trend_score

# 1. 漲跌停價要依升降單位取整：漲停往下取、跌停往上取（實際 2026/09/30 收盤資料）
assert tw_limit_prices(49.75) == (54.7, 44.8)      # 群創：54.725 → 54.7（50~100元跳 0.1）
assert tw_limit_prices(945) == (1035, 851)         # 環球晶：1039.5 → 1035（1000元以上跳 5）；富果標示今天收在漲停 1035
assert tw_limit_prices(2475) == (2720, 2230)       # 台積電
assert tw_limit_prices(9.5) == (10.45, 8.55)       # 漲停跨進 10 元以上：10.45 本身就是 0.05 的倍數
assert tw_limit_prices(98) == (107.5, 88.2)        # 107.8 → 100~500 元跳 0.5 → 107.5；88.2 本身就是 0.1 的倍數


def state(price, prev_close, ma5, ma20, ma60):
    t = Tick(symbol="T", name="T", ts=datetime(2026, 10, 1, 13, 0, tzinfo=TW_TZ), price=price,
             open=price, high=price, low=price, prev_close=prev_close, volume=0, turnover=0)
    up, down = tw_limit_prices(prev_close)
    return StockDailyState(symbol="T", name="T", tick=t, bars_1m=[], bars_5m=[],
                           ma5=ma5, ma20=ma20, ma60=ma60,
                           at_limit_up=price >= up, at_limit_down=price <= down)


# 2. 鎖漲停一整段（現價 = 三條均線 = 漲停價）：多方趨勢滿分、空方 0（以前兩邊都是 0）
s = state(54.7, 49.75, 54.7, 54.7, 54.7)
assert s.at_limit_up and not s.at_limit_down
assert (trend_score(s, "long"), trend_score(s, "short")) == (20.0, 0.0)

# 3. 鎖跌停：反過來
s = state(44.8, 49.75, 44.8, 44.8, 44.8)
assert s.at_limit_down
assert (trend_score(s, "long"), trend_score(s, "short")) == (0.0, 20.0)

# 4. 剛拉上漲停、離 MA5 很遠：一樣視為多頭排列，但追價風險扣分照舊（乖離 ≥ 5% 扣 8）
s = state(54.7, 49.75, 51.0, 50.5, 50.0)
assert trend_score(s, "long") == 12.0

# 5. 沒有碰到漲跌停：照原本規則，不受影響
s = state(52.0, 49.75, 52.0, 52.0, 52.0)
assert not s.at_limit_up and not s.at_limit_down
assert (trend_score(s, "long"), trend_score(s, "short")) == (0.0, 0.0)

print("=== PASS: 漲跌停價與鎖漲跌停趨勢判斷全部正確 ===")
