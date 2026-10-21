"""
系統主流程（orchestrator）：

    即時行情 → 第一高低 → 成交額/量能 → 相對強弱 → 多空排名 → 劇本 → AI解讀 → 網頁

執行方式：
    python main.py                # 只跑資料處理，不開網頁（適合搭配另開 web server）
    python -m web.app             # 另開一個 terminal 跑網頁（見 web/app.py）
或直接：
    python run_all.py             # 資料處理 + 網頁一起啟動（見 run_all.py）
"""
import threading
import time
from datetime import datetime, timedelta

from config_loader import get, load_config
from data_source.factory import build_data_source
from engine.first_range import FirstRangeTracker
from engine.indicators import IndicatorEngine, build_volume_profile
from engine.market_context import MarketContext, compute_group_strength, group_is_synced
from scoring.score_engine import compute_scores
from scenario.scenario_engine import classify_scenario
from ai_narrator.narrator import narrate
from models import StockDailyState
import state_store
import user_watchlist
from data_source.market_scanner import MarketScanner
from market_time import now_tw, TW_TZ
from user_watchlist import pinned_symbols


class Orchestrator:
    def __init__(self):
        load_config(force_reload=True)
        self.ds = build_data_source()
        self.first_range_tracker = FirstRangeTracker()
        self.indicator_engine = IndicatorEngine()
        self.market_ctx = MarketContext()
        self.states: dict[str, StockDailyState] = {}
        self._symbol_to_group = self._build_symbol_group_map()
        self._dynamic_symbols: set = set()   # 由全市場掃描動態加入/移除的候選（跟手動watchlist分開管）
        self._scanner = None
        self._backfilled: set = set()        # 今天已經用1分K補過第一高低的標的，被掃描輪出再輪入時不重複打API
        self._last_candidates: list = []     # 最近一次掃描/暫定候選（依排名），自選股移除後用來立刻補回空出的名額
        # 掃描執行緒和網頁（加入/移除自選股）都會改訂閱名單，要排隊
        self._sub_lock = threading.RLock()
        state_store.register_controller(self)

    def _build_symbol_group_map(self) -> dict:
        groups_cfg = get("groups", {})
        m = {}
        for group_name, symbols in groups_cfg.items():
            for s in symbols:
                m[s] = group_name
        return m

    def _watchlist(self) -> list:
        wl = pinned_symbols()
        for s in self._dynamic_symbols:
            if s not in wl:
                wl.append(s)
        return wl

    def _subscribe(self, symbols: list):
        """所有「開始追蹤某檔」都走這裡：WebSocket訂閱 + 背景暖機（回補第一高低、先拉一次報價）。"""
        self.ds.subscribe(symbols, self.on_tick)
        threading.Thread(target=self._warm_up, args=(list(symbols),), daemon=True).start()

    def _warm_up(self, symbols: list):
        """背景執行緒，每檔依序做三件事（每件用1次REST額度，跟大盤輪詢、全市場掃描共用限速）：

        1. 當日1分K回補（_backfill_intraday）：第一高低只看 09:00~09:10 收到的 tick、均線只看
           開始追蹤後收到的 tick。晚開、重開、或全市場掃描中途才選進來的股票，第一高低會退化成
           「開始追蹤當下的價格」、MA60 要等 60 分鐘才算得出來。這裡抓當日1分K把兩者補回去。
        2. 前一交易日1分K（_load_volume_profile）：量能分數（今日量比昨日同期）的對照表。
        3. 先拉一次最新報價餵進 on_tick：不然儀表板要等 WebSocket 推來第一筆報價才會出現
           這檔（收盤後根本不會推，就會一直是空的）。放在回補之後，這筆報價才會用正確的
           第一高低、均線、量能對照算劇本和分數。
        """
        for s in symbols:
            self._backfill_intraday(s)
            self._load_volume_profile(s)
            if s not in self._watchlist():
                continue   # 暖機期間已經被掃描輪出，不要再把它放回儀表板
            try:
                tick = self.ds.get_quote_tick(s)
                if tick is not None:
                    self.on_tick(tick)
            except Exception as e:
                print(f"[Orchestrator] {s} 初始報價取得失敗（等 WebSocket 推送）: {e}")

    def _load_volume_profile(self, symbol: str):
        """量能分數（今日量比昨日同期，佔 20 分）需要「前一交易日每分鐘累計量」。
        開始追蹤時抓一次前一交易日 1 分K 算好（每檔每天 1 次 REST 額度）；
        抓不到就維持沒有對照表，量能分數給 0，不會拿模擬資料頂替。"""
        if self.indicator_engine.has_yesterday_profile(symbol):
            return
        try:
            bars = self.ds.get_previous_day_candles(symbol)
            if not bars:
                return
            self.indicator_engine.set_yesterday_profile(symbol, build_volume_profile(bars))
            print(f"[Orchestrator] {symbol} 已載入前一交易日（{bars[0].ts_open:%m/%d}）量能對照，"
                  f"全日 {sum(b.volume for b in bars)} 張")
        except Exception as e:
            print(f"[Orchestrator] {symbol} 前一交易日量能載入失敗（量能分數暫時為0）: {e}")

    def _backfill_intraday(self, symbol: str):
        """抓一次當日1分K，同時補回：
        - 第一高低（engine/first_range.py seed_from_bars）
        - 已經走完的K棒，讓 MA5/20/60、5分K 不用開始追蹤後再重新累積（engine/indicators.py seed_bars）
        09:00 前開始追蹤的不需要補；每檔追蹤期間補一次（被掃描輪出後再輪入會重補，補上中間的空檔）。"""
        if not get("first_range.backfill_from_candles", True):
            return
        if now_tw().time() < self.first_range_tracker._cfg()["start_time"]:
            return
        if symbol in self._backfilled:
            return
        self._backfilled.add(symbol)
        try:
            bars = self.ds.get_intraday_candles(symbol)
            if self.first_range_tracker.seed_from_bars(symbol, bars):
                fr = self.first_range_tracker.get_state(symbol)
                print(f"[Orchestrator] {symbol} 已用1分K補回第一高低："
                      f"高 {fr.first_high} / 低 {fr.first_low}")
            elif bars:
                print(f"[Orchestrator] {symbol} 當日1分K沒有觀察區間內的資料，第一高低改用即時報價")
            n = self.indicator_engine.seed_bars(symbol, bars, now_tw())
            if n:
                print(f"[Orchestrator] {symbol} 已用1分K補回 {n} 根K棒（均線可直接計算）")
        except Exception as e:
            # 補不到就維持原本的行為（只靠即時報價），不影響即時報價；允許下次重新訂閱時再試
            self._backfilled.discard(symbol)
            print(f"[Orchestrator] {symbol} 當日1分K回補失敗（改用即時報價）: {e}")

    def _scan_enabled(self) -> bool:
        return (get("data_source.provider") == "fugle"
                and get("universe.auto_scan", False)
                and get("universe.scan.enabled", False))

    def scan_loop(self):
        """背景執行緒：全市場掃描（第一層REST篩選），篩出候選後動態調整
        WebSocket即時訂閱（第二層），只有被選中的候選才會有逐秒即時報價/劇本判斷。
        詳細架構說明見 data_source/market_scanner.py 檔頭註解。"""
        self._scanner = MarketScanner(self.ds)

        # 重啟程式時，先用「今天稍早掃描完成」的快取候選頂著，立刻就能看到追蹤名單，
        # 不用乾等一輪全市場掃描；下面while迴圈第一輪還是會馬上重新掃一次，
        # 掃完後才會真正更新成最新結果（跨日的快取會被 MarketScanner 自動忽略）。
        # 快取可能是在自選清單變動前存的，_apply_candidates 會依目前剩餘的訂閱名額再截一次
        cached = self._scanner.load_cached_candidates()
        if cached:
            self._apply_candidates(cached, "今日快取候選")
        else:
            # 沒有今天的快取：先用官方前一交易日排行當暫定候選（1秒內就有），儀表板馬上有資料；
            # 開盤前這份就是正式候選（今天還沒成交額可排），盤中/盤後則等下面第一輪掃描（約2分鐘）後替換
            self._apply_candidates(self._scanner.pre_open_candidates(), "暫定候選（前一交易日排行）")

        while True:
            wait_sec = get("universe.scan.scan_interval_minutes", 5) * 60
            try:
                open_at = datetime.combine(
                    now_tw().date(), self.first_range_tracker._cfg()["start_time"], tzinfo=TW_TZ)
                first_scan_at = open_at + timedelta(
                    minutes=get("universe.scan.first_scan_delay_minutes", 3))
                if now_tw() < first_scan_at:
                    # 開盤前 / 剛開盤（例如 09:00:30 才啟動）成交額還沒累積，這時掃描大部分股票
                    # 都過不了成交額門檻：沿用上面的暫定候選（09:00 起就收即時報價），
                    # 等開盤幾分鐘後再做第一輪正式掃描
                    wait_sec = (first_scan_at - now_tw()).total_seconds()
                    print(f"[Orchestrator] 第一輪全市場掃描排在 {first_scan_at:%H:%M}，"
                          "在那之前先追蹤暫定候選")
                else:
                    candidates = self._scanner.scan_once()
                    if candidates:
                        self._apply_candidates(candidates, "全市場掃描候選")
                    else:
                        # 一檔都沒掃到（API異常、成交額門檻太高…）時保留目前的候選，不要把儀表板清空
                        print("[Orchestrator] 本輪掃描沒有任何候選，保留目前追蹤名單")
            except Exception as e:
                print(f"[Orchestrator] 全市場掃描發生錯誤（本輪跳過，下一輪重試）: {e}")

            time.sleep(max(wait_sec, 1))

    def _apply_candidates(self, candidates: list, label: str):
        """把候選清單套用到 WebSocket 訂閱：新的訂閱（並暖機），不在清單裡的退訂並從儀表板移除。
        候選只補滿「訂閱上限 − 固定追蹤（設定檔 watchlist + 儀表板自選股）」的名額。"""
        with self._sub_lock:
            self._last_candidates = list(candidates)
            pinned = set(pinned_symbols())
            ordered = [c["symbol"] for c in candidates if c["symbol"] not in pinned]
            new_symbols = set(ordered[: MarketScanner.candidate_slots()])
            name_map = get("universe.stock_names", {})
            for c in candidates:
                if c["symbol"] not in name_map:
                    name_map[c["symbol"]] = c["name"]

            to_add = new_symbols - self._dynamic_symbols
            to_remove = self._dynamic_symbols - new_symbols - pinned
            # 先更新名單再訂閱：暖機執行緒會用 _watchlist() 判斷這檔還在不在追蹤
            self._dynamic_symbols = new_symbols

            if to_remove:
                self._drop(to_remove)
                print(f"[Orchestrator] {label}：移除 {sorted(to_remove)}")
            if to_add:
                self._subscribe(list(to_add))
                print(f"[Orchestrator] {label}：新增 {sorted(to_add)}")

    def _drop(self, symbols):
        self.ds.unsubscribe(list(symbols))
        for s in symbols:
            self.states.pop(s, None)
            state_store.remove(s)
            # 退訂期間收不到報價、K棒會有空檔；之後再輪入時要重新用1分K補
            self._backfilled.discard(s)

    # ---------- 儀表板自選股（web/app.py 呼叫）----------
    def add_user_symbol(self, symbol: str) -> dict:
        """加入自選股：佔一個 WebSocket 名額，名額滿了就擠掉分數最低的掃描候選。
        跟其他股票走同一套流程（第一高低回補、劇本、多空分數、watch_alert 提醒）。"""
        symbol = symbol.strip().upper()
        if not (symbol.isalnum() and 4 <= len(symbol) <= 6):
            return {"ok": False, "message": f"「{symbol}」不是有效的股票代號"}
        with self._sub_lock:
            pinned = pinned_symbols()
            if symbol in pinned:
                return {"ok": True, "message": f"{symbol} 已經在自選清單裡"}
            max_sub = get("universe.scan.max_websocket_subscriptions", 5)
            if len(pinned) >= max_sub:
                return {"ok": False, "message": f"自選股已達 {max_sub} 檔上限（免費方案 WebSocket 訂閱數），請先移除一檔"}
            try:
                tick = self.ds.get_quote_tick(symbol)   # 順便確認代號存在（查不到會丟例外）
            except Exception as e:
                return {"ok": False, "message": f"查不到 {symbol}（{e}）"}
            name = tick.name if tick is not None else symbol
            get("universe.stock_names", {})[symbol] = name

            user_watchlist.add(symbol)
            if symbol in self._dynamic_symbols:
                # 本來就是掃描候選：直接轉成自選，不用重新訂閱
                self._dynamic_symbols.discard(symbol)
            else:
                room = max_sub - len(pinned) - 1
                if len(self._dynamic_symbols) > room:
                    self._evict_weakest_candidates(len(self._dynamic_symbols) - room)
                self._subscribe([symbol])
            print(f"[Orchestrator] 自選股加入 {symbol} {name}")
            return {"ok": True, "message": f"已加入 {symbol} {name}"}

    def remove_user_symbol(self, symbol: str) -> dict:
        symbol = symbol.strip().upper()
        with self._sub_lock:
            if symbol in user_watchlist.config_symbols():
                return {"ok": False, "message": f"{symbol} 是寫在 config/settings.yaml 的 watchlist，請從設定檔移除"}
            if symbol not in user_watchlist.user_symbols():
                return {"ok": False, "message": f"{symbol} 不在自選清單裡"}
            user_watchlist.remove(symbol)
            self._drop([symbol])
            print(f"[Orchestrator] 自選股移除 {symbol}")
            # 空出的名額立刻用最近一次掃描結果補回，不用等下一輪掃描
            if self._last_candidates:
                self._apply_candidates(self._last_candidates, "補回掃描候選")
            return {"ok": True, "message": f"已移除 {symbol}"}

    def _evict_weakest_candidates(self, n: int):
        """擠掉 n 檔目前分數最低（多空分數取高者）的掃描候選，讓出名額給自選股。"""
        rows = state_store.get_all()

        def strength(s):
            r = rows.get(s)
            return max(r["long_score"], r["short_score"]) if r else -1

        victims = sorted(self._dynamic_symbols, key=strength)[:n]
        self._dynamic_symbols -= set(victims)
        self._drop(victims)
        print(f"[Orchestrator] 為自選股讓出名額，移除掃描候選 {victims}")

    def user_watchlist_info(self) -> dict:
        return {
            "user": user_watchlist.user_symbols(),
            "config": user_watchlist.config_symbols(),
            "max": get("universe.scan.max_websocket_subscriptions", 5),
        }

    def _min_turnover(self) -> float:
        return get("liquidity.min_turnover_billion", 5)

    def on_tick(self, tick):
        # 大盤 tick 走另一條路徑（由 poll_index 呼叫），這裡只處理個股
        name_map = get("universe.stock_names", {})
        if tick.symbol in name_map:
            tick.name = name_map[tick.symbol]
        self.indicator_engine.update_bars(tick)
        fr_state = self.first_range_tracker.process_tick(tick)
        group = self._symbol_to_group.get(tick.symbol)

        state = self.indicator_engine.build_state(
            tick, fr_state, group, self.market_ctx.index_change_pct
        )
        self.states[tick.symbol] = state

        group_strength = compute_group_strength(self.states, get("groups", {}))
        state_store.set_group_strength(group_strength)

        scores = compute_scores(state, group_strength, self.market_ctx.index_change_pct)
        scenario_result = classify_scenario(
            state, self.market_ctx.index_change_pct, group_strength, self._min_turnover()
        )
        narrative = narrate(state, scenario_result, scores, use_claude=False)

        state_store.upsert(tick.symbol, {
            "symbol": tick.symbol,
            "name": state.name,
            "price": state.tick.price,
            "change_pct": state.change_pct,
            "turnover_billion": state.turnover_billion,
            "volume_multiple_recent": state.volume_multiple_recent,
            "volume_ratio_vs_yesterday": state.volume_ratio_vs_yesterday,
            "relative_strength_pct": state.relative_strength_pct,
            "first_high": state.first_range.first_high if state.first_range else None,
            "first_low": state.first_range.first_low if state.first_range else None,
            "scenario": scenario_result["scenario"],
            "direction": scenario_result["direction"],
            "signals": f"{scenario_result['signals_true']}/{scenario_result['signals_total']}",
            "signal_labels": scenario_result["signal_labels"],
            "long_score": scores["long"]["total"],
            "short_score": scores["short"]["total"],
            "narrative": narrative,
            "dist_to_limit_up_pct": state.dist_to_limit_up_pct,
            "dist_to_limit_down_pct": state.dist_to_limit_down_pct,
            "group": group,
        })

        # 高分事件寫入盤後回測log
        alert_th = get("display.alert_score_threshold", 80)
        if scores["long"]["total"] >= alert_th or scores["short"]["total"] >= alert_th:
            state_store.log_event({
                "symbol": tick.symbol, "scenario": scenario_result["scenario"],
                "long_score": scores["long"]["total"], "short_score": scores["short"]["total"],
                "price": state.tick.price,
            })

    def poll_index(self):
        while True:
            try:
                idx_tick = self.ds.get_index_quote()
                self.market_ctx.update_index(idx_tick)
                state_store.set_market(self.market_ctx.index_change_pct)
            except NotImplementedError:
                pass
            except Exception as e:
                # 任何單次失敗（例如富果API暫時性錯誤、429、網路抖動）都不該讓這個
                # 背景執行緒整個死掉——以前發生過一次429就導致大盤指數永遠不再更新的問題。
                # 印出錯誤但繼續下一輪，實際的呼叫頻率上限交給 ds.rate_limiter 統一控管。
                print(f"[Orchestrator] 大盤指數輪詢失敗（本輪跳過，下一輪重試）: {e}")
            # 大盤每次輪詢都會用掉1次REST額度（跟全市場掃描共用），間隔太短會嚴重拖慢掃描
            time.sleep(get("data_source.index_poll_interval_sec", 15))

    def start(self):
        """連線並啟動所有背景執行緒後立刻返回（cloud_run.py 要自己控制何時結束，所以跟 run() 分開）。"""
        manual = pinned_symbols()
        max_sub = get("universe.scan.max_websocket_subscriptions", 5)
        if get("data_source.provider") == "fugle" and len(manual) > max_sub:
            print(f"[Orchestrator] 警告：自選清單有 {len(manual)} 檔，超過 WebSocket 訂閱上限 {max_sub} 檔，"
                  "超出的部分會被富果拒絕。請減少 config/settings.yaml 的 universe.watchlist"
                  "或在儀表板移除自選股")
        self.ds.connect()
        self._subscribe(self._watchlist())
        threading.Thread(target=self.poll_index, daemon=True).start()
        if self._scan_enabled():
            threading.Thread(target=self.scan_loop, daemon=True).start()
            print("[Orchestrator] 全市場自動掃描已啟動（背景執行緒，"
                  f"每 {get('universe.scan.scan_interval_minutes', 5)} 分鐘掃一輪，"
                  f"取前 {get('universe.scan.top_n_candidates', 5)} 名候選動態追蹤，"
                  "詳見 data_source/market_scanner.py 說明）")
        elif get("universe.auto_scan", False) and get("data_source.provider") != "fugle":
            print("[Orchestrator] 提醒：universe.auto_scan=true，但目前資料來源不是 fugle，"
                  "全市場掃描目前只支援 fugle（REST全市場端點），暫不啟動")
        print(f"[Orchestrator] 系統啟動，追蹤 {len(self._watchlist())} 檔標的，"
              f"資料來源={get('data_source.provider', 'mock')}"
              "（如果這裡顯示mock但你要接真實資料，請檢查 config/settings.yaml 的 "
              "data_source.provider 是否為 fugle——每次解壓縮新版zip會覆蓋回預設模板，"
              "你自己填的設定需要重新貼回去）")

    def run(self):
        self.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.ds.disconnect()


if __name__ == "__main__":
    Orchestrator().run()
