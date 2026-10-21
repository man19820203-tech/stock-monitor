"""
全市場掃描器（第一層篩選）。

背景：Fugle 免費方案沒有「一次拿全市場報價」的批次端點，也沒有真正意義上的
「全市場即時推播」——WebSocket 訂閱數上限只有5檔（付費方案也就數百~數千檔，
不是無上限），REST 呼叫則限制 60次/分鐘。

所以「看全部台股」在免費方案下不可能是即時的，只能做「輪詢式全市場掃描」：
    第一層（本檔）：用 REST 按頻率上限，一檔一檔把全市場（或至少全部上市權值股）
                    掃過一輪，算出概略的漲跌%/成交額排名，篩出前N名候選。
                    掃一輪在免費額度下大約需要 (股票檔數 / 55) 分鐘，
                    以全部約1000多檔上市股計算，一輪要 20-30 分鐘，
                    所以這是「概略雷達」，抓的是「這幾分鐘誰在動」，
                    不是逐秒即時的全市場報價。
    第二層（main.py Orchestrator）：把第一層篩出的候選，動態訂閱到既有的
                    WebSocket 即時引擎（FugleDataSource），只有這幾檔才有
                    真正逐秒即時的五檔/內外盤/K棒/劇本判斷。

之後如果升級付費方案（更高REST額度、更多WebSocket訂閱數），把
config/settings.yaml 的 scan.rest_calls_per_minute / top_n_candidates /
max_websocket_subscriptions 調大即可，不用改這裡的邏輯。

加速：先用官方資料縮小範圍（universe.scan.shortlist_size）
  Fugle 免費方案的全市場 snapshot 端點會回 403（已實測），所以改用證交所 / 櫃買中心的
  官方 OpenAPI（免金鑰、不佔 Fugle 額度，一次就拿到全市場前一交易日成交資料，<1秒），
  挑出前一交易日成交額最大的前 N 檔，Fugle 只逐檔掃這 N 檔：
  預設 100 檔 ≈ 2 分鐘一輪，取代原本逐檔掃 1000 多檔的 20-30 分鐘。
  代價：「昨天沒上榜、今天才突然爆量」的股票會漏掉，N 調大可以降低這個風險。
  官方資料抓不到時，自動退回原本的全市場逐檔掃描。
  開盤前（今天還沒有成交額可以排）則直接用這份排行當候選，讓候選從 09:00 就開始即時訂閱，
  見 pre_open_candidates()。

補充：因為免費額度下掃一輪要20-30分鐘，如果每次「重新啟動程式」都要重新從零
掃一輪才看得到候選名單，體驗會很差。所以這裡多做了一層「當日快取」
（見 load_cached_candidates/save_cache）：只要是同一個交易日，重啟程式時會先
拿上次掃描完成的結果頂著、立刻可以看到候選（畢竟盤中股票輪動也不是秒等級的事），
背景同時會馬上重新掃一輪去更新成最新結果。跨日的快取一律視為過期、不會使用。
"""
import json
import os
from datetime import datetime, timedelta
from typing import Optional

from config_loader import get
from market_time import now_tw
from user_watchlist import pinned_symbols

CACHE_PATH = os.path.join("data", "scan_cache.json")

# 官方 OpenAPI（免金鑰）：上市 = 證交所「每日收盤行情(全部)」、上櫃 = 櫃買中心「上櫃股票行情」
TWSE_DAY_ALL_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_QUOTES_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"


def _to_float(s) -> Optional[float]:
    try:
        return float(str(s).replace(",", "").replace("+", ""))
    except (TypeError, ValueError):
        return None


def _rank_key(c: dict) -> float:
    """概略排序依據：成交額 * (1 + |漲跌幅|/10)，讓「量大又有波動」的排前面。"""
    return c["turnover_billion"] * (1 + abs(c["change_pct"]) / 10)


