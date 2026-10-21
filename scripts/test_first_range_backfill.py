"""
第一高低「用當日1分K補回」測試：

    python scripts/test_first_range_backfill.py

不需要連線、不需要開盤，直接用假K棒/假tick驗證：
晚開、重開、掃描中途加入的股票，第一高低會用 09:00~09:10 的1分K補回正確值，
而不是退化成「開始追蹤當下的價格」。
（觀察區間用 config/settings.yaml 的 first_range 設定，預設 09:00 起 10 分鐘）
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.first_range import FirstRangeTracker
from market_time import now_tw, TW_TZ
from models import Bar, Tick

TODAY = now_tw().date()


def at(hh, mm, ss=0):
    return datetime(TODAY.year, TODAY.month, TODAY.day, hh, mm, ss, tzinfo=TW_TZ)


def bar(hh, mm, high, low, volume=100):
    return Bar(symbol="T", interval_min=1, ts_open=at(hh, mm),
               open=low, high=high, low=low, close=high, volume=volume)


def tick(ts, price):
    return Tick(symbol="T", name="T", ts=ts, price=price, open=price, high=price,
                low=price, prev_close=price, volume=1000, turnover=0.0)


# 09:00~09:09 的K棒：真正的第一高 105（09:03）、第一低 98（09:07）；09:10 之後的K棒不算
BARS = [bar(9, m, 100 + (5 if m == 3 else 1), 100 - (2 if m == 7 else 1)) for m in range(10)]
BARS += [bar(9, 10, 120, 90), bar(9, 11, 121, 89)]

# 1. 晚開（10:00 才開始追蹤）：先來的 tick 會退化成當下價格，補K棒後要被正確值取代
t = FirstRangeTracker()
fr = t.process_tick(tick(at(10, 0), 110))
assert (fr.first_high, fr.first_low) == (110, 110), "前提：沒補之前會退化成當下價格"
assert t.seed_from_bars("T", BARS, now=at(10, 0, 5))
fr = t.get_state("T")
assert fr.formed
assert (fr.first_high, fr.first_low) == (105, 98), (fr.first_high, fr.first_low)
assert fr.first_high_time == at(9, 3) and fr.first_low_time == at(9, 7)
# 退化值造成的突破判定要一起清掉，之後依正確基準重新判斷
assert fr.broke_high_at is None and fr.broke_low_at is None

# 2. 補完後，突破判定用正確的第一高（105 * 1.003 = 105.315）
fr = t.process_tick(tick(at(10, 1), 106))
assert fr.broke_high_at == at(10, 1), fr.broke_high_at

# 3. 先補K棒、之後 tick 才來：不能再被退化邏輯覆蓋
t = FirstRangeTracker()
assert t.seed_from_bars("T", BARS, now=at(10, 0))
fr = t.process_tick(tick(at(10, 0, 1), 100))
assert (fr.first_high, fr.first_low) == (105, 98)

# 4. 觀察區間中途才開（09:05）：K棒補前半段，之後的 tick 繼續更新，區間結束才形成
t = FirstRangeTracker()
assert t.seed_from_bars("T", [b for b in BARS if b.ts_open < at(9, 5)], now=at(9, 5, 10))
fr = t.get_state("T")
assert not fr.formed and (fr.first_high, fr.first_low) == (105, 99)
t.process_tick(tick(at(9, 7), 97))
fr = t.process_tick(tick(at(9, 10, 1), 100))
assert fr.formed and (fr.first_high, fr.first_low) == (105, 97)

# 5. 區間內已有真實 tick 時，補K棒是「合併」而不是覆蓋（tick 看到的極值不會被丟掉）
t = FirstRangeTracker()
t.process_tick(tick(at(9, 8), 106))
t.seed_from_bars("T", BARS, now=at(9, 8, 30))
fr = t.get_state("T")
assert (fr.first_high, fr.first_low) == (106, 98)

# 6. 沒有區間內的K棒（例如09:00前開、或API沒回資料）：什麼都不改，回傳 False
t = FirstRangeTracker()
fr = t.process_tick(tick(at(10, 0), 110))
assert not t.seed_from_bars("T", [], now=at(10, 0))
assert not t.seed_from_bars("T", [bar(9, 30, 200, 1)], now=at(10, 0))
assert (fr.first_high, fr.first_low) == (110, 110)

print("=== PASS: 第一高低K棒回補全部正確 ===")
