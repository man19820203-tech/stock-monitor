"""
產生模擬的「昨日同時段累計成交量」CSV，讓 volume_ratio_vs_yesterday 這個指標
在 mock 模式下也能被完整測試（正式上線後，這份資料應該由收盤後的
歷史資料蒐集流程，或券商歷史API 產生，取代這支腳本）。

用法：
    python scripts/gen_mock_history.py
會在 data/history/{symbol}.csv 產生 09:00~13:30、每分鐘一筆的模擬累計量。
"""
import csv
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config_loader import get

OUT_DIR = Path(__file__).parent.parent / "data" / "history"


def gen_for_symbol(symbol: str):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    t = datetime.strptime("09:00", "%H:%M")
    end = datetime.strptime("13:30", "%H:%M")
    cum = 0
    rows = []
    while t <= end:
        cum += random.randint(2000, 8000)
        rows.append({"time": t.strftime("%H:%M"), "cum_volume": cum})
        t += timedelta(minutes=1)
    with open(OUT_DIR / f"{symbol}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["time", "cum_volume"])
        w.writeheader()
        w.writerows(rows)
    print(f"已產生 {symbol} 的模擬歷史資料 -> {OUT_DIR / f'{symbol}.csv'}")


if __name__ == "__main__":
    for sym in get("universe.watchlist", []):
        gen_for_symbol(sym)
