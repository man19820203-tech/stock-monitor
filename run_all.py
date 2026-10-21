"""
一鍵啟動：資料處理迴圈（Orchestrator）+ 網頁儀表板一起跑，並自動開啟瀏覽器。

    python run_all.py
    # 或
    py run_all.py

會自動開啟瀏覽器 http://localhost:8000，不用手動輸入網址。
（這個自動開瀏覽器的邏輯直接寫在這裡，不管你是直接跑這個檔案、
 還是透過 start.ps1／桌面捷徑啟動，都一定會生效。）
"""
import os
import threading
import time
import webbrowser

import uvicorn

from main import Orchestrator

DASHBOARD_URL = "http://localhost:8000"


def start_orchestrator():
    """資料處理跑在背景執行緒。啟動失敗（例如 API Key 錯誤、連不上富果）時，
    如果只讓這個執行緒死掉，網頁仍會照常開著但永遠沒有資料，看起來像「還在等掃描」。
    所以這裡直接結束整個程式，並把原因印在視窗上。"""
    try:
        Orchestrator().run()
    except Exception as e:
        msg = str(e)
        print("\n" + "=" * 60)
        print("[錯誤] 資料處理啟動失敗，系統已停止。")
        print(f"原因：{msg}")
        if "authentication" in msg.lower() or "unauthorized" in msg.lower():
            print("→ 富果 API Key 無效或已過期，請到 https://developer.fugle.tw/ 確認，"
                  "再更新 config/secrets.yaml 的 data_source.fugle.api_token")
        print("=" * 60)
        os._exit(1)


def open_browser_when_ready(url: str = DASHBOARD_URL, delay_sec: float = 2.5):
    """網頁伺服器（uvicorn）需要幾秒才會真正把 port 開起來，
    所以這裡先等一下再開瀏覽器，避免開出「連不上網頁」的畫面。"""
    time.sleep(delay_sec)
    try:
        webbrowser.open(url)
        print(f"[run_all] 已自動開啟瀏覽器：{url}")
    except Exception as e:
        # 自動開瀏覽器失敗不該讓整個系統掛掉（例如某些精簡版Windows/伺服器環境沒有預設瀏覽器）
        # ——印出訊息，使用者自己手動打開網址就好，資料處理跟網頁伺服器仍會正常運作。
        print(f"[run_all] 自動開啟瀏覽器失敗（不影響系統運作，請自行打開 {url}）：{e}")


if __name__ == "__main__":
    t = threading.Thread(target=start_orchestrator, daemon=True)
    t.start()

    threading.Thread(target=open_browser_when_ready, daemon=True).start()

    # 有設定 Telegram Bot Token（config/secrets.yaml）就一起啟動機器人，手機也能收提醒、下指令
    from telegram_bot import TelegramBot
    bot = TelegramBot.from_config()
    if bot:
        bot.start()
        print("[run_all] Telegram 機器人已啟動")

    from web.app import app
    uvicorn.run(app, host="0.0.0.0", port=8000)
