"""
均線用K棒「用當日1分K補回」測試：

    python scripts/test_bar_backfill.py

不需要連線：晚開 / 中途加入的股票，開始追蹤時用當日1分K把已經走過的K棒補回，
MA5/20/60、5分K 不用重新累積 5/20/60 分鐘。
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.indicators import IndicatorEngine
from market_time import TW_TZ
from models import Bar, Tick


def at(hh, mm, ss=0):
    return datetime(2026, 10, 1, hh, mm, ss, tzinfo=TW_TZ)


def bar(hh, mm, close, vol=10):
    return Bar(symbol="T", interval_min=1, ts_open=at(hh, mm), open=close, high=close + 1,
               low=close - 1, close=close, volume=vol)


def tick(ts, price, cum_vol):
    return Tick(symbol="T", name="T", ts=ts, price=price, open=price, high=price, low=price,
                prev_close=price, volume=cum_vol, turnover=0.0)


# 09:00~10:04 共 65 根，收盤價 100,101,...,164；富果回傳可能是新到舊
CANDLES = [bar(9 + (m // 60), m % 60, 100 + m) for m in range(65)][::-1]

# 1. 10:04:30 晚開：只補「已經收完」的K棒（10:04 這根還在走，不能補）
eng = IndicatorEngine()
assert eng.moving_average("T", 60) is None, "前提：沒補之前 MA60 算不出來"
n = eng.seed_bars("T", CANDLES, now=at(10, 4, 30))
assert n == 64, n
bars = eng.get_bars_1m("T", include_current=False)
assert [b.ts_open for b in bars] == sorted(b.ts_open for b in bars), "要依時間排序"
assert bars[-1].ts_open == at(10, 3)
assert eng.moving_average("T", 5) == 161.0      # 159..163 平均
assert eng.moving_average("T", 60) == 133.5     # 104..163 平均
# 5分K：09:00~10:00 共 13 根完整的（10:00~10:04 這組還沒收完）
b5 = eng.get_bars_5m("T")
assert len(b5) == 12 and b5[0].ts_open == at(9, 0) and b5[-1].ts_open == at(9, 55), [b.ts_open for b in b5]
assert (b5[0].open, b5[0].close, b5[0].high, b5[0].low, b5[0].volume) == (100, 104, 105, 99, 50)

# 2. 補完後即時 tick 接著走：10:04 開新K棒，10:05 收掉 10:04
eng.update_bars(tick(at(10, 4, 40), 170, 1000))
eng.update_bars(tick(at(10, 5, 1), 171, 1050))
bars = eng.get_bars_1m("T", include_current=False)
assert bars[-1].ts_open == at(10, 4) and bars[-1].close == 170 and len(bars) == 65

# 3. 已經有即時K棒（09:30 才開始收 tick）再補：收完的分鐘以1分K為準（即時第一根只收到半分鐘，量不完整），
#    不重複、不遺漏
eng = IndicatorEngine()
eng.update_bars(tick(at(9, 30, 30), 999, 500))
eng.update_bars(tick(at(9, 31, 5), 131, 520))
eng.seed_bars("T", CANDLES, now=at(9, 31, 10))
bars = eng.get_bars_1m("T", include_current=False)
assert len(bars) == 31 and bars[-1].ts_open == at(9, 30), len(bars)
assert bars[-1].close == 130 and bars[-1].volume == 10, "09:30 要用1分K的完整資料"
assert eng.get_bars_1m("T")[-1].ts_open == at(9, 31), "進行中的 09:31 保留即時資料"

# 4. 比最後一根K棒還舊的報價（例如收盤後查到 13:30 的報價）不能再開一根重複的K棒
eng = IndicatorEngine()
eng.seed_bars("T", [bar(13, 29, 50), bar(13, 30, 51)], now=at(18, 0))
eng.update_bars(tick(at(13, 30), 51, 999))
assert [b.ts_open for b in eng.get_bars_1m("T")] == [at(13, 29), at(13, 30)]

# 5. 被掃描輪出後再輪入：輪出前留下的舊「進行中」K棒不能在補完後被收到尾端（順序會亂）
eng = IndicatorEngine()
eng.update_bars(tick(at(9, 10, 20), 110, 100))          # 09:10 進行中時被輪出
eng.seed_bars("T", CANDLES, now=at(9, 40, 5))           # 09:40 再輪入並補K棒
eng.update_bars(tick(at(9, 40, 30), 140, 900))
eng.update_bars(tick(at(9, 41, 1), 141, 950))
ts = [b.ts_open for b in eng.get_bars_1m("T", include_current=False)]
assert ts == sorted(ts) and len(ts) == len(set(ts)) and ts[-1] == at(9, 40), ts[-3:]

# 6. 沒有K棒可以補
assert IndicatorEngine().seed_bars("T", [], now=at(10, 0)) == 0

print("=== PASS: 均線K棒回補全部正確 ===")
