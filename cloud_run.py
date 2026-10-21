"""
雲端執行入口：GitHub Actions 每個交易日早上自動跑一次（見 .github/workflows/monitor.yml），
盤中用 Telegram 推播提醒、回應指令，收盤後自動結束。不開網頁儀表板（雲端機器連不進去）。

    python cloud_run.py               # 正常模式：休市日直接結束；等到 cloud.start_time 開始，cloud.stop_time 結束
    python cloud_run.py --minutes 10  # 測試模式：馬上開始、跑 10 分鐘就結束，不檢查休市日（收盤後也能測 Telegram）

金鑰由環境變數傳入（FUGLE_API_TOKEN / TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID），
本機測試也可以寫在 config/secrets.yaml。
"""
import argparse
import os
import sys
import time
from datetime import datetime, date, timedelta

import requests

from config_loader import get, load_config
from main import Orchestrator
from market_time import now_tw, TW_TZ
from telegram_bot import TelegramBot

TWSE_HOLIDAY_URL = "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule"


def is_trading_day(d: date) -> bool:
    """週末不開盤；平日查證交所休市日曆。日曆裡也有「開始交易日／最後交易日」這種有開盤的日子，
    名稱不含「交易日」的才是休市（含「市場無交易，僅辦理結算交割」）。查不到日曆時當作有開盤，寧可多跑。"""
    if d.weekday() >= 5:
        return False
    roc_date = f"{d.year - 1911}{d:%m%d}"
    try:
        rows = requests.get(TWSE_HOLIDAY_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=20).json()
        for r in rows:
            if r.get("Date") == roc_date and "交易日" not in r.get("Name", ""):
                print(f"[cloud_run] 今天 {d} 休市：{r.get('Name')}")
                return False
    except Exception as e:
        print(f"[cloud_run] 休市日曆查詢失敗（照常執行）: {e}")
    return True


def _at_today(hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime.combine(now_tw().date(), datetime.min.time(), tzinfo=TW_TZ).replace(hour=h, minute=m)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=float, default=0, help="測試模式：馬上開始，跑幾分鐘就結束")
    args = parser.parse_args()

    load_config(force_reload=True)
    bot = TelegramBot.from_config()
    if bot is None:
        print("[cloud_run] 警告：沒有設定 TELEGRAM_BOT_TOKEN，系統會照跑但不會推播")

    if args.minutes > 0:
        stop_at = now_tw() + timedelta(minutes=args.minutes)
    else:
        if get("cloud.skip_holidays", True) and not is_trading_day(now_tw().date()):
            return 0
        start_at = _at_today(get("cloud.start_time", "08:45"))
        stop_at = _at_today(get("cloud.stop_time", "13:35"))
        if now_tw() >= stop_at:
            print(f"[cloud_run] 已經過了結束時間 {stop_at:%H:%M}，今天不執行"
                  "（要在收盤後測試請用 --minutes）")
            return 0
        if now_tw() < start_at:
            wait = (start_at - now_tw()).total_seconds()
            print(f"[cloud_run] 等到 {start_at:%H:%M} 再開始（{wait / 60:.0f} 分鐘）")
            time.sleep(wait)

    try:
        orch = Orchestrator()
        orch.start()
    except Exception as e:
        print(f"[cloud_run] 資料處理啟動失敗: {e}")
        if bot:
            bot.send(f"❌ 盤中監控啟動失敗：{e}\n"
                     "富果 API Key 失效的話，請更新 GitHub Secrets 的 FUGLE_API_TOKEN")
        return 1

    if bot:
        bot.stop_at = stop_at
        bot.start()
        bot.send(f"🟢 盤中監控已啟動（{now_tw():%m/%d %H:%M}），{stop_at:%H:%M} 自動結束\n"
                 "輸入 /help 看指令")
    print(f"[cloud_run] 執行中，{stop_at:%H:%M} 自動結束")

    while now_tw() < stop_at:
        time.sleep(5)

    if bot:
        try:
            summary = bot.top_text()
        except Exception as e:
            summary = f"（排名產生失敗：{e}）"
        bot.send(f"🔴 盤中監控結束，今天推播 {bot.alert_count} 則提醒\n\n{summary}")
        bot.stop()
    try:
        orch.ds.disconnect()
    except Exception as e:
        print(f"[cloud_run] 斷線時發生錯誤（不影響結束）: {e}")
    print("[cloud_run] 結束")
    return 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    # 富果 WebSocket 等背景執行緒不一定是 daemon，直接結束整個程式，避免 workflow 卡到逾時
    os._exit(code)
