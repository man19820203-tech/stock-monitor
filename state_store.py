"""
全系統共用的執行期記憶體儲存（多執行緒安全）。
main.py 的資料處理迴圈負責寫入，web/app.py 的 API 負責讀出。
"""
import threading
from market_time import now_tw

_lock = threading.RLock()
_latest: dict = {}          # symbol -> {"state":..., "scores":..., "scenario":..., "narrative":...}
_market = {"index_change_pct": 0.0, "updated_at": None}
_group_strength: list = []
_daily_log: list = []       # 供盤後回測使用的事件紀錄
_controller = None          # 正在跑的 Orchestrator，讓網頁可以加/移除自選股（run_all.py 同一個程式內才有）


def register_controller(controller):
    global _controller
    _controller = controller


def get_controller():
    return _controller


def upsert(symbol: str, payload: dict):
    with _lock:
        payload["updated_at"] = now_tw().isoformat(timespec="seconds")
        _latest[symbol] = payload


def remove(symbol: str):
    """全市場掃描換候選股時，把輪出的舊候選從儀表板資料裡移除，
    避免網頁一直顯示已經不在追蹤的過期資料。"""
    with _lock:
        _latest.pop(symbol, None)


def get_all() -> dict:
    with _lock:
        return dict(_latest)


def set_market(index_change_pct: float):
    with _lock:
        _market["index_change_pct"] = index_change_pct
        _market["updated_at"] = now_tw().isoformat(timespec="seconds")


def get_market() -> dict:
    with _lock:
        return dict(_market)


def set_group_strength(groups: list):
    with _lock:
        global _group_strength
        _group_strength = groups


def get_group_strength() -> list:
    with _lock:
        return list(_group_strength)


def log_event(event: dict):
    with _lock:
        event["ts"] = now_tw().isoformat(timespec="seconds")
        _daily_log.append(event)


def get_daily_log() -> list:
    with _lock:
        return list(_daily_log)


def clear_daily_log():
    with _lock:
        _daily_log.clear()
