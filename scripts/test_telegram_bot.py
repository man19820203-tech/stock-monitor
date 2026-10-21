"""
Telegram 機器人＋雲端執行入口測試：

    python scripts/test_telegram_bot.py

不需要真的 Telegram Bot、不需要富果 API、不需要開盤：Telegram API 換成假的（記錄送出的訊息），
資料來源用 mock。最後一段會用 mock 資料實際跑 cloud_run.py 的流程約 20 秒。
"""
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml

import config_loader
import state_store
from config_loader import get, load_config


class FakeBot:
    """把 TelegramBot 的網路呼叫換掉：送出的訊息記在 sent，收到的指令從 inbox 拿。"""

    def __init__(self, bot):
        self.sent = []   # (chat_id, text)
        bot._call = self._call
        bot._get_updates = lambda offset: []

    def _call(self, method, timeout=15, **params):
        if method == "sendMessage":
            self.sent.append((params["chat_id"], params["text"]))
        return {"ok": True, "result": []}


def make_bot():
    from telegram_bot import TelegramBot
    bot = TelegramBot("TEST:TOKEN", "1001")
    return bot, FakeBot(bot)


def msg(text, chat_id=1001):
    return {"chat": {"id": chat_id}, "text": text}


def row(symbol, scenario, direction, long_score, short_score, **kw):
    return {"symbol": symbol, "name": f"股{symbol}", "price": 100.0, "change_pct": 1.5,
            "turnover_billion": 12.3, "volume_ratio_vs_yesterday": 1.8, "relative_strength_pct": 0.8,
            "first_high": 101.0, "first_low": 98.0, "scenario": scenario, "direction": direction,
            "signals": "4/5", "signal_labels": [], "long_score": long_score, "short_score": short_score,
            "narrative": "測試解讀 <不是HTML>", "group": None, **kw}


