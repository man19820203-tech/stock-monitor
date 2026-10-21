"""
離線煙霧測試：不依賴真實時鐘走到09:00，直接餵合成的 tick 序列，
驗證 first_range -> indicators -> scoring -> scenario -> narrator 整條管線不會噴例外，
且能夠產生出「強勢突破」之類的劇本判斷。
"""
import sys
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

from config_loader import load_config, get
from market_time import TW_TZ, now_tw
from models import Tick
from engine.first_range import FirstRangeTracker
from engine.indicators import IndicatorEngine
from engine.market_context import MarketContext, compute_group_strength
from scoring.score_engine import compute_scores
from scenario.scenario_engine import classify_scenario
from ai_narrator.narrator import narrate

load_config(force_reload=True)

symbol = "2408"
name = "南亞科"
prev_close = 500.0
start = now_tw().replace(hour=9, minute=0, second=0, microsecond=0)  # 用「今天」9:00，避免日期寫死導致觀察區間對不上

fr_tracker = FirstRangeTracker()
ind = IndicatorEngine()
mkt = MarketContext()
mkt.index_change_pct = -0.8  # 大盤走弱，個股逆勢走強 -> 應該加分

prices = []
# 第一觀察區間 09:00~09:10：價格在 495~505 間震盪
t = start
for i in range(11):
    price = 495 + (i % 5)
    prices.append((t, price, 200000 * (i + 1)))
    t += timedelta(minutes=1)

# 09:11 開始噴出，帶量突破 first_high(505)
for i in range(15):
    price = 506 + i * 1.2
    t += timedelta(seconds=30)
    prices.append((t, price, 3000000 + i * 600000))

states = {}
last_state = None
for ts, price, cum_vol in prices:
    tick = Tick(
        symbol=symbol, name=name, ts=ts, price=price,
        open=495.0, high=max(price, 505), low=min(price, 493),
        prev_close=prev_close, volume=cum_vol, turnover=cum_vol * price,
        inner_volume=int(cum_vol * 0.4), outer_volume=int(cum_vol * 0.6),
    )
    ind.update_bars(tick)
    fr_state = fr_tracker.process_tick(tick)
    state = ind.build_state(tick, fr_state, "記憶體", mkt.index_change_pct)
    states[symbol] = state
    last_state = state

group_strength = compute_group_strength(states, {"記憶體": [symbol]})
scores = compute_scores(last_state, group_strength, mkt.index_change_pct)
scenario_result = classify_scenario(last_state, mkt.index_change_pct, group_strength, get("liquidity.min_turnover_billion", 5))
narrative = narrate(last_state, scenario_result, scores)

print("=== 煙霧測試結果 ===")
print("first_high:", last_state.first_range.first_high, "first_low:", last_state.first_range.first_low)
print("broke_high_at:", last_state.first_range.broke_high_at)
print("high_after_break_held:", last_state.first_range.high_after_break_held)
print("scenario:", scenario_result["scenario"], scenario_result["signals_true"], "/", scenario_result["signals_total"])
print("long_score:", scores["long"]["total"], "short_score:", scores["short"]["total"])
print()
print(narrative)
print()
print("=== PASS: 完整管線無例外 ===")
