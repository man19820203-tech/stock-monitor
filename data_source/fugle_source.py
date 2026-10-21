"""
富果（Fugle）行情 API adapter —— 已依官方文件（developer.fugle.tw）串接完成。

申請步驟：
  1. 到 https://developer.fugle.tw/ 註冊「富果會員」（不需要富果證券開戶）
  2. 到 https://developer.fugle.tw/docs/key/ 申請 API Key
     免費方案：WebSocket 即時報價 5檔訂閱數／1連線數，60次/分鐘 REST 呼叫
     （對這套系統的預設 watchlist 5檔股票剛好卡在免費額度邊緣，
      如果之後要監控更多檔，需要升級付費方案，見 developer.fugle.tw/docs/pricing）
  3. pip install fugle-marketdata
  4. 把 API Key 填入 config/secrets.yaml -> data_source.fugle.api_token（雲端用環境變數 FUGLE_API_TOKEN）
  5. 把 config/settings.yaml -> data_source.provider 改成 "fugle"

技術細節：
  - 個股即時資料使用 WebSocket 的 "aggregates" 頻道（一個頻道就包含開高低收、
    昨收、五檔買賣、累計成交量額、內外盤量，不需要同時訂閱多個頻道）
  - 大盤指數（加權指數 IX0001）用 REST API 定期輪詢（大盤沒有五檔/內外盤的必要，
    用 WebSocket 反而浪費訂閱額度）
  - 處置股/注意股/禁空旗標目前免費方案的 aggregates 頻道沒有提供，
    先預設 False；若需要這幾個欄位，可以另外呼叫 REST 的
    intraday/ticker 端點補齊（TODO，見下方 _enrich_flags）
"""
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, List

from config_loader import get
from data_source.base import DataSourceBase
from market_time import TW_TZ
from models import Bar, Tick


def _us_to_datetime(us: int) -> datetime:
    """Fugle 的時間戳是 Unix epoch 微秒(microseconds)，轉成台北時間。"""
    if not us:
        return datetime.now(TW_TZ)
    return datetime.fromtimestamp(us / 1_000_000, tz=timezone.utc).astimezone(TW_TZ)


class SharedRestRateLimiter:
    """整個系統所有會打富果REST API的地方（大盤指數輪詢 poll_index、
    全市場掃描 market_scanner）都必須共用「同一個」計數器，而不是各自維護
    自己的限速計數——不然會發生：掃描器自己算「我這分鐘打了50次，還沒到55次上限」，
    但同一分鐘內大盤輪詢又另外打了20次，兩邊加起來其實已經超過富果帳號
    真正的60次/分鐘上限，最後被富果那邊回傳 429 Rate limit exceeded。

    這個class被 FugleDataSource 持有一份，get_index_quote() 和
    market_scanner.py 都透過 ds.rate_limiter.wait() 過這同一關，
    上限用 universe.scan.rest_calls_per_minute 這組設定（全系統共用一個總額度）。

    實作方式是「平均分散間隔」而不是「先衝額度、用完再等」：
    例如上限55次/分鐘，就是每次呼叫間至少間隔 60/55 ≈ 1.09 秒才發下一個請求，
    而不是讓55次請求在幾秒內全部連續打出去、額度用完才開始等。
    這是實測後才發現的重要教訓：富果的伺服器除了「每分鐘60次」的總量限制，
    另外還有更嚴格的「瞬間短時間內太密集」限制——就算沒超過每分鐘總額度，
    只要連續發送的間隔太短，一樣會被回傳 429 Rate limit exceeded
    （曾發生過連前3、4次請求都被拒絕的實際案例，就是因為舊版限速器允許
    請求幾乎無間隔地連續發出）。平均分散間隔可以同時滿足兩種限制。
    """
    def __init__(self):
        self._lock = threading.Lock()
        self._last_call_at: float = 0.0

    def wait(self):
        limit = get("universe.scan.rest_calls_per_minute", 55)
        min_interval = 60.0 / max(limit, 1)
        with self._lock:
            now = time.time()
            elapsed = now - self._last_call_at
            sleep_for = min_interval - elapsed
            if sleep_for > 0:
                time.sleep(sleep_for)
            self._last_call_at = time.time()


