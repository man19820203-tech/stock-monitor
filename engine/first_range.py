"""
第一波高低點偵測引擎 —— 系統中最重要的模組之一。

規則（全部從 config 讀取，不寫死）：
  1. 09:00~09:00+duration_minutes 形成「第一觀察區間」（預設10分鐘，可調 5/10/15）
  2. 區間結束後鎖定 first_high / first_low，含當下的量與時間
  3. 之後價格向上超越 first_high * (1 + breakout_min_pct%) 視為「突破」事件，記錄時間與價格
  4. 突破後 fakeout_check_seconds 秒內，若價格又跌回 first_high 以下，
     判定為「未站穩 / 假突破嫌疑」；若期間內都沒跌破，判定為「站穩」
  5. 對稱邏輯套用在 first_low / 跌破

這一層只做「確定性事件偵測」，不做任何解讀或加分，
解讀交給 scenario / breakout_quality，加分交給 scoring。

晚開 / 重開 / 掃描中途才加入的股票：
  觀察區間內沒收到 tick 時，第 1 點只能退化成「開始追蹤當下的價格」，後面所有
  突破判斷都會用錯基準。所以開始追蹤時，main.py 會另外抓當日 1 分K，
  呼叫 seed_from_bars() 把 09:00~09:10 的真正高低補回來（見 main.py _backfill_first_range）。
"""
import threading
from datetime import datetime, timedelta, time as dtime

from config_loader import get
from market_time import now_tw, TW_TZ
from models import Bar, Tick, FirstRangeState


def _parse_hhmm(s: str) -> dtime:
    h, m = s.split(":")
    return dtime(int(h), int(m))