def build_shortlist(twse_rows: list, tpex_rows: list, size: int,
                    exclude_etf: bool, price_range: dict) -> list[dict]:
    """把證交所 / 櫃買中心前一交易日行情合併，依成交額由大到小取前 size 檔。
    只收一般股票代碼（4碼數字）；exclude_etf=False 時另外收 00 開頭的 ETF。
    回傳格式跟 scan_once() 的候選一樣：{"symbol","name","price","change_pct","turnover_billion"}"""
    rows = []
    for r in twse_rows:
        rows.append((r.get("Code"), r.get("Name"), r.get("ClosingPrice"), r.get("Change"), r.get("TradeValue")))
    for r in tpex_rows:
        rows.append((r.get("SecuritiesCompanyCode"), r.get("CompanyName"), r.get("Close"),
                     r.get("Change"), r.get("TransactionAmount")))

    out = []
    for code, name, close, change, value in rows:
        code = (code or "").strip()
        is_etf = code.startswith("00")
        if is_etf and exclude_etf:
            continue
        if not (code.isdigit() and len(code) == 4) and not is_etf:
            continue
        price, chg, turnover = _to_float(close), _to_float(change), _to_float(value)
        if not price or not turnover:
            continue
        if not (price_range.get("min", 0) <= price <= price_range.get("max", 999999)):
            continue
        prev_close = price - (chg or 0)
        out.append({
            "symbol": code,
            "name": (name or code).strip(),
            "price": price,
            "change_pct": round((chg or 0) / prev_close * 100, 2) if prev_close else 0.0,
            "turnover_billion": round(turnover / 1e8, 2),
        })
    out.sort(key=lambda c: c["turnover_billion"], reverse=True)
    return out[:size]


def _fetch_json(url: str) -> list:
    # 用 requests（fugle-marketdata 的相依套件，一定有裝）而不是內建 urllib：
    # 實測內建 urllib 在這台 Windows 上驗證證交所憑證鏈會失敗（CERTIFICATE_VERIFY_FAILED），
    # requests 用自帶的 certifi 憑證庫則正常
    import requests
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    resp.raise_for_status()
    return resp.json()