class FugleDataSource(DataSourceBase):
    def __init__(self, api_token: str):
        self.api_token = api_token
        self._ws_client = None
        self._rest_client = None
        self._stock_ws = None
        self._name_cache: dict[str, str] = {}
        self.rate_limiter = SharedRestRateLimiter()
        self._ws_connected = False
        self._on_tick = None
        self._subscribe_lock = threading.Lock()

    def connect(self):
        if not self.api_token:
            raise RuntimeError(
                "尚未設定富果 API token。本機請填在 config/secrets.yaml 的 "
                "data_source.fugle.api_token（GitHub Actions 請設 Secrets 的 FUGLE_API_TOKEN），"
                "或先把 data_source.provider "
                "改回 mock 測試。申請入口：https://developer.fugle.tw/docs/key/"
            )
        try:
            from fugle_marketdata import RestClient, WebSocketClient
        except ImportError as e:
            raise RuntimeError(
                "缺少 fugle-marketdata 套件，請先執行：pip install fugle-marketdata"
            ) from e

        self._rest_client = RestClient(api_key=self.api_token)
        self._ws_client = WebSocketClient(api_key=self.api_token)
        self._stock_ws = self._ws_client.stock
        print("[FugleDataSource] 已建立連線物件（實際連線在 subscribe() 時開始）")

    def _parse_aggregate_to_tick(self, data: dict) -> Tick:
        total = data.get("total", {}) or {}
        bids = data.get("bids", []) or []
        asks = data.get("asks", []) or []
        symbol = data["symbol"]
        name = data.get("name") or self._name_cache.get(symbol, symbol)
        self._name_cache[symbol] = name

        return Tick(
            symbol=symbol,
            name=name,
            ts=_us_to_datetime(total.get("time") or data.get("lastUpdated")),
            price=data.get("lastPrice") or data.get("closePrice") or data.get("referencePrice", 0),
            open=data.get("openPrice", 0) or 0,
            high=data.get("highPrice", 0) or 0,
            low=data.get("lowPrice", 0) or 0,
            prev_close=data.get("previousClose", 0) or 0,
            volume=total.get("tradeVolume", 0) or 0,
            turnover=total.get("tradeValue", 0) or 0,
            bid_prices=[b["price"] for b in bids],
            bid_volumes=[b["size"] for b in bids],
            ask_prices=[a["price"] for a in asks],
            ask_volumes=[a["size"] for a in asks],
            # tradeVolumeAtBid = 成交價落在買價 = 主動賣出 = 內盤
            # tradeVolumeAtAsk = 成交價落在賣價 = 主動買進 = 外盤
            inner_volume=total.get("tradeVolumeAtBid", 0) or 0,
            outer_volume=total.get("tradeVolumeAtAsk", 0) or 0,
            is_disposition=False,   # TODO: 見檔頭說明，可另外用REST補齊
            is_attention=False,
            is_short_restricted=False,
        )

    def subscribe(self, symbols: List[str], on_tick: Callable[[Tick], None]):
        """可以被呼叫多次（全市場掃描動態換股時，每次候選輪動都會再呼叫一次
        subscribe()/unsubscribe() 加減新的標的）。第一次呼叫才會真正建立WebSocket
        連線、註冊事件handler；之後的呼叫只會送出額外的 subscribe 訊息，
        不會重新連線或重複註冊handler——避免每次動態換股都把連線重置一次，
        也避免同一個事件重複觸發多次handler（這是之前版本的一個潛在bug，
        在還只有「啟動時訂閱一次」的用法下不會被發現，但動態訂閱功能上線後就會出現）。"""
        if self._stock_ws is None:
            raise RuntimeError("請先呼叫 connect()")

        with self._subscribe_lock:
            if not self._ws_connected:
                def handle_message(message):
                    # 富果 WebSocket 訊息格式為 {"event": "...", "data": {...}}
                    # 注意：fugle-marketdata SDK 傳給 message handler 的是「原始 JSON 字串」
                    # 不是 dict（見 SDK websocket/client.py 的 __on_message），必須先解析，
                    # 否則下面的 isinstance(dict) 檢查會讓每一筆報價都被默默丟掉
                    if isinstance(message, (str, bytes, bytearray)):
                        try:
                            message = json.loads(message)
                        except ValueError:
                            return
                    event = message.get("event") if isinstance(message, dict) else None
                    if event not in ("data", None):
                        if event in ("authenticated", "subscribed"):
                            print(f"[FugleDataSource] {event}")
                        return
                    data = message.get("data") if isinstance(message, dict) else None
                    if not data or "symbol" not in data or "total" not in data:
                        return
                    try:
                        tick = self._parse_aggregate_to_tick(data)
                        self._on_tick(tick)
                    except Exception as e:
                        print(f"[FugleDataSource] 解析訊息失敗: {e}, raw={message}")

                def handle_connect():
                    print("[FugleDataSource] WebSocket 已連線")

                def handle_disconnect(code, reason):
                    print(f"[FugleDataSource] WebSocket 斷線: {code} {reason}")

                def handle_error(error):
                    print(f"[FugleDataSource] WebSocket 錯誤: {error}")

                self._on_tick = on_tick
                self._stock_ws.on("connect", handle_connect)
                self._stock_ws.on("message", handle_message)
                self._stock_ws.on("disconnect", handle_disconnect)
                self._stock_ws.on("error", handle_error)
                self._stock_ws.connect()
                self._ws_connected = True

            for symbol in symbols:
                self._stock_ws.subscribe({"channel": "aggregates", "symbol": symbol})
                print(f"[FugleDataSource] 已訂閱 {symbol}")

    def unsubscribe(self, symbols: List[str]):
        """取消訂閱（全市場掃描換候選股時使用）。SDK的 unsubscribe() 呼叫格式跟
        subscribe() 完全對稱（都是送 {"channel": ..., "symbol": ...}），
        已直接確認過安裝的 fugle_marketdata 套件原始碼。"""
        if self._stock_ws is None:
            return
        for symbol in symbols:
            try:
                self._stock_ws.unsubscribe({"channel": "aggregates", "symbol": symbol})
                print(f"[FugleDataSource] 已取消訂閱 {symbol}")
            except Exception as e:
                print(f"[FugleDataSource] 取消訂閱 {symbol} 失敗: {e}")

    def get_rest_client(self):
        """給 market_scanner.py 用：全市場掃描需要直接呼叫 intraday.tickers()/quote()，
        這兩個是REST批次/輪詢用途，跟即時 WebSocket 訂閱是分開的兩件事，
        所以不透過 subscribe() 走，而是讓 scanner 直接拿 RestClient 來用。"""
        if self._rest_client is None:
            raise RuntimeError("請先呼叫 connect()")
        return self._rest_client

    def get_quote_tick(self, symbol: str) -> Tick:
        """REST intraday/quote 的欄位跟 WebSocket aggregates 一樣（symbol/total/bids/asks/
        lastUpdated，已實際呼叫確認，上市上櫃都適用），直接共用同一個解析函式。"""
        if self._rest_client is None:
            raise RuntimeError("請先呼叫 connect()")
        self.rate_limiter.wait()
        return self._parse_aggregate_to_tick(self._rest_client.stock.intraday.quote(symbol=symbol))

    def get_intraday_candles(self, symbol: str) -> List[Bar]:
        """REST intraday/candles（timeframe=1）。回傳格式已實際呼叫確認：
        {"date": "2026-09-30", "symbol": "2330", "data": [
            {"date": "2026-09-30T09:00:00.000+08:00", "open", "high", "low", "close", "volume", "average"}, ...]}
        每呼叫一次用掉1次REST額度，一樣過共用的 rate_limiter。"""
        if self._rest_client is None:
            raise RuntimeError("請先呼叫 connect()")
        self.rate_limiter.wait()
        resp = self._rest_client.stock.intraday.candles(symbol=symbol, timeframe="1")
        return self._candles_to_bars(symbol, resp)

    def get_previous_day_candles(self, symbol: str) -> List[Bar]:
        """REST historical/candles（timeframe=1）。已實際呼叫確認：
        - 可以一次查多天，回傳新到舊；沒開盤的日子（假日、颱風假）就沒有資料
        - volume 單位跟即時報價的累計量相同（當日1分K加總 = quote total.tradeVolume）
        查最近 14 天（涵蓋春節長假），取今天以前最近一個有資料的交易日。"""
        if self._rest_client is None:
            raise RuntimeError("請先呼叫 connect()")
        today = datetime.now(TW_TZ).date()
        self.rate_limiter.wait()
        resp = self._rest_client.stock.historical.candles(
            symbol=symbol, timeframe="1",
            **{"from": (today - timedelta(days=14)).isoformat(),
               "to": (today - timedelta(days=1)).isoformat()})
        bars = [b for b in self._candles_to_bars(symbol, resp) if b.ts_open.date() < today]
        if not bars:
            return []
        last_day = max(b.ts_open.date() for b in bars)
        return [b for b in bars if b.ts_open.date() == last_day]

    @staticmethod
    def _candles_to_bars(symbol: str, resp: dict) -> List[Bar]:
        bars = []
        for c in resp.get("data", []) or []:
            bars.append(Bar(
                symbol=symbol,
                interval_min=1,
                ts_open=datetime.fromisoformat(c["date"]).astimezone(TW_TZ),
                open=c.get("open", 0) or 0,
                high=c.get("high", 0) or 0,
                low=c.get("low", 0) or 0,
                close=c.get("close", 0) or 0,
                volume=c.get("volume", 0) or 0,
            ))
        return bars

    def get_index_quote(self) -> Tick:
        if self._rest_client is None:
            raise RuntimeError("請先呼叫 connect()")
        self.rate_limiter.wait()
        q = self._rest_client.stock.intraday.quote(symbol="IX0001")
        price = q.get("closePrice") or q.get("lastPrice") or q.get("referencePrice", 0)
        return Tick(
            symbol="IX0001",
            name="加權指數",
            ts=datetime.now(TW_TZ),
            price=price,
            open=q.get("openPrice", 0) or 0,
            high=q.get("highPrice", 0) or 0,
            low=q.get("lowPrice", 0) or 0,
            prev_close=q.get("previousClose", 0) or 0,
            volume=0,
            turnover=0.0,
        )

    def disconnect(self):
        if self._stock_ws is not None:
            self._stock_ws.disconnect()
            self._ws_connected = False
            print("[FugleDataSource] 已中斷")
