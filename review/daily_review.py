"""
盤後自動檢討。

排程建議：每天 13:30（config: review.run_after_time）之後執行一次：
    python -m review.daily_review

會做的事：
  1. 把當天 state_store 裡累積的高分事件 log（訊號出現、突破成功/失敗）存成 JSON
  2. 統計：TOP候選（觸發過警報分數的股票）最後漲跌、多方/空方候選成功率
  3. 產出一段純文字摘要（規則統計，不是AI亂猜），供之後累積回測用

注意：這個檔案設計成「資料處理與檢討邏輯」都是確定性統計；
要接到 main.py 常駐流程需要在收盤時機呼叫 run_review(orchestrator)。
之後累積夠多天的 JSON，就可以另外寫一支批次程式，
分析『哪個訊號成功率最高、哪個劇本最常見』，逐步調整 config 權重。
"""
import json
from datetime import datetime
from pathlib import Path

from config_loader import get
import state_store


def run_review(orchestrator=None) -> dict:
    save_dir = Path(get("review.save_dir", "review/logs"))
    save_dir.mkdir(parents=True, exist_ok=True)

    all_states = state_store.get_all()
    daily_log = state_store.get_daily_log()

    long_candidates = [s for s in all_states.values() if s["long_score"] >= get("display.alert_score_threshold", 80)]
    short_candidates = [s for s in all_states.values() if s["short_score"] >= get("display.alert_score_threshold", 80)]

    def success_rate(candidates, direction):
        if not candidates:
            return None
        wins = 0
        for c in candidates:
            # 簡化版判斷：多方候選若當下 change_pct > 0 視為成功、空方候選若 change_pct < 0 視為成功
            # 待真實回測時，應改成「候選成立當下價位 vs 尾盤價位」的實際報酬計算
            if direction == "long" and c["change_pct"] > 0:
                wins += 1
            if direction == "short" and c["change_pct"] < 0:
                wins += 1
        return round(wins / len(candidates), 2)

    scenario_counts = {}
    for s in all_states.values():
        scenario_counts[s["scenario"]] = scenario_counts.get(s["scenario"], 0) + 1
    dominant_scenario = max(scenario_counts, key=scenario_counts.get) if scenario_counts else None

    report = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "dominant_scenario": dominant_scenario,
        "scenario_counts": scenario_counts,
        "long_candidate_count": len(long_candidates),
        "short_candidate_count": len(short_candidates),
        "long_success_rate": success_rate(long_candidates, "long"),
        "short_success_rate": success_rate(short_candidates, "short"),
        "alert_events": daily_log,
        "final_snapshot": all_states,
    }

    out_path = save_dir / f"{report['date']}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print(f"[daily_review] 今天屬於「{dominant_scenario}」為主的一天。")
    print(f"[daily_review] 多方候選成功率：{report['long_success_rate']}　"
          f"空方候選成功率：{report['short_success_rate']}")
    print(f"[daily_review] 已存檔：{out_path}")
    return report


if __name__ == "__main__":
    run_review()
