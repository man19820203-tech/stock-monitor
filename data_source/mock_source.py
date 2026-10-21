"""
模擬資料來源：在沒有券商 API 金鑰前，用隨機漫步 + 偶發量能脈衝，
產生「看起來像真的」的盤中 tick 流，讓後面的引擎/評分/劇本邏輯可以被完整測試。

正式上線時，把 main.py 的 provider 從 mock 換成 spark 或 fugle 即可，
下游所有程式碼不用改一行。
"""
import random
import threading
import time
from typing import Callable, List

from data_source.base import DataSourceBase
from market_time import now_tw
from models import Tick


class MockDataSource(DataSourceBase):
    def __init__(self, poll_interval_sec: float = 1.0):
        self.poll_interval_sec = poll_interval_sec
        self._running = False
        self._threads = {}          # symbol -> Thread
        self._symbol_running = {}   # symbol -> bool，個別關閉單一標的用（動態候選輪動測試用）
        self._state = {}   # symbol -> 內部模擬狀態

    def connect(self):
        print("[MockDataSource] 已連線（模擬模式，非真實行情）")

    def _init_symbol(self, symbol: str):
        prev_close = round(random.uniform(30, 600), 1)
        self._state[symbol] = {
            "prev_close": prev_close,
            "price": prev_close,
            "open": prev_close,
            "high": prev_close,
            "low": prev_close,
            "volume": 0,
            "turnover": 0.0,
            "inner_volume": 0,
            "outer_volume": 0,
            "bias": random.choice([1, 1, -1, -1, 0]),   # 今天的隱藏偏向，讓走勢有主題而非純雜訊
            "pulse_countdown": random.randint(20, 90),
        }

    def _tick_once(self, symbol: str) -> Tick:
        st = self._state[symbol]
        # 偶發量能脈衝：模擬突破/跌破時刻的放量
        st["pulse_countdown"] -= 1
        pulse = st["pulse_countdown"] <= 0
        if pulse:
            st["pulse_countdown"] = random.randint(60, 200)

        drift = st["bias"] * random.uniform(0, 0.15)
        noise = random.uniform(-0.3, 0.3)
        move_pct = drift + noise + (random.uniform(0.5, 1.2) if pulse else 0) * st["bias"]
        st["price"] = max(1.0, round(st["price"] * (1 + move_pct / 100), 2))

        st["high"] = max(st["high"], st["price"])
        st["low"] = min(st["low"], st["price"])

        vol_this_tick = random.randint(500, 3000) * (5 if pulse else 1)
        st["volume"] += vol_this_tick
        st["turnover"] += vol_this_tick * st["price"]

        # 內外盤：偏多天內盤外盤都會有，但外盤成交比例略高於內盤模擬主動買盤
        outer_ratio = 0.55 + (0.15 if st["bias"] > 0 else -0.1 if st["bias"] < 0 else 0)
        outer_ratio = min(max(outer_ratio, 0.2), 0.85)
        st["outer_volume"] += int(vol_this_tick * outer_ratio)
        st["inner_volume"] += int(vol_this_tick * (1 - outer_ratio))

        spread = round(st["price"] * 0.001, 2) or 0.01
        bid_prices = [round(st["price"] - spread * i, 2) for i in range(1, 6)]
        ask_prices = [round(st["price"] + spread * i, 2) for i in range(1, 6)]

        return Tick(
            symbol=symbol,
            name=symbol,
            ts=now_tw(),
            price=st["price"],
            open=st["open"],
            high=st["high"],
            low=st["low"],
            prev_close=st["prev_close"],
            volume=st["volume"],
            turnover=st["turnover"],
            bid_prices=bid_prices,
            bid_volumes=[random.randint(1, 50) for _ in range(5)],
            ask_prices=ask_prices,
            ask_volumes=[random.randint(1, 50) for _ in range(5)],
            inner_volume=st["inner_volume"],
            outer_volume=st["outer_volume"],
            is_disposition=False,
            is_attention=False,
            is_short_restricted=False,
        )

    def subscribe(self, symbols: List[str], on_tick: Callable[[Tick], None]):
        self._running = True

        def loop(symbol):
            while self._running and self._symbol_running.get(symbol):
                tick = self._tick_once(symbol)
                on_tick(tick)
                time.sleep(self.poll_interval_sec)

        for s in symbols:
            if s in self._symbol_running and self._symbol_running[s]:
                continue  # 已經在訂閱中，避免重複開執行緒
            self._init_symbol(s)
            self._symbol_running[s] = True
            t = threading.Thread(target=loop, args=(s,), daemon=True)
            t.start()
            self._threads[s] = t

    def unsubscribe(self, symbols: List[str]):
        """取消訂閱：把對應標的的模擬執行緒關掉並清掉狀態，供全市場掃描動態換股時使用。"""
        for s in symbols:
            self._symbol_running[s] = False
            self._threads.pop(s, None)
            self._state.pop(s, None)

    def get_index_quote(self) -> Tick:
        if "__INDEX__" not in self._state:
            self._init_symbol("__INDEX__")
            self._state["__INDEX__"]["prev_close"] = 22000.0
            self._state["__INDEX__"]["price"] = 22000.0
            self._state["__INDEX__"]["open"] = 22000.0
            self._state["__INDEX__"]["high"] = 22000.0
            self._state["__INDEX__"]["low"] = 22000.0
        return self._tick_once("__INDEX__")

    def disconnect(self):
        self._running = False
        print("[MockDataSource] 已中斷")
