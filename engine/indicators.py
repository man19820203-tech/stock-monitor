"""
確定性技術指標計算引擎：1分K / 5分K、MA5/20/60、量能倍數、內外盤比、
距漲跌停距離、今日量比昨日同期。

原則（呼應你的需求）：所有價格/量的數字都是程式算出來的，
AI 完全不碰這裡的計算，只在後面拿「已經算好的結果」做文字解讀。
"""
import csv
import os
import threading
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path
from typing import Optional

from config_loader import get
from models import Tick, Bar, StockDailyState

HISTORY_DIR = Path(__file__).parent.parent / "data" / "history"


def tw_tick_size(price: Decimal) -> Decimal:
    """台股（上市櫃股票）升降單位。"""
    if price < 10:
        return Decimal("0.01")
    if price < 50:
        return Decimal("0.05")
    if price < 100:
        return Decimal("0.1")
    if price < 500:
        return Decimal("0.5")
    if price < 1000:
        return Decimal("1")
    return Decimal("5")


def tw_limit_prices(prev_close: float) -> tuple:
    """依昨收算出實際的漲停價、跌停價：±limit_pct（config limit.limit_up_pct / limit_down_pct，預設10%）
    之後依升降單位取整——漲停往下取、跌停往上取（不能超過 ±10%）。
    例：群創昨收 49.75 → 54.725 → 50~100 元跳 0.1 → 漲停 54.7（不是四捨五入的 54.73）。
    用 Decimal 算，避免浮點誤差把剛好整除的價格取錯一格。"""
    prev = Decimal(str(prev_close))
    up_raw = prev * (1 + Decimal(str(get("limit.limit_up_pct", 10.0))) / 100)
    down_raw = prev * (1 - Decimal(str(get("limit.limit_down_pct", 10.0))) / 100)
    up_tick, down_tick = tw_tick_size(up_raw), tw_tick_size(down_raw)
    up = (up_raw / up_tick).to_integral_value(rounding=ROUND_FLOOR) * up_tick
    down = (down_raw / down_tick).to_integral_value(rounding=ROUND_CEILING) * down_tick
    return float(up), float(down)


def build_volume_profile(bars: list) -> dict:
    """前一交易日 1 分K → {"HH:MM": 截至該分鐘（含）的累計成交量}，給「今日量比昨日同期」用。
    K棒順序不拘（富果 historical 回傳是新到舊）。量的單位跟即時報價的累計量一致（張）。"""
    profile = {}
    cum = 0
    for b in sorted(bars, key=lambda b: b.ts_open):
        cum += b.volume
        profile[b.ts_open.strftime("%H:%M")] = cum
    return profile