class MarketScanner:
    def __init__(self, fugle_data_source):
        """fugle_data_source: 必須是已經 connect() 過的 FugleDataSource，
        掃描器直接借用它已建立好的 RestClient（get_rest_client()），
        不重複建立連線物件。限速也直接共用 fugle_data_source.rate_limiter——
        這點很重要：如果掃描器自己另外維護一份限速計數，會跟同時在跑的
        大盤指數輪詢（main.py poll_index，也會打REST）各算各的，兩邊加總
        實際打向富果API的次數還是會超過真正的帳號上限，導致 429 Rate limit
        exceeded（這是之前發生過的真實案例）。"""
        self.ds = fugle_data_source
        self._ticker_list: list[dict] = []
        self._ticker_list_fetched_at: Optional[datetime] = None
        self._shortlist: list[dict] = []
        self._shortlist_fetched_at: Optional[datetime] = None

    # ---------- 限速（全系統共用同一個計數器，見 fugle_source.SharedRestRateLimiter）----------
    def _rate_limit_wait(self):
        self.ds.rate_limiter.wait()

    def _rest(self):
        return self.ds.get_rest_client()

    @staticmethod
    def candidate_slots() -> int:
        """掃描候選可用的名額 = min(top_n, 訂閱上限 − 固定追蹤檔數)。
        固定追蹤（設定檔 watchlist + 儀表板自選股）+ 候選的總數不能超過 WebSocket 訂閱上限，
        否則多出來的會被富果拒絕。"""
        top_n = get("universe.scan.top_n_candidates", 5)
        max_sub = get("universe.scan.max_websocket_subscriptions", 5)
        return max(0, min(top_n, max_sub - len(pinned_symbols())))

    @staticmethod
    def _ranked_candidates(candidates: list) -> list:
        """排除固定追蹤的股票後，保留到「訂閱上限」檔（而不是只到目前剩餘名額）：
        自選股移除時，Orchestrator 可以直接用這份名單把空出的名額補回來。
        實際要訂閱幾檔由 Orchestrator._apply_candidates 依剩餘名額截斷。"""
        pinned = set(pinned_symbols())
        max_sub = get("universe.scan.max_websocket_subscriptions", 5)
        top_n = get("universe.scan.top_n_candidates", 5)
        return [c for c in candidates if c["symbol"] not in pinned][: min(top_n, max_sub)]

    # ---------- 全市場代碼清單（低頻率重抓）----------
    def _refresh_ticker_list_if_needed(self):
        refresh_hours = get("universe.scan.ticker_list_refresh_hours", 6)
        if (self._ticker_list_fetched_at is not None and
                datetime.now() - self._ticker_list_fetched_at < timedelta(hours=refresh_hours)
                and self._ticker_list):
            return
        self._rate_limit_wait()
        try:
            resp = self._rest().stock.intraday.tickers(type="EQUITY")
            self._ticker_list = resp.get("data", []) or []
            self._ticker_list_fetched_at = datetime.now()
            print(f"[MarketScanner] 已更新全市場代碼清單，共 {len(self._ticker_list)} 檔")
        except Exception as e:
            print(f"[MarketScanner] 取得全市場代碼清單失敗: {e}")

    # ---------- 官方前一交易日成交額排行（縮小掃描範圍，不佔 Fugle 額度）----------
    def _refresh_shortlist_if_needed(self):
        size = get("universe.scan.shortlist_size", 100)
        if not size:
            self._shortlist = []   # 0 = 關閉，逐檔掃全市場（舊行為）
            return
        refresh_hours = get("universe.scan.ticker_list_refresh_hours", 6)
        if (self._shortlist and self._shortlist_fetched_at is not None and
                datetime.now() - self._shortlist_fetched_at < timedelta(hours=refresh_hours)):
            return
        twse_rows, tpex_rows = [], []
        for label, url, target in (("證交所", TWSE_DAY_ALL_URL, twse_rows),
                                   ("櫃買中心", TPEX_QUOTES_URL, tpex_rows)):
            try:
                target.extend(_fetch_json(url))
            except Exception as e:
                print(f"[MarketScanner] 取得{label}前一交易日行情失敗: {e}")
        shortlist = build_shortlist(
            twse_rows, tpex_rows, size,
            exclude_etf=get("universe.exclude_etf", True),
            price_range=get("universe.price_range", {"min": 0, "max": 999999}),
        )
        if shortlist:
            self._shortlist = shortlist
            self._shortlist_fetched_at = datetime.now()
            print(f"[MarketScanner] 已用官方前一交易日成交額排行縮小掃描範圍：前 {len(shortlist)} 檔"
                  f"（上市 {len(twse_rows)} / 上櫃 {len(tpex_rows)} 檔中挑選）")
        elif not self._shortlist:
            print("[MarketScanner] 官方排行抓不到，本輪改為逐檔掃描全市場（較慢）")

    def pre_open_candidates(self) -> list[dict]:
        """開盤前今天還沒有成交額可以排，直接用官方前一交易日排行當候選，
        讓候選從 09:00 就開始收即時報價（第一高低完整），不用等開盤後第一輪掃描。"""
        self._refresh_shortlist_if_needed()
        return self._ranked_candidates(sorted(self._shortlist, key=_rank_key, reverse=True))

    def _passes_universe_filters(self, ticker: dict) -> bool:
        """套用 universe.* 篩選規則（排除ETF/處置股、價格區間等），
        跟即時引擎那邊用的是同一套 config，篩選標準保持一致。"""
        symbol = ticker.get("symbol", "")
        if get("universe.exclude_etf", True):
            # 上市ETF代碼慣例為 00 開頭（例如 0050、0056），股票代碼不會這樣開頭
            if symbol.startswith("00"):
                return False
        # tickers() 端點通常沒有直接給「是否處置股/禁空」旗標，這兩項在真正
        # 訂閱到WebSocket、拿到aggregates資料後，_enrich_flags（fugle_source.py的TODO）
        # 若未來補上會在第二層（即時引擎）再擋一次；掃描階段先不擋，避免漏掉候選。
        return True

    def _passes_price_range(self, price: float) -> bool:
        rng = get("universe.price_range", {"min": 0, "max": 999999})
        if price <= 0:
            return False
        return rng.get("min", 0) <= price <= rng.get("max", 999999)

    # ---------- 掃描一輪 ----------
    def scan_once(self) -> list[dict]:
        """掃一輪全市場（在REST額度限制下），回傳依「成交額+漲跌幅」概略排序的候選清單。
        每個元素: {"symbol", "name", "price", "change_pct", "turnover_billion"}

        注意：這是「輪詢式」掃描，掃一輪本身要花相當時間（見檔頭說明），
        呼叫端（main.py 的 Orchestrator）應該把這個放在背景執行緒跑，
        不要卡住主流程或WebSocket即時處理。
        """
        self._refresh_shortlist_if_needed()
        if self._shortlist:
            universe = [{"symbol": c["symbol"], "name": c["name"]} for c in self._shortlist]
        else:
            self._refresh_ticker_list_if_needed()
            universe = self._ticker_list
        if not universe:
            return []

        min_turnover = get("universe.scan.min_turnover_billion_for_scan", 1)
        candidates = []

        for ticker in universe:
            symbol = ticker.get("symbol")
            if not symbol or not self._passes_universe_filters(ticker):
                continue

            self._rate_limit_wait()
            try:
                q = self._rest().stock.intraday.quote(symbol=symbol)
            except Exception as e:
                # 單檔失敗不要讓整輪掃描中斷（可能是暫時性API錯誤、下市股票等）
                print(f"[MarketScanner] 查詢 {symbol} 失敗，略過: {e}")
                continue

            price = q.get("closePrice") or q.get("lastPrice") or q.get("referencePrice") or 0
            prev_close = q.get("previousClose") or 0
            if not self._passes_price_range(price):
                continue

            total = q.get("total", {}) or {}
            turnover = total.get("tradeValue", 0) or 0
            turnover_billion = round(turnover / 1e8, 2)
            if turnover_billion < min_turnover:
                continue

            change_pct = q.get("changePercent")
            if change_pct is None and prev_close:
                change_pct = round((price - prev_close) / prev_close * 100, 2)
            change_pct = change_pct or 0.0

            candidates.append({
                "symbol": symbol,
                "name": q.get("name") or ticker.get("name") or symbol,
                "price": price,
                "change_pct": change_pct,
                "turnover_billion": turnover_billion,
            })

        # 概略排序依據：成交額 * (1 + |漲跌幅|/10)，讓「量大又有波動」的排前面，
        # 這只是第一層粗篩用的排序，正式的多空/劇本評分仍由第二層即時引擎（score_engine）算，
        # 這裡的目的只是決定「值得花掉WebSocket訂閱額度去盯」的候選是誰。
        candidates.sort(key=_rank_key, reverse=True)
        # 固定追蹤的股票本來就會被追蹤，不佔候選名額
        result = self._ranked_candidates(candidates)
        print(f"[MarketScanner] 本輪掃描完成，篩出候選: "
              f"{[c['symbol'] for c in result]}（{now_tw().strftime('%H:%M:%S')}）")
        self.save_cache(result)
        return result

    # ---------- 當日快取（重啟程式不用每次乾等一輪完整掃描）----------
    def load_cached_candidates(self) -> Optional[list]:
        """讀取上次成功掃描完成的候選清單。只認「今天」（Asia/Taipei）存的快取，
        跨日一律視為過期不使用——隔一天股票的成交量/漲跌都是全新的，用舊資料
        沒有意義，寧可讓使用者多等一輪新掃描，也不要顯示昨天的候選誤導。"""
        try:
            if not os.path.exists(CACHE_PATH):
                return None
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                cache = json.load(f)
            if cache.get("date") != now_tw().strftime("%Y-%m-%d"):
                return None
            candidates = cache.get("candidates") or []
            if candidates:
                print(f"[MarketScanner] 已載入今日快取候選（上次掃描於 "
                      f"{cache.get('saved_at', '?')}）: {[c['symbol'] for c in candidates]}，"
                      f"背景將立即重新掃描一輪更新")
            return candidates
        except Exception as e:
            print(f"[MarketScanner] 讀取快取失敗（忽略，改為乾等即時掃描）: {e}")
            return None

    def save_cache(self, candidates: list):
        try:
            os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
            with open(CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump({
                    "date": now_tw().strftime("%Y-%m-%d"),
                    "saved_at": now_tw().strftime("%H:%M:%S"),
                    "candidates": candidates,
                }, f, ensure_ascii=False)
        except Exception as e:
            # 快取寫入失敗不影響系統正常運作，只是下次重啟要重新乾等一輪掃描而已
            print(f"[MarketScanner] 寫入快取失敗（不影響本次運作）: {e}")
