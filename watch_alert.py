"""
關注提醒：從目前追蹤中的股票裡，挑出「劇本符合 + 分數達門檻」的，
交給儀表板跳出桌面通知／頁面彈窗／音效。

條件設定在 config/settings.yaml 的 watch_alert 區塊：
    watch_alert:
      enabled: true
      scenarios: ["強勢突破", "V型反轉", "跌深反彈"]
      min_score: 70

分數依劇本方向取：多方劇本看多方分數、空方劇本看空方分數，
沒有方向的劇本（例如箱型震盪）取兩者較高的。
這裡只負責「篩選」，同一檔同一劇本一天只提醒一次的去重交給前端處理
（前端知道使用者看過哪些提醒）。
"""


def _alert_score(state: dict) -> float:
    direction = state.get("direction")
    if direction == "long":
        return state.get("long_score", 0)
    if direction == "short":
        return state.get("short_score", 0)
    return max(state.get("long_score", 0), state.get("short_score", 0))


def match_watch_alerts(states: list, cfg) -> list:
    """回傳符合條件的股票清單（每筆多一個 alert_score 欄位），分數高的排前面。"""
    if not cfg or not cfg.get("enabled", False):
        return []
    scenarios = set(cfg.get("scenarios") or [])
    min_score = cfg.get("min_score", 70)

    hits = []
    for state in states:
        if state.get("scenario") not in scenarios:
            continue
        score = _alert_score(state)
        if score >= min_score:
            hits.append({**state, "alert_score": score})
    hits.sort(key=lambda h: h["alert_score"], reverse=True)
    return hits
