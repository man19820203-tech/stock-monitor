"""
Telegram 機器人：盤中主動推播關注提醒，並用指令查詢排名／個股／管理自選股。
雲端（GitHub Actions）沒有網頁儀表板可看，手機就靠這個；本機跑 run_all.py 時也會一起啟動。

    推播：符合 config watch_alert 條件（劇本 + 分數門檻）的股票，同一檔同一劇本一天只推一次；
          另外盤中每 telegram.summary_interval_minutes 分鐘推一次排名摘要
    指令：/top /list /stock 2330 /add 2330 /remove 2330 /watch /mute /unmute /status /help

機器人是「主動往外連」Telegram（long polling），不用開 port、不用固定 IP。
只回應、只推播給設定的 chat_id；其他人傳訊息只會拿到他自己的 chat ID（方便第一次設定時查自己的 ID）。
金鑰設定見 config/secrets.example.yaml。
"""
import html
import threading
import time
from datetime import timedelta
from typing import Optional

import requests

import state_store
from config_loader import get, load_config
from market_time import now_tw
from user_watchlist import pinned_symbols
from watch_alert import match_watch_alerts

_API = "https://api.telegram.org/bot{token}/{method}"
_MAX_LEN = 4000   # Telegram 單則上限 4096 字，留點餘裕
_MARKET_CLOSE_MIN = 13 * 60 + 30   # 13:30 收盤（定時摘要只在盤中送）

HELP_TEXT = (
    "<b>台股當沖監控 指令</b>\n"
    "/top － 大盤＋多方／空方排名\n"
    "/list － 目前追蹤中的全部股票\n"
    "/stock 2330 － 個股分數、劇本、解讀\n"
    "/add 2330 － 加入自選股（一定追蹤）\n"
    "/remove 2330 － 移除自選股\n"
    "/watch － 自選股清單\n"
    "/mute ／ /unmute － 暫停／恢復今天的提醒和定時摘要\n"
    "/status － 系統狀態\n"
    "\n分數僅供排序參考，非買賣建議。"
)


def _num(v, fmt="{:,.2f}", none="—"):
    return none if v is None else fmt.format(v)


def _pct(v):
    return "—" if v is None else f"{v:+.2f}%"


def _stock_line(s: dict, score_key: str) -> str:
    """排名用的一行摘要：代號 名稱 價格 漲跌 分數 劇本。
    放在哪一欄看多空分數哪個高，劇本是另一套規則判斷的，兩者可能相反（例如多方欄裡的「反彈空」），
    這時加註提醒，不然看起來像系統算錯。"""
    column = "long" if score_key == "long_score" else "short"
    direction = s.get("direction")
    conflict = " ⚠️方向不一致" if direction in ("long", "short") and direction != column else ""
    return (f"<code>{s['symbol']}</code> {html.escape(str(s.get('name') or ''))} "
            f"{_num(s.get('price'))} ({_pct(s.get('change_pct'))}) "
            f"<b>{s.get(score_key, 0)}</b>分｜{html.escape(str(s.get('scenario') or ''))}{conflict}")


def _with_conflict_note(lines: list) -> str:
    if any("⚠️方向不一致" in line for line in lines):
        lines.append("\n⚠️方向不一致：分數偏這一邊，但劇本判斷是另一邊，代表方向不明確")
    return "\n".join(lines)


def _group_line(groups: list, n: int = 3) -> str:
    """族群強弱只用「追蹤中的股票」算（免費方案只追蹤 5 檔，一個族群常常只有 1 檔），
    所以標明是追蹤中族群、附上檔數，而且只列上漲的，下跌的族群不叫強勢。"""
    up = [g for g in groups if g["avg_change_pct"] > 0][:n]
    if not up:
        return ""
    return "\n🔥 追蹤中強勢族群：" + "、".join(
        f"{html.escape(g['group'])} {_pct(g['avg_change_pct'])}（{len(g['members'])}檔）" for g in up)