# 1. 金鑰：環境變數蓋過設定檔，settings.yaml 本身不能有金鑰
raw = yaml.safe_load(Path(config_loader._CONFIG_PATH).read_text(encoding="utf-8"))
assert raw["data_source"]["fugle"]["api_token"] == "", "settings.yaml 不可以寫死富果 API Key"
os.environ["FUGLE_API_TOKEN"] = "env-token"
os.environ["TELEGRAM_CHAT_ID"] = "777"
load_config(force_reload=True)
assert get("data_source.fugle.api_token") == "env-token"
assert get("telegram.chat_id") == "777"
del os.environ["FUGLE_API_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]
load_config(force_reload=True)

# 2. 推播：符合 watch_alert 的才推，同一檔同一劇本只推一次，/mute 後不推
for s in ("A", "B", "C"):
    state_store.remove(s)
state_store.upsert("A", row("A", "強勢突破", "long", 85, 20))
state_store.upsert("B", row("B", "假突破", "long", 90, 10))     # 劇本不在提醒清單
state_store.upsert("C", row("C", "V型反轉", "long", 50, 10))    # 分數不夠
bot, fake = make_bot()
bot._push_watch_alerts()
bot._push_watch_alerts()
assert len(fake.sent) == 1 and "股A" in fake.sent[0][1] and "強勢突破" in fake.sent[0][1], fake.sent
assert "&lt;不是HTML&gt;" in fake.sent[0][1], "解讀文字要做 HTML 跳脫，不然 Telegram 會拒收"
assert bot.alert_count == 1
state_store.upsert("A", row("A", "V型反轉", "long", 85, 20))    # 換劇本 → 再推一次
bot._push_watch_alerts()
assert len(fake.sent) == 2
bot._handle_message(msg("/mute"))
state_store.upsert("C", row("C", "V型反轉", "long", 95, 10))
bot._push_watch_alerts()
assert not any("股C" in t for _, t in fake.sent), "靜音中不應推播"
bot._handle_message(msg("/unmute"))
bot._push_watch_alerts()
assert "股C" in fake.sent[-1][1]

# 3. 指令
bot, fake = make_bot()
bot._handle_message(msg("/top", chat_id=555))   # 陌生人只會拿到自己的 chat ID
assert fake.sent[-1] == ("555", fake.sent[-1][1]) and "555" in fake.sent[-1][1] and "多方" not in fake.sent[-1][1]
bot._handle_message(msg("/top"))
assert "多方" in fake.sent[-1][1] and "股A" in fake.sent[-1][1]
bot._handle_message(msg("/stock A"))
assert "股A" in fake.sent[-1][1] and "第一高" in fake.sent[-1][1]
bot._handle_message(msg("/stock 9999"))
assert "沒有在追蹤" in fake.sent[-1][1]
bot._handle_message(msg("/list"))
assert "追蹤中" in fake.sent[-1][1]
bot._handle_message(msg("/status@my_bot"))     # 群組裡帶 @機器人名稱
assert "系統狀態" in fake.sent[-1][1]
bot._handle_message(msg("/xyz"))
assert "/help" in fake.sent[-1][1]

# 3b. 劇本方向跟欄位相反要加註；族群只列上漲的，並標明是追蹤中族群
from telegram_bot import _stock_line, _group_line
assert "⚠️方向不一致" in _stock_line(row("X", "反彈空", "short", 55, 40), "long_score")
assert "⚠️方向不一致" not in _stock_line(row("X", "強勢突破", "long", 85, 20), "long_score")
assert "⚠️方向不一致" not in _stock_line(row("X", "無方向／禁止交易", None, 71, 20), "long_score")
assert "⚠️方向不一致" in _stock_line(row("X", "V型反轉", "long", 30, 80), "short_score")
g = lambda name, pct, n: {"group": name, "avg_change_pct": pct, "members": [{}] * n}
line = _group_line([g("載板", 4.27, 1), g("被動元件", 3.63, 2), g("PCB", -3.23, 1)])
assert "追蹤中強勢族群" in line and "載板 +4.27%（1檔）" in line and "PCB" not in line, line
assert _group_line([g("PCB", -3.23, 1)]) == "", "全部下跌時不顯示強勢族群"
state_store.remove("A")
state_store.upsert("D", row("D", "反彈空", "short", 55, 40))
bot._handle_message(msg("/top"))
assert "⚠️方向不一致" in fake.sent[-1][1] and "代表方向不明確" in fake.sent[-1][1]
state_store.remove("D")


class FakeController:
    def __init__(self):
        self.calls = []

    def add_user_symbol(self, s):
        self.calls.append(("add", s))
        return {"ok": True, "message": f"已加入 {s}"}

    def remove_user_symbol(self, s):
        self.calls.append(("remove", s))
        return {"ok": True, "message": f"已移除 {s}"}

    def user_watchlist_info(self):
        return {"user": ["2330"], "config": [], "max": 5}


ctrl = FakeController()
state_store.register_controller(ctrl)
bot._handle_message(msg("/add 2330"))
bot._handle_message(msg("/remove 2330"))
bot._handle_message(msg("/watch"))
assert ctrl.calls == [("add", "2330"), ("remove", "2330")]
assert "2330" in fake.sent[-1][1]
state_store.register_controller(None)
for s in ("A", "B", "C"):
    state_store.remove(s)

# 4. 休市日判斷
import cloud_run


class FakeResp:
    def json(self):
        return [{"Name": "國曆新年開始交易日", "Date": "1150102"},
                {"Name": "市場無交易，僅辦理結算交割作業", "Date": "1150212"},
                {"Name": "端午節", "Date": "1150619"}]


real_get = cloud_run.requests.get
cloud_run.requests.get = lambda *a, **k: FakeResp()
assert cloud_run.is_trading_day(date(2026, 1, 2))        # 開始交易日 → 有開盤
assert not cloud_run.is_trading_day(date(2026, 2, 12))   # 無交易
assert not cloud_run.is_trading_day(date(2026, 6, 19))   # 端午節
assert cloud_run.is_trading_day(date(2026, 6, 18))       # 一般平日
assert not cloud_run.is_trading_day(date(2026, 6, 20))   # 星期六
cloud_run.requests.get = lambda *a, **k: (_ for _ in ()).throw(OSError("network down"))
assert cloud_run.is_trading_day(date(2026, 6, 18)), "查不到日曆時要照常執行"
cloud_run.requests.get = real_get

# 5. 實際跑一次 cloud_run 流程（mock 資料、約 20 秒）：啟動訊息 → 追蹤到股票 → 結束訊息
cfg = yaml.safe_load(Path(config_loader._CONFIG_PATH).read_text(encoding="utf-8"))
cfg["data_source"]["provider"] = "mock"
cfg["universe"]["watchlist"] = list(cfg["universe"]["stock_names"].keys())
tmp = Path(tempfile.mkdtemp()) / "settings.yaml"
tmp.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
config_loader._CONFIG_PATH = tmp

holder = {}


def fake_from_config():
    bot, fake = make_bot()
    holder["bot"], holder["fake"] = bot, fake
    return bot


cloud_run.TelegramBot.from_config = staticmethod(fake_from_config)
sys.argv = ["cloud_run.py", "--minutes", "0.33"]
assert cloud_run.main() == 0
texts = [t for _, t in holder["fake"].sent]
assert "已啟動" in texts[0], texts
assert "結束" in texts[-1] and "多方" in texts[-1], texts[-1]
assert len(state_store.get_all()) == len(cfg["universe"]["watchlist"]), state_store.get_all().keys()

print(f"=== PASS: Telegram 機器人與雲端執行入口全部正確（mock 流程送出 {len(texts)} 則訊息）===")
sys.stdout.flush()
os._exit(0)