class IndicatorEngine:
    def __init__(self):
        self._bars_1m: dict[str, list[Bar]] = {}
        self._cur_1m: dict[str, dict] = {}
        self._bars_5m: dict[str, list[Bar]] = {}
        self._yesterday_profile_cache: dict[str, dict] = {}
        # 即時 tick（報價執行緒）跟 seed_bars（暖機執行緒）會同時改同一檔的K棒
        self._lock = threading.RLock()

    # ---------------- K棒建構 ----------------
    def seed_bars(self, symbol: str, bars: list, now: datetime) -> int:
        """用當日 1 分K 補回「開始追蹤以前」已經走完的K棒（晚開、重開、掃描中途加入的股票），
        MA5/20/60、5分K 就不用開始追蹤後再重新累積 5/20/60 分鐘。回傳補進幾根。

        - 只補已經收完的分鐘（< now 所在的分鐘）；還在走的那一分鐘交給即時 tick
        - 已經收完的分鐘以 1 分K 為準：即時收到的第一根通常只有半分鐘，量不完整
        - 5分K 依補完的 1 分K 重建，只建完整走完的 5 分鐘區段
        """
        now_min = now.replace(second=0, microsecond=0)
        seeded = {b.ts_open: b for b in bars if b.ts_open < now_min}
        if not seeded:
            return 0
        with self._lock:
            cur = self._cur_1m.get(symbol)
            merged = {b.ts_open: b for b in self._bars_1m.get(symbol, [])}
            merged.update(seeded)
            if cur is not None:
                if cur["ts_open"] < now_min:
                    # 舊的「進行中」K棒（例如被掃描輪出前留下的）：那一分鐘早就走完、1分K裡有完整資料，
                    # 留著的話下一筆 tick 會把它收進K棒尾端，順序就亂了
                    del self._cur_1m[symbol]
                else:
                    merged.pop(cur["ts_open"], None)
            self._bars_1m[symbol] = [merged[k] for k in sorted(merged)]
            self._rebuild_5m(symbol, now_min)
        return len(seeded)

    def _rebuild_5m(self, symbol: str, now_min: datetime):
        groups: dict[datetime, list[Bar]] = {}
        for b in self._bars_1m.get(symbol, []):
            key = b.ts_open.replace(minute=(b.ts_open.minute // 5) * 5)
            groups.setdefault(key, []).append(b)
        bars5 = []
        for key in sorted(groups):
            if key + timedelta(minutes=5) > now_min:
                continue   # 這 5 分鐘還沒走完
            g = groups[key]
            bars5.append(Bar(symbol=symbol, interval_min=5, ts_open=key, open=g[0].open,
                             high=max(b.high for b in g), low=min(b.low for b in g),
                             close=g[-1].close, volume=sum(b.volume for b in g)))
        self._bars_5m[symbol] = bars5

    def update_bars(self, tick: Tick):
        with self._lock:
            self._update_bars(tick)

    def _update_bars(self, tick: Tick):
        symbol = tick.symbol
        bucket = tick.ts.replace(second=0, microsecond=0)
        closed = self._bars_1m.get(symbol)
        if closed and bucket <= closed[-1].ts_open:
            # 比已收完的最後一根還舊的報價（例如收盤後查到 13:30 的報價，而 13:30 已由1分K補好），
            # 不能再開一根重複的K棒
            return
        cur = self._cur_1m.get(symbol)

        if cur is None or cur["ts_open"] != bucket:
            if cur is not None:
                self._close_bar(symbol, cur)
            cur = {
                "ts_open": bucket,
                "open": tick.price,
                "high": tick.price,
                "low": tick.price,
                "close": tick.price,
                "vol_open_cum": tick.volume,
                "vol_last_cum": tick.volume,
            }
            self._cur_1m[symbol] = cur
        else:
            cur["high"] = max(cur["high"], tick.price)
            cur["low"] = min(cur["low"], tick.price)
            cur["close"] = tick.price
            cur["vol_last_cum"] = tick.volume

        self._maybe_build_5m(symbol)

    def _close_bar(self, symbol: str, cur: dict):
        bar = Bar(
            symbol=symbol,
            interval_min=1,
            ts_open=cur["ts_open"],
            open=cur["open"],
            high=cur["high"],
            low=cur["low"],
            close=cur["close"],
            volume=max(0, cur["vol_last_cum"] - cur["vol_open_cum"]),
        )
        self._bars_1m.setdefault(symbol, []).append(bar)

    def _maybe_build_5m(self, symbol: str):
        bars_1m = self._bars_1m.get(symbol, [])
        if len(bars_1m) < 5:
            return
        last5 = bars_1m[-5:]
        bucket5 = last5[0].ts_open.replace(
            minute=(last5[0].ts_open.minute // 5) * 5
        )
        existing = self._bars_5m.setdefault(symbol, [])
        if existing and existing[-1].ts_open == bucket5:
            return
        if last5[0].ts_open.minute % 5 != 0:
            return
        bar5 = Bar(
            symbol=symbol,
            interval_min=5,
            ts_open=bucket5,
            open=last5[0].open,
            high=max(b.high for b in last5),
            low=min(b.low for b in last5),
            close=last5[-1].close,
            volume=sum(b.volume for b in last5),
        )
        existing.append(bar5)

    def get_bars_1m(self, symbol: str, include_current: bool = True) -> list[Bar]:
        bars = list(self._bars_1m.get(symbol, []))
        if include_current and symbol in self._cur_1m:
            cur = self._cur_1m[symbol]
            bars = bars + [Bar(
                symbol=symbol, interval_min=1, ts_open=cur["ts_open"],
                open=cur["open"], high=cur["high"], low=cur["low"], close=cur["close"],
                volume=max(0, cur["vol_last_cum"] - cur["vol_open_cum"]),
            )]
        return bars

    def get_bars_5m(self, symbol: str) -> list[Bar]:
        return list(self._bars_5m.get(symbol, []))

    # ---------------- 移動平均 ----------------
    def moving_average(self, symbol: str, period: int) -> Optional[float]:
        bars = self.get_bars_1m(symbol, include_current=True)
        if len(bars) < period:
            return None
        closes = [b.close for b in bars[-period:]]
        return round(sum(closes) / len(closes), 2)

    # ---------------- 量能倍數（最近1分鐘 vs 近N分鐘均量）----------------
    def recent_volume_multiple(self, symbol: str) -> float:
        lookback = get("indicators.volume_avg_lookback_min", 5)
        bars = self._bars_1m.get(symbol, [])
        if len(bars) < lookback + 1:
            return 0.0
        recent_bar = bars[-1]
        base_bars = bars[-(lookback + 1):-1]
        avg_vol = sum(b.volume for b in base_bars) / len(base_bars)
        if avg_vol <= 0:
            return 0.0
        return round(recent_bar.volume / avg_vol, 2)

    # ---------------- 今日量比昨日同期 ----------------
    def has_yesterday_profile(self, symbol: str) -> bool:
        return bool(self._yesterday_profile_cache.get(symbol))

    def set_yesterday_profile(self, symbol: str, profile: dict):
        """真實資料模式：Orchestrator 開始追蹤一檔股票時，用前一交易日 1 分K 算好對照表放進來
        （見 build_volume_profile / main.py _load_volume_profile）。"""
        self._yesterday_profile_cache[symbol] = profile

    def _load_yesterday_profile(self, symbol: str) -> Optional[dict]:
        """優先用 set_yesterday_profile 放進來的真實對照表。
        mock 模式才讀 data/history/{symbol}.csv（scripts/gen_mock_history.py 產生的模擬資料）——
        真實資料模式絕對不能拿模擬 CSV 跟真實成交量比，那會算出假的量比、假的量能分數。
        都沒有時回傳 None，量能分數給 0，而不是編造數字。"""
        if symbol in self._yesterday_profile_cache:
            return self._yesterday_profile_cache[symbol]
        if get("data_source.provider", "mock") != "mock":
            return None
        path = HISTORY_DIR / f"{symbol}.csv"
        if not path.exists():
            return None
        profile = {}
        with open(path, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                profile[row["time"]] = int(row["cum_volume"])
        self._yesterday_profile_cache[symbol] = profile
        return profile

    def volume_ratio_vs_yesterday(self, symbol: str, current_cum_volume: int, now: datetime) -> Optional[float]:
        profile = self._load_yesterday_profile(symbol)
        if not profile:
            return None
        hhmm = now.strftime("%H:%M")
        # 找 <= 現在時間的最近一筆
        keys = sorted(k for k in profile if k <= hhmm)
        if not keys:
            return None
        yesterday_vol = profile[keys[-1]]
        if yesterday_vol <= 0:
            return None
        return round(current_cum_volume / yesterday_vol, 2)

    # ---------------- 內外盤比 ----------------
    @staticmethod
    def inner_outer_ratio(tick: Tick) -> dict:
        total = tick.inner_volume + tick.outer_volume
        if total == 0:
            return {"inner_pct": 0.0, "outer_pct": 0.0}
        return {
            "inner_pct": round(tick.inner_volume / total * 100, 1),
            "outer_pct": round(tick.outer_volume / total * 100, 1),
        }

    # ---------------- 距漲跌停 ----------------
    @staticmethod
    def dist_to_limit(tick: Tick) -> dict:
        if not tick.prev_close:
            return {"limit_up_price": None, "limit_down_price": None,
                    "dist_to_limit_up_pct": None, "dist_to_limit_down_pct": None,
                    "at_limit_up": False, "at_limit_down": False}
        limit_up_price, limit_down_price = tw_limit_prices(tick.prev_close)
        dist_up = round((limit_up_price - tick.price) / tick.price * 100, 2) if tick.price else None
        dist_down = round((tick.price - limit_down_price) / tick.price * 100, 2) if tick.price else None
        return {
            "limit_up_price": limit_up_price,
            "limit_down_price": limit_down_price,
            "dist_to_limit_up_pct": dist_up,
            "dist_to_limit_down_pct": dist_down,
            "at_limit_up": bool(tick.price) and tick.price >= limit_up_price,
            "at_limit_down": bool(tick.price) and tick.price <= limit_down_price,
        }

    def build_state(self, tick: Tick, first_range_state, group: Optional[str],
                     index_change_pct: Optional[float]) -> StockDailyState:
        """把所有算好的東西組成一份 StockDailyState，交給 scoring / scenario 使用。"""
        change_pct = round((tick.price - tick.prev_close) / tick.prev_close * 100, 2) if tick.prev_close else 0.0
        limit_info = self.dist_to_limit(tick)
        vol_ratio = self.volume_ratio_vs_yesterday(tick.symbol, tick.volume, tick.ts)

        state = StockDailyState(
            symbol=tick.symbol,
            name=tick.name,
            tick=tick,
            bars_1m=self.get_bars_1m(tick.symbol),
            bars_5m=self.get_bars_5m(tick.symbol),
            ma5=self.moving_average(tick.symbol, get("indicators.ma_periods", [5, 20, 60])[0]),
            ma20=self.moving_average(tick.symbol, get("indicators.ma_periods", [5, 20, 60])[1]),
            ma60=self.moving_average(tick.symbol, get("indicators.ma_periods", [5, 20, 60])[2]),
            first_range=first_range_state,
            day_high=tick.high,
            day_low=tick.low,
            change_pct=change_pct,
            turnover_billion=round(tick.turnover / 1e8, 2),
            volume_ratio_vs_yesterday=vol_ratio if vol_ratio is not None else 0.0,
            volume_multiple_recent=self.recent_volume_multiple(tick.symbol),
            group=group,
            relative_strength_pct=(round(change_pct - index_change_pct, 2)
                                    if index_change_pct is not None else None),
            dist_to_limit_up_pct=limit_info["dist_to_limit_up_pct"],
            dist_to_limit_down_pct=limit_info["dist_to_limit_down_pct"],
            at_limit_up=limit_info["at_limit_up"],
            at_limit_down=limit_info["at_limit_down"],
        )
        return state
