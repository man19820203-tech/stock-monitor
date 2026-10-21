"""
盤中儀表板：三欄式（多方TOP10｜空方TOP10｜警報），加上早上打開只需要看一眼的市場摘要。

前端每 N 秒（config: display.refresh_sec）拉一次 /api/ranking，
所有排序/分數/劇本都是後端算好的，前端只負責顯示，不做任何運算。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import state_store
from config_loader import get, load_config
from user_watchlist import pinned_symbols
from watch_alert import match_watch_alerts

app = FastAPI(title="台股當沖即時監控")


@app.get("/api/ranking")
def api_ranking():
    load_config(force_reload=True)  # 支援後台熱更新參數
    pinned = set(pinned_symbols())
    all_states = {k: {**v, "pinned": k in pinned} for k, v in state_store.get_all().items()}
    market = state_store.get_market()
    groups = state_store.get_group_strength()

    # 每檔只放在它比較強的那一欄（多方分數 ≥ 空方分數 → 多方欄，反之空方欄）。
    # 以前兩欄都是「全部追蹤中的股票」只換排序，追蹤檔數少（免費方案 5 檔）時兩欄永遠一模一樣，
    # 強烈偏多的股票（例如多98／空40）也會出現在空方欄。
    long_list = sorted((s for s in all_states.values() if s["long_score"] >= s["short_score"]),
                       key=lambda s: s["long_score"], reverse=True)
    short_list = sorted((s for s in all_states.values() if s["short_score"] > s["long_score"]),
                        key=lambda s: s["short_score"], reverse=True)

    top_n_long = get("display.top_n_long", 10)
    top_n_short = get("display.top_n_short", 10)
    alert_th = get("display.alert_score_threshold", 80)

    alerts = [s for s in all_states.values()
              if s["long_score"] >= alert_th or s["short_score"] >= alert_th]
    alerts.sort(key=lambda s: max(s["long_score"], s["short_score"]), reverse=True)

    market_label = "偏多" if market.get("index_change_pct", 0) > 0.3 else (
        "偏空" if market.get("index_change_pct", 0) < -0.3 else "震盪")

    return {
        "market": {
            "index_change_pct": market.get("index_change_pct"),
            "label": market_label,
            "updated_at": market.get("updated_at"),
        },
        "group_strength": groups,
        # 自選股（設定檔 watchlist + 儀表板輸入的）不論排名都列出來
        "pinned": [all_states[s] for s in pinned_symbols() if s in all_states],
        "long_top": long_list[:top_n_long],
        "short_top": short_list[:top_n_short],
        "alerts": alerts[:10],
        # 關注提醒：符合 config watch_alert 條件的股票，前端據此跳通知／彈窗（去重在前端）
        "watch_hits": match_watch_alerts(list(all_states.values()), get("watch_alert")),
        "summary": {
            "best_long_count": len([s for s in long_list if s["long_score"] >= alert_th]),
            "best_short_count": len([s for s in short_list if s["short_score"] >= alert_th]),
        },
    }


class SymbolIn(BaseModel):
    symbol: str


def _controller_or_error():
    ctrl = state_store.get_controller()
    if ctrl is None:
        return None, JSONResponse(
            {"ok": False, "message": "資料處理沒有在同一個程式裡執行（請用 run_all.py / 一鍵啟動）"},
            status_code=503)
    return ctrl, None


@app.get("/api/watchlist")
def api_watchlist():
    ctrl, err = _controller_or_error()
    return err or ctrl.user_watchlist_info()


@app.post("/api/watchlist")
def api_watchlist_add(body: SymbolIn):
    ctrl, err = _controller_or_error()
    if err:
        return err
    result = ctrl.add_user_symbol(body.symbol)
    return JSONResponse(result, status_code=200 if result["ok"] else 400)


@app.delete("/api/watchlist/{symbol}")
def api_watchlist_remove(symbol: str):
    ctrl, err = _controller_or_error()
    if err:
        return err
    result = ctrl.remove_user_symbol(symbol)
    return JSONResponse(result, status_code=200 if result["ok"] else 400)


@app.get("/", response_class=HTMLResponse)
def index():
    return (Path(__file__).parent / "templates" / "dashboard.html").read_text(encoding="utf-8")
