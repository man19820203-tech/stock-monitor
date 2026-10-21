"""
上線前 API 連線檢查（開盤前跑一次，確認每個資料來源都讀得到）：

    python scripts/check_api.py

會逐項實際呼叫系統用到的每一個外部 API，印出 PASS / FAIL：
  1. 設定檔（資料來源、API Key、訂閱數）
  2. 證交所 / 櫃買中心官方 OpenAPI（全市場掃描縮小範圍、開盤前候選）
  3. 富果 REST：大盤指數、個股報價、當日1分K、前一交易日1分K、全市場代碼清單
  4. 富果 WebSocket：連線、認證、訂閱
總共約用掉 6 次富果 REST 額度 + 1 個 WebSocket 連線（幾秒後就關閉）。
注意：主程式正在跑的時候不要同時執行（免費方案只能有 1 條 WebSocket 連線）。
"""
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config_loader import get
from data_source.market_scanner import (TPEX_QUOTES_URL, TWSE_DAY_ALL_URL, _fetch_json,
                                        build_shortlist)
from market_time import now_tw

results = []


def check(name, fn):
    try:
        detail = fn()
        results.append((True, name))
        print(f"[PASS] {name}：{detail}")
    except Exception as e:
        results.append((False, name))
        print(f"[FAIL] {name}：{e}")


def cfg():
    provider = get("data_source.provider")
    token = get("data_source.fugle.api_token", "")
    from user_watchlist import pinned_symbols
    wl = pinned_symbols()   # 設定檔 watchlist + 儀表板自選股
    max_sub = get("universe.scan.max_websocket_subscriptions", 5)
    assert provider == "fugle", f"data_source.provider 是 {provider!r}，要接真實資料請改成 fugle"
    assert token, "富果 API Key 是空的（本機填 config/secrets.yaml，雲端設環境變數 FUGLE_API_TOKEN）"
    assert len(wl) <= max_sub, f"watchlist 有 {len(wl)} 檔，超過訂閱上限 {max_sub}"
    rng = get("universe.price_range", {})
    return (f"provider=fugle、API Key 已填、自選 {len(wl)} 檔、掃描候選名額 "
            f"{min(get('universe.scan.top_n_candidates', 5), max_sub - len(wl))} 檔、"
            f"價格區間 {rng.get('min')}~{rng.get('max')}")


print(f"=== API 連線檢查（{now_tw():%Y-%m-%d %H:%M:%S} 台北時間）===\n")
check("設定檔", cfg)

official = {}


def twse():
    rows = _fetch_json(TWSE_DAY_ALL_URL)
    official["twse"] = rows
    assert len(rows) > 500, f"只拿到 {len(rows)} 筆"
    return f"{len(rows)} 筆（資料日期 {rows[0].get('Date')}）"


def tpex():
    rows = _fetch_json(TPEX_QUOTES_URL)
    official["tpex"] = rows
    assert len(rows) > 300, f"只拿到 {len(rows)} 筆"
    return f"{len(rows)} 筆（資料日期 {rows[0].get('Date')}）"


def shortlist():
    size = get("universe.scan.shortlist_size", 100)
    rows = build_shortlist(official.get("twse", []), official.get("tpex", []), size,
                           get("universe.exclude_etf", True), get("universe.price_range", {}))
    assert rows, "排行是空的"
    top = "、".join(f"{r['symbol']}{r['name']}" for r in rows[:5])
    return f"前 {len(rows)} 檔，成交額最大：{top}"


check("證交所 OpenAPI（上市）", twse)
check("櫃買中心 OpenAPI（上櫃）", tpex)
check("官方排行 → 掃描範圍", shortlist)

from fugle_marketdata import RestClient, WebSocketClient  # noqa: E402

token = get("data_source.fugle.api_token", "")
rest = RestClient(api_key=token).stock


def paced(fn):
    time.sleep(1.3)   # 跟主程式一樣平均分散呼叫，避免被富果判定太密集（429）
    return fn()


def index_quote():
    q = paced(lambda: rest.intraday.quote(symbol="IX0001"))
    price = q.get("closePrice") or q.get("lastPrice") or q.get("referencePrice")
    assert price, f"沒有價格欄位：{json.dumps(q, ensure_ascii=False)[:200]}"
    return f"加權指數 {price}（{q.get('date')}）"


def stock_quote():
    q = paced(lambda: rest.intraday.quote(symbol="2330"))
    assert "total" in q and "symbol" in q, "回傳格式跟預期不同（缺 total/symbol）"
    price = q.get("lastPrice") or q.get("closePrice") or q.get("referencePrice")
    return f"2330 {q.get('name')} {price}（{q.get('date')}）"


def candles():
    r = paced(lambda: rest.intraday.candles(symbol="2330", timeframe="1"))
    d = r.get("data", [])
    if not d:
        return f"目前沒有K棒（{r.get('date')}，開盤前正常，09:00 後才會有）"
    return f"{len(d)} 根，第一根 {d[0]['date']}（{r.get('date')}）"


def previous_day():
    from datetime import timedelta
    today = now_tw().date()
    r = paced(lambda: rest.historical.candles(
        symbol="2330", timeframe="1",
        **{"from": (today - timedelta(days=14)).isoformat(), "to": (today - timedelta(days=1)).isoformat()}))
    d = r.get("data", [])
    assert d, "沒有前一交易日的1分K"
    last = max(x["date"][:10] for x in d)
    rows = [x for x in d if x["date"].startswith(last)]
    return f"{last} 共 {len(rows)} 根、全日 {sum(x['volume'] for x in rows)} 張"


def tickers():
    r = paced(lambda: rest.intraday.tickers(type="EQUITY"))
    d = r.get("data", [])
    assert d, "代碼清單是空的"
    return f"{len(d)} 檔（官方排行抓不到時的備援）"


check("富果 REST 大盤指數", index_quote)
check("富果 REST 個股報價", stock_quote)
check("富果 REST 當日1分K（第一高低回補）", candles)
check("富果 REST 前一交易日1分K（量能分數）", previous_day)
check("富果 REST 代碼清單", tickers)


def websocket():
    events = {}
    done = threading.Event()
    ws = WebSocketClient(api_key=token).stock

    def on_message(message):
        if isinstance(message, (str, bytes, bytearray)):
            message = json.loads(message)
        ev = message.get("event")
        events.setdefault(ev, message)
        if ev == "subscribed" or ev == "error":
            done.set()
        if ev == "authenticated":
            ws.subscribe({"channel": "aggregates", "symbol": "2330"})

    ws.on("message", on_message)
    ws.on("error", lambda e: (events.setdefault("error", str(e)), done.set()))
    ws.connect()
    try:
        done.wait(15)
    finally:
        ws.disconnect()
    assert "error" not in events, f"WebSocket 錯誤：{events['error']}"
    assert "authenticated" in events, f"沒收到認證成功（收到的事件：{list(events)}）"
    assert "subscribed" in events, f"沒收到訂閱成功（收到的事件：{list(events)}）"
    return "連線、認證、訂閱 2330 都成功（盤中才會收到即時報價）"


check("富果 WebSocket", websocket)

failed = [n for ok, n in results if not ok]
print()
if failed:
    print(f"=== 有 {len(failed)} 項失敗：{'、'.join(failed)} ===")
    sys.exit(1)
print(f"=== 全部 {len(results)} 項通過，可以啟動系統 ===")