class FirstRangeTracker:
    def __init__(self):
        self._states: dict[str, FirstRangeState] = {}
        # 內部輔助狀態，不放進 dataclass 避免污染對外資料結構
        self._extra: dict[str, dict] = {}
        # process_tick 跑在報價執行緒、seed_from_bars 跑在回補執行緒，兩邊會改同一份狀態
        self._lock = threading.RLock()

    def _cfg(self):
        return {
            "start_time": _parse_hhmm(get("first_range.start_time", "09:00")),
            "duration_minutes": get("first_range.duration_minutes", 10),
            "confirm_hold_seconds": get("first_range.confirm_hold_seconds", 30),
            "breakout_min_pct": get("first_range.breakout_min_pct", 0.3),
            "fakeout_check_seconds": get("first_range.fakeout_check_seconds", 90),
        }

    def get_state(self, symbol: str) -> FirstRangeState:
        if symbol not in self._states:
            cfg = self._cfg()
            today = now_tw().date()
            window_start = datetime.combine(today, cfg["start_time"], tzinfo=TW_TZ)
            window_end = window_start + timedelta(minutes=cfg["duration_minutes"])
            self._states[symbol] = FirstRangeState(
                symbol=symbol,
                window_start=window_start.time(),
                window_end=window_end.time(),
            )
            self._extra[symbol] = {
                "window_start_dt": window_start,
                "window_end_dt": window_end,
                "min_since_break_high": None,
                "max_since_break_low": None,
                "degraded": False,   # 第一高低是否為「區間內沒tick、退化用當下價格」的替代值
            }
        return self._states[symbol]

    def seed_from_bars(self, symbol: str, bars: list[Bar], now: datetime = None) -> bool:
        """用當日 1 分K 補回觀察區間的第一高低。回傳是否有補到（區間內沒有K棒就什麼都不改）。

        - 目前的第一高低是退化值（或還沒有值）→ 直接用K棒結果取代，並清掉用錯誤基準
          算出來的突破/跌破判定，之後的 tick 會依正確基準重新判斷
        - 區間內已經有真實 tick → 跟K棒取聯集（取較高的高、較低的低），不丟掉 tick 看到的極值
        - 觀察區間已結束 → 直接標記 formed；還沒結束 → 之後的 tick 繼續更新，區間結束時才形成
        """
        with self._lock:
            state = self.get_state(symbol)
            extra = self._extra[symbol]
            in_window = [b for b in bars
                         if extra["window_start_dt"] <= b.ts_open < extra["window_end_dt"]]
            if not in_window:
                return False

            in_window.sort(key=lambda b: b.ts_open)
            hi_bar = max(in_window, key=lambda b: b.high)   # 同價取最早出現的那根
            lo_bar = min(in_window, key=lambda b: b.low)
            cum_volume = {}
            running = 0
            for b in in_window:
                running += b.volume
                cum_volume[b.ts_open] = running

            replace = extra["degraded"] or state.first_high is None
            if replace or hi_bar.high > state.first_high:
                state.first_high = hi_bar.high
                state.first_high_time = hi_bar.ts_open
                state.first_high_volume = cum_volume[hi_bar.ts_open]
            if replace or lo_bar.low < state.first_low:
                state.first_low = lo_bar.low
                state.first_low_time = lo_bar.ts_open
                state.first_low_volume = cum_volume[lo_bar.ts_open]

            if extra["degraded"]:
                state.broke_high_at = state.broke_high_price = state.high_after_break_held = None
                state.broke_low_at = state.broke_low_price = state.low_after_break_held = None
                extra["min_since_break_high"] = extra["max_since_break_low"] = None
                extra["degraded"] = False

            if (now or now_tw()) >= extra["window_end_dt"]:
                state.formed = True
            return True

    def process_tick(self, tick: Tick) -> FirstRangeState:
        with self._lock:
            return self._process_tick(tick)

    def _process_tick(self, tick: Tick) -> FirstRangeState:
        cfg = self._cfg()
        state = self.get_state(tick.symbol)
        extra = self._extra[tick.symbol]
        now = tick.ts

        # --- 階段一：形成第一觀察區間 ---
        if now < extra["window_end_dt"]:
            if now >= extra["window_start_dt"]:
                if state.first_high is None or tick.price > state.first_high:
                    state.first_high = tick.price
                    state.first_high_volume = tick.volume
                    state.first_high_time = now
                if state.first_low is None or tick.price < state.first_low:
                    state.first_low = tick.price
                    state.first_low_volume = tick.volume
                    state.first_low_time = now
            return state

        if not state.formed:
            state.formed = True
            if state.first_high is None:
                # 區間內完全沒有 tick（例如剛開盤資料延遲、晚開），退化用目前價格；
                # 回補執行緒拿到K棒後會用 seed_from_bars() 取代這個替代值
                state.first_high = tick.price
                state.first_low = tick.price
                extra["degraded"] = True

        # --- 階段二：偵測突破 / 跌破 ---
        if state.first_high is not None and state.broke_high_at is None:
            threshold = state.first_high * (1 + cfg["breakout_min_pct"] / 100)
            if tick.price > threshold:
                state.broke_high_at = now
                state.broke_high_price = tick.price
                extra["min_since_break_high"] = tick.price

        if state.first_low is not None and state.broke_low_at is None:
            threshold = state.first_low * (1 - cfg["breakout_min_pct"] / 100)
            if tick.price < threshold:
                state.broke_low_at = now
                state.broke_low_price = tick.price
                extra["max_since_break_low"] = tick.price

        # --- 階段三：站穩 / 假突破判定 ---
        if state.broke_high_at is not None:
            extra["min_since_break_high"] = min(extra["min_since_break_high"], tick.price)
            elapsed = (now - state.broke_high_at).total_seconds()
            if elapsed >= cfg["fakeout_check_seconds"] and state.high_after_break_held is None:
                state.high_after_break_held = extra["min_since_break_high"] >= state.first_high
            elif state.high_after_break_held is None:
                # 觀察期間內若已經跌破第一高，提前判定未站穩
                if tick.price < state.first_high:
                    state.high_after_break_held = False

        if state.broke_low_at is not None:
            extra["max_since_break_low"] = max(extra["max_since_break_low"], tick.price)
            elapsed = (now - state.broke_low_at).total_seconds()
            if elapsed >= cfg["fakeout_check_seconds"] and state.low_after_break_held is None:
                state.low_after_break_held = extra["max_since_break_low"] <= state.first_low
            elif state.low_after_break_held is None:
                if tick.price > state.first_low:
                    state.low_after_break_held = False

        # --- 今日創高/破低（獨立於第一波，只看目前價是否等於當日最高/最低）---
        state.made_new_high_today = tick.price >= tick.high
        state.made_new_low_today = tick.price <= tick.low

        return state

    def reset_daily(self):
        """收盤後或跨日呼叫，清空所有狀態重新開始。"""
        with self._lock:
            self._states.clear()
            self._extra.clear()