def _stock_detail(s: dict, title: str = "") -> str:
    fh, fl = s.get("first_high"), s.get("first_low")
    lines = []
    if title:
        lines.append(title)
    lines += [
        f"<b>{s['symbol']} {html.escape(str(s.get('name') or ''))}</b>  "
        f"{_num(s.get('price'))} ({_pct(s.get('change_pct'))})",
        f"劇本：{html.escape(str(s.get('scenario') or '—'))}｜訊號 {s.get('signals', '—')}",
        f"多方 <b>{s.get('long_score', 0)}</b> ／ 空方 <b>{s.get('short_score', 0)}</b>",
        f"成交額 {_num(s.get('turnover_billion'), '{:,.1f}')} 億｜量比昨日 "
        f"{_num(s.get('volume_ratio_vs_yesterday'), '{:.2f}')}x｜相對大盤 {_pct(s.get('relative_strength_pct'))}",
    ]
    if fh is not None and fl is not None:
        lines.append(f"第一高 {_num(fh)} ／ 第一低 {_num(fl)}")
    if s.get("narrative"):
        lines.append(f"\n{html.escape(s['narrative'])}")
    return "\n".join(lines)


class TelegramBot:
    def __init__(self, token: str, chat_id: str = ""):
        self.token = token
        self.chat_id = str(chat_id).strip()
        self._stop = threading.Event()
        self._session = requests.Session()
        self._sent_alerts: set = set()     # (日期, 代號, 劇本)：同一檔同一劇本一天只推一次
        self._muted_on = None              # /mute 的日期，隔天自動恢復
        self._last_summary_slot = None     # (日期, 第幾個時段)：定時摘要每個時段只送一次
        self._first_summary_check = None   # 第一次檢查定時摘要的時間（≈啟動時間）
        self.alert_count = 0
        self.stop_at = None                # cloud_run.py 設定的自動結束時間，/status 顯示用

    @classmethod
    def from_config(cls) -> Optional["TelegramBot"]:
        """沒設 bot_token 或 telegram.enabled=false 就回傳 None（系統照常運作，只是沒有機器人）。"""
        if not get("telegram.enabled", True):
            return None
        token = str(get("telegram.bot_token", "") or "").strip()
        if not token:
            return None
        return cls(token, str(get("telegram.chat_id", "") or ""))

    # ---------- 對外 ----------
    def start(self):
        threading.Thread(target=self._poll_commands, daemon=True).start()
        if self.chat_id:
            threading.Thread(target=self._alert_loop, daemon=True).start()
        else:
            print("[Telegram] 尚未設定 chat_id，不會推播；請先傳任意訊息給機器人取得你的 chat ID")

    def stop(self):
        self._stop.set()

    def send(self, text: str, chat_id: str = "") -> bool:
        chat_id = chat_id or self.chat_id
        if not chat_id:
            return False
        ok = True
        for i in range(0, len(text), _MAX_LEN):
            try:
                r = self._call("sendMessage", chat_id=chat_id, text=text[i:i + _MAX_LEN],
                               parse_mode="HTML", disable_web_page_preview=True)
                ok = ok and r.get("ok", False)
            except Exception as e:
                print(f"[Telegram] 發送失敗: {e}")
                ok = False
        return ok

    # ---------- Telegram API ----------
    def _call(self, method: str, timeout: float = 15, retries: int = 3, **params) -> dict:
        """每次開新連線、失敗重試。不共用 self._session：GitHub Actions 的機器會悄悄切斷閒置幾分鐘的連線，
        重用舊連線送出的訊息會卡到逾時才失敗（實測收盤訊息就是這樣送不出去）。
        _session 只給 _get_updates 用，它每 25 秒一輪不會閒置。"""
        for attempt in range(1, retries + 1):
            try:
                resp = requests.post(_API.format(token=self.token, method=method),
                                     json=params, timeout=timeout)
                break
            except requests.RequestException as e:
                if attempt == retries:
                    raise
                print(f"[Telegram] {method} 連線失敗，重試第 {attempt} 次: {e}")
                time.sleep(2 * attempt)
        data = resp.json()
        if not data.get("ok"):
            print(f"[Telegram] {method} 失敗: {data.get('description')}")
        return data

    def _poll_commands(self):
        """long polling 收指令。啟動時先跳過累積的舊訊息（例如昨晚傳的），避免一開機就回覆一堆過期指令。"""
        offset = None
        try:
            last = self._call("getUpdates", offset=-1, timeout=10).get("result", [])
            if last:
                offset = last[-1]["update_id"] + 1
        except Exception as e:
            print(f"[Telegram] 初始化 getUpdates 失敗: {e}")

        while not self._stop.is_set():
            try:
                for u in self._get_updates(offset):
                    offset = u["update_id"] + 1
                    msg = u.get("message") or {}
                    if msg.get("text"):
                        self._handle_message(msg)
            except Exception as e:
                print(f"[Telegram] 收訊息失敗（5 秒後重試）: {e}")
                time.sleep(5)

    def _get_updates(self, offset) -> list:
        params = {"timeout": 25, "allowed_updates": ["message"]}
        if offset is not None:
            params["offset"] = offset
        resp = self._session.post(_API.format(token=self.token, method="getUpdates"),
                                  json=params, timeout=35)
        data = resp.json()
        if not data.get("ok"):
            # 409 = 同一個 Token 有另一個程式也在收訊息（例如本機和雲端同時開著）
            raise RuntimeError(data.get("description"))
        return data.get("result", [])

    # ---------- 推播 ----------
    def _alert_loop(self):
        while not self._stop.is_set():
            try:
                self._push_watch_alerts()
                self._push_summary()
            except Exception as e:
                print(f"[Telegram] 推播檢查失敗: {e}")
            self._stop.wait(get("display.refresh_sec", 3))

    def _push_summary(self):
        """盤中定時推一次排名摘要（預設每 30 分鐘：09:30、10:00…13:00），讓你知道系統還活著。
        對齊整點／半點，只在每個時段開頭 5 分鐘內送；中途才啟動的話等下一個時段，不會跟啟動通知擠在一起。
        13:30 收盤那次不送，結束時的收盤摘要已經有。"""
        interval = get("telegram.summary_interval_minutes", 30) or 0
        if interval <= 0:
            return
        now = now_tw()
        if self._first_summary_check is None:
            self._first_summary_check = now
        if self._muted_on == now.date():
            return
        h, m = map(int, str(get("first_range.start_time", "09:00")).split(":"))
        minutes = now.hour * 60 + now.minute - (h * 60 + m)
        if minutes < interval or now.hour * 60 + now.minute >= _MARKET_CLOSE_MIN or minutes % interval >= 5:
            return
        slot = (now.date(), minutes // interval)
        if slot == self._last_summary_slot:
            return
        slot_start = now.replace(second=0, microsecond=0) - timedelta(minutes=minutes % interval)
        if self._first_summary_check > slot_start:
            self._last_summary_slot = slot   # 這個時段開始後才啟動（剛送過啟動通知），跳過等下一個
            return
        self._last_summary_slot = slot
        self.send(f"⏱ <b>盤中摘要 {now:%H:%M}</b>（今天已推播 {self.alert_count} 則提醒）\n\n{self.top_text()}")

    def _push_watch_alerts(self):
        load_config(force_reload=True)   # 跟儀表板一樣，改設定檔不用重開
        if not get("telegram.push_watch_alerts", True):
            return
        today = now_tw().date()
        if self._muted_on == today:
            return
        hits = match_watch_alerts(list(state_store.get_all().values()), get("watch_alert"))
        for h in hits:
            key = (today, h["symbol"], h["scenario"])
            if key in self._sent_alerts:
                continue
            self._sent_alerts.add(key)
            title = f"🔔 <b>關注提醒｜{html.escape(h['scenario'])}</b>（{h['alert_score']} 分）"
            if self.send(_stock_detail(h, title)):
                self.alert_count += 1

    # ---------- 指令 ----------
    def _handle_message(self, msg: dict):
        chat_id = str(msg["chat"]["id"])
        if chat_id != self.chat_id:
            self.send(f"你的 chat ID 是 <code>{chat_id}</code>\n"
                      "如果你是這個機器人的擁有者，請把它設成 TELEGRAM_CHAT_ID。", chat_id=chat_id)
            return
        parts = msg["text"].strip().split()
        cmd = parts[0].split("@")[0].lower()    # 群組裡指令會帶 @機器人名稱
        arg = parts[1] if len(parts) > 1 else ""
        handler = {
            "/start": self._cmd_help, "/help": self._cmd_help,
            "/top": lambda _arg: self.top_text(), "/list": self._cmd_list,
            "/stock": self._cmd_stock, "/s": self._cmd_stock,
            "/add": self._cmd_add, "/remove": self._cmd_remove, "/del": self._cmd_remove,
            "/watch": self._cmd_watch, "/mute": self._cmd_mute, "/unmute": self._cmd_unmute,
            "/status": self._cmd_status,
        }.get(cmd)
        if handler is None and cmd.isdigit():
            handler, arg = self._cmd_stock, cmd   # 直接傳代號 = 查個股
        try:
            self.send(handler(arg) if handler else "看不懂這個指令，輸入 /help 看用法")
        except Exception as e:
            self.send(f"指令執行失敗：{html.escape(str(e))}")

    def _cmd_help(self, _arg):
        return HELP_TEXT

    def top_text(self) -> str:
        from web.app import api_ranking   # 跟儀表板同一套排序（每檔只放在它比較強的那一欄）
        data = api_ranking()
        n = get("telegram.top_n", 5)
        m = data["market"]
        lines = [f"<b>大盤</b> {_pct(m.get('index_change_pct'))}（{m.get('label')}）"]
        lines.append("\n📈 <b>多方</b>")
        lines += [_stock_line(s, "long_score") for s in data["long_top"][:n]] or ["（無）"]
        lines.append("\n📉 <b>空方</b>")
        lines += [_stock_line(s, "short_score") for s in data["short_top"][:n]] or ["（無）"]
        group_line = _group_line(data["group_strength"])
        if group_line:
            lines.append(group_line)
        return _with_conflict_note(lines)

    def _cmd_list(self, _arg):
        rows = sorted(state_store.get_all().values(),
                      key=lambda s: max(s["long_score"], s["short_score"]), reverse=True)
        if not rows:
            return "目前還沒有追蹤中的股票（剛啟動或還在等掃描結果）"
        pinned = set(pinned_symbols())
        lines = [f"<b>追蹤中 {len(rows)} 檔</b>（⭐ = 自選）"]
        for s in rows:
            key = "long_score" if s["long_score"] >= s["short_score"] else "short_score"
            lines.append(("⭐" if s["symbol"] in pinned else "・") + _stock_line(s, key))
        return _with_conflict_note(lines)

    def _cmd_stock(self, arg):
        symbol = arg.strip().upper()
        if not symbol:
            return "用法：/stock 2330"
        s = state_store.get_all().get(symbol)
        if s is None:
            return f"{symbol} 目前沒有在追蹤。要一直追蹤可以用 /add {symbol}"
        return _stock_detail(s)

    def _controller(self):
        ctrl = state_store.get_controller()
        if ctrl is None:
            raise RuntimeError("資料處理還沒啟動")
        return ctrl

    def _cmd_add(self, arg):
        if not arg:
            return "用法：/add 2330"
        return html.escape(self._controller().add_user_symbol(arg)["message"])

    def _cmd_remove(self, arg):
        if not arg:
            return "用法：/remove 2330"
        return html.escape(self._controller().remove_user_symbol(arg)["message"])

    def _cmd_watch(self, _arg):
        info = self._controller().user_watchlist_info()
        user, cfg = info["user"], info["config"]
        if not user and not cfg:
            return f"自選清單是空的（最多 {info['max']} 檔），用 /add 2330 加入"
        lines = [f"<b>自選股</b>（{len(user) + len(cfg)}/{info['max']}）"]
        lines += [f"・{s}（設定檔）" for s in cfg] + [f"・{s}" for s in user]
        return "\n".join(lines)

    def _cmd_mute(self, _arg):
        self._muted_on = now_tw().date()
        return "🔕 今天的提醒和定時摘要已暫停，明天自動恢復（/unmute 立即恢復）"

    def _cmd_unmute(self, _arg):
        self._muted_on = None
        return "🔔 提醒推播已恢復"

    def _cmd_status(self, _arg):
        market = state_store.get_market()
        lines = [
            f"<b>系統狀態</b>（{now_tw():%m/%d %H:%M:%S}）",
            f"資料來源：{get('data_source.provider')}",
            f"追蹤中：{len(state_store.get_all())} 檔",
            f"大盤最後更新：{(market.get('updated_at') or '')[11:19] or '—'}",
            f"今日已推播：{self.alert_count} 則" + ("（已暫停）" if self._muted_on == now_tw().date() else ""),
        ]
        if self.stop_at:
            lines.append(f"自動結束：{self.stop_at:%H:%M}")
        return "\n".join(lines)
