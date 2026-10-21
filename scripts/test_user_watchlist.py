"""
儀表板自選股（加入/移除、WebSocket 名額分配）測試：

    python scripts/test_user_watchlist.py

不需要連線：用假資料來源、暫存檔，不會動到 data/user_watchlist.json。
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import user_watchlist
user_watchlist._PATH = Path(tempfile.mkdtemp()) / "user_watchlist.json"

import main
import state_store
from config_loader import get
from models import Tick
from market_time import now_tw


class FakeDS:
    def __init__(self):
        self.subscribed = set()

    def subscribe(self, symbols, cb):
        self.subscribed |= set(symbols)

    def unsubscribe(self, symbols):
        self.subscribed -= set(symbols)

    def get_quote_tick(self, s):
        if s == "9999":
            raise RuntimeError("404 Not Found")
        return Tick(symbol=s, name=f"名{s}", ts=now_tw(), price=100, open=100, high=100,
                    low=100, prev_close=100, volume=0, turnover=0)

    def get_intraday_candles(self, s):
        return []


main.build_data_source = FakeDS
o = main.Orchestrator()
o._warm_up = lambda symbols: None           # 暖機（回補/初始報價）另外有測試，這裡只看名額分配
get("universe")["watchlist"] = []            # 不受你設定檔裡的 watchlist 影響
MAX = get("universe.scan.max_websocket_subscriptions", 5)
assert MAX == 5, "這個測試假設免費方案 5 檔上限"


def cands(*syms):
    return [{"symbol": s, "name": s} for s in syms]


def row(sym, score):
    state_store.upsert(sym, {"symbol": sym, "long_score": score, "short_score": 0})


# 掃描給 5 檔（1001~1005，分數由高到低；備援 1006 在下一輪才出現）
o._apply_candidates(cands("1001", "1002", "1003", "1004", "1005"), "測試")
for sym, sc in zip(["1001", "1002", "1003", "1004", "1005"], [90, 80, 70, 60, 50]):
    row(sym, sc)
assert o.ds.subscribed == {"1001", "1002", "1003", "1004", "1005"}

# 1. 名額滿時加入自選股：擠掉分數最低的候選 1005，總訂閱數維持 5
r = o.add_user_symbol("2330")
assert r["ok"], r
assert o.ds.subscribed == {"1001", "1002", "1003", "1004", "2330"}, o.ds.subscribed
assert user_watchlist.user_symbols() == ["2330"]
assert "1005" not in state_store.get_all()

# 2. 重複加入、無效代號、查無代號
assert o.add_user_symbol("2330")["ok"] and user_watchlist.user_symbols() == ["2330"]
assert not o.add_user_symbol("ab<script>")["ok"]
assert not o.add_user_symbol("9999")["ok"] and "9999" not in user_watchlist.user_symbols()

# 3. 加入的剛好是掃描候選：直接轉成自選，不重新訂閱、不擠掉別人
r = o.add_user_symbol("1001")
assert r["ok"] and o.ds.subscribed == {"1001", "1002", "1003", "1004", "2330"}
assert "1001" not in o._dynamic_symbols and "1001" in user_watchlist.user_symbols()

# 4. 下一輪掃描：候選只補滿剩下的 3 個名額，不會把自選股退訂
o._apply_candidates(cands("1002", "1003", "1006", "1004", "1005"), "測試")
assert o.ds.subscribed == {"1001", "2330", "1002", "1003", "1006"}, o.ds.subscribed

# 5. 自選滿 5 檔後不能再加
for s in ["1101", "1301", "1303"]:
    assert o.add_user_symbol(s)["ok"]
assert len(o.ds.subscribed) == 5 and not o._dynamic_symbols
r = o.add_user_symbol("2454")
assert not r["ok"] and "上限" in r["message"], r

# 6. 移除自選股：立刻用最近一次掃描結果補回名額
r = o.remove_user_symbol("2330")
assert r["ok"], r
assert "2330" not in o.ds.subscribed and len(o.ds.subscribed) == 5
assert o._dynamic_symbols == {"1002"}, o._dynamic_symbols
assert not o.remove_user_symbol("2330")["ok"]

# 7. 設定檔 watchlist 裡的不能從網頁移除
get("universe")["watchlist"] = ["2603"]
assert not o.remove_user_symbol("2603")["ok"]

print("=== PASS: 自選股加入/移除與名額分配全部正確 ===")
