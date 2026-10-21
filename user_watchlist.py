"""
使用者從儀表板輸入的自選股（跟 config/settings.yaml 的 universe.watchlist 一樣是「一定要追蹤」的股票，
只是可以在網頁上隨時加減，不用改設定檔、不用重開程式）。

存在 data/user_watchlist.json，重開程式會保留，直到你在儀表板上移除。

「固定追蹤」= 設定檔 watchlist + 這裡的自選股，兩者都佔 WebSocket 訂閱名額，
全市場掃描候選只補滿剩下的名額（見 pinned_symbols() 的使用處）。
"""
import json
import threading
from pathlib import Path

from config_loader import get

_PATH = Path(__file__).parent / "data" / "user_watchlist.json"
_lock = threading.RLock()


def _load() -> list:
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
        return [str(s) for s in data.get("symbols", [])]
    except FileNotFoundError:
        return []
    except Exception as e:
        print(f"[user_watchlist] 讀取 {_PATH} 失敗（當作沒有自選股）: {e}")
        return []


def _save(symbols: list):
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(json.dumps({"symbols": symbols}, ensure_ascii=False), encoding="utf-8")


def user_symbols() -> list:
    with _lock:
        return _load()


def config_symbols() -> list:
    return [str(s) for s in (get("universe.watchlist", []) or [])]


def pinned_symbols() -> list:
    """固定追蹤清單：設定檔 watchlist 在前，儀表板自選股在後（不重複）。"""
    out = config_symbols()
    for s in user_symbols():
        if s not in out:
            out.append(s)
    return out


def add(symbol: str):
    with _lock:
        symbols = _load()
        if symbol not in symbols:
            symbols.append(symbol)
            _save(symbols)


def remove(symbol: str):
    with _lock:
        symbols = _load()
        if symbol in symbols:
            symbols.remove(symbol)
            _save(symbols)
