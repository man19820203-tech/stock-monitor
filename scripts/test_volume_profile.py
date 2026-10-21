"""
「今日量比昨日同期」測試：

    python scripts/test_volume_profile.py

不需要連線：用假K棒驗證
  1. 前一交易日 1 分K → 每分鐘累計量對照表
  2. 真實資料模式不會拿 data/history 裡的模擬 CSV 來比（只有 mock 模式會用）
  3. 對照表載入後，量比與量能分數正確
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config_loader import get
from engine.indicators import IndicatorEngine, build_volume_profile
from market_time import TW_TZ
from models import Bar


def bar(hh, mm, vol, day=29):
    return Bar(symbol="2330", interval_min=1, ts_open=datetime(2026, 9, day, hh, mm, tzinfo=TW_TZ),
               open=1, high=1, low=1, close=1, volume=vol)


# 1. 累計量；富果回傳是新到舊排序，要能處理
profile = build_volume_profile([bar(9, 2, 30), bar(9, 0, 100), bar(9, 1, 20)])
assert profile == {"09:00": 100, "09:01": 120, "09:02": 150}, profile
assert build_volume_profile([]) == {}

today = datetime(2026, 9, 30, 9, 1, 30, tzinfo=TW_TZ)

# 2. 真實資料模式（fugle）：data/history/2330.csv 是模擬資料，不能拿來比
cfg = get("data_source")
original = cfg["provider"]
try:
    cfg["provider"] = "fugle"
    eng = IndicatorEngine()
    assert eng.volume_ratio_vs_yesterday("2330", 240, today) is None, "fugle 模式不該讀模擬 CSV"

    # 3. 載入真實的前一交易日對照表後：09:01:30 今天累計 240 / 昨天同時段(09:01) 120 = 2.0 倍
    eng.set_yesterday_profile("2330", profile)
    assert eng.volume_ratio_vs_yesterday("2330", 240, today) == 2.0

    # mock 模式仍然用 CSV（mock 本來就是假資料，拿來測流程）
    cfg["provider"] = "mock"
    assert IndicatorEngine().volume_ratio_vs_yesterday("2330", 240, today) is not None
finally:
    cfg["provider"] = original

print("=== PASS: 今日量比昨日同期全部正確 ===")
