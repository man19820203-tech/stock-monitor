# 不用自己的電腦：GitHub Actions 盤中自動執行 + Telegram 推播

## 原理

```
每個交易日 08:23  GitHub 自動開一台雲端機器，下載這個專案
         08:45  開始連富果 API：全市場掃描、第一高低、評分、判斷劇本
         盤中   符合提醒條件 → Telegram 推播到你手機；你也可以傳 /top、/stock 2330 查詢
         13:35  傳一則收盤摘要後自動結束（週末、國定假日不會執行）
```

- **費用**：repo 設成 **Public**（公開），GitHub Actions 不限分鐘數、**完全免費**。
- **金鑰安全**：富果 API Key、Telegram Token 存在 GitHub 的 **Secrets**，別人看得到程式碼，但看不到金鑰
  （執行紀錄裡也會自動遮成 `***`）。公開的只有程式碼和 `config/settings.yaml` 裡的參數。
- **限制**：雲端機器連不進去，所以沒有網頁儀表板，改用 Telegram 看資料。

---

## 一、建立 Telegram 機器人（約 3 分鐘）

1. 在 Telegram 搜尋 **@BotFather**（有藍色勾勾的官方帳號），傳 `/newbot`。
2. 依指示輸入機器人的顯示名稱（例如「我的當沖監控」），再輸入帳號名稱（必須以 `bot` 結尾，例如 `my_daytrade_2026_bot`）。
3. BotFather 會回一串 **Token**（長得像 `123456789:AAH...`），先複製起來。**不要給別人看**，拿到 Token 的人就能控制你的機器人。
4. 取得你自己的 **chat ID**：在 Telegram 搜尋 **@userinfobot**，傳任意訊息給它，它回覆的 `Id` 那串數字就是。
   （之後如果設錯，傳訊息給你自己的機器人，它也會回覆你的 chat ID。）
5. 先傳一則訊息（例如 `/start`）給**你自己的機器人**。Telegram 規定使用者要先開口，機器人才能傳訊息給你。
6. （選用）讓輸入框出現指令選單：對 @BotFather 傳 `/setcommands`，選你的機器人，貼上：
   ```
   top - 大盤＋多方／空方排名
   list - 目前追蹤中的全部股票
   stock - 個股分數、劇本、解讀（例：/stock 2330）
   add - 加入自選股（例：/add 2330）
   remove - 移除自選股（例：/remove 2330）
   watch - 自選股清單
   mute - 暫停今天的提醒推播
   unmute - 恢復提醒推播
   status - 系統狀態
   help - 指令說明
   ```

## 二、把專案放上 GitHub

1. 到 https://github.com/ 註冊帳號（已經有就登入）。
2. 右上角 **+** → **New repository**：
   - Repository name：例如 `tw-daytrade-monitor`
   - 選 **Public**
   - 下面的 README、.gitignore、license **都不要勾**（專案裡已經有了）
   - 按 **Create repository**
3. 電腦要先裝 Git：https://git-scm.com/download/win （一路下一步即可）。
4. 在專案資料夾開 PowerShell，執行（把網址換成你剛建立的 repo 網址）：
   ```powershell
   git status
   ```
   **先確認清單裡沒有 `config/secrets.yaml`**（它已列入 `.gitignore`，正常不會出現），再繼續：
   ```powershell
   git add .
   git commit -m "第一版"
   git remote add origin https://github.com/你的帳號/tw-daytrade-monitor.git
   git push -u origin main
   ```
   第一次 push 會跳出視窗要你登入 GitHub。

> 本機的富果 API Key 已經從 `config/settings.yaml` 移到 `config/secrets.yaml`，
> 這個檔案不會被上傳；本機跑 `run_all.py` 照常會讀到它。

## 三、設定 Secrets（金鑰）

到 GitHub 上你的 repo → **Settings** → 左側 **Secrets and variables** → **Actions** →
**New repository secret**，新增 3 個（名稱要一模一樣，全大寫）：

| Name | Secret（值） |
|---|---|
| `FUGLE_API_TOKEN` | 富果 API Key（跟 `config/secrets.yaml` 裡的一樣） |
| `TELEGRAM_BOT_TOKEN` | 第一步 BotFather 給的 Token |
| `TELEGRAM_CHAT_ID` | 第一步 @userinfobot 給的數字 |

## 四、測試一次（隨時都可以測，不用等開盤）

1. repo 上方 **Actions** 分頁。第一次進去如果看到提示，按 **I understand my workflows, go ahead and enable them**。
2. 左側點 **盤中監控** → 右邊 **Run workflow** → 「測試用：跑幾分鐘就結束」填 `10` → 綠色 **Run workflow**。
3. 大約 1 分鐘內，Telegram 會收到「🟢 盤中監控已啟動」。可以試試傳 `/status`、`/list`、`/top`。
   （收盤後測試沒有即時成交，但系統會先抓一次最新報價，`/list` 仍然看得到股票和分數。）
4. 10 分鐘後會收到「🔴 盤中監控結束」，代表整套流程都正常。

**沒收到訊息？** 點進那次執行 → **monitor** → 展開「執行盤中監控」看紀錄：
- `富果 API Key 無效` → 檢查 `FUGLE_API_TOKEN`
- `[Telegram] sendMessage 失敗: Unauthorized` → `TELEGRAM_BOT_TOKEN` 貼錯
- `chat not found` → `TELEGRAM_CHAT_ID` 錯了，或你還沒先傳訊息給機器人（第一步第 5 點）

測試成功後就不用再管它，之後每個交易日早上會自動執行。

---

## 日常使用

| 想做的事 | 怎麼做 |
|---|---|
| 收提醒 | 什麼都不用做。符合 `watch_alert` 條件（預設：強勢突破／V型反轉／跌深反彈、分數 ≥ 70）會自動推播，同一檔同一劇本一天只推一次 |
| 看排名 | 傳 `/top` |
| 查個股 | 傳 `/stock 2330`，或直接傳 `2330` |
| 加／刪自選股 | `/add 2330`、`/remove 2330`。收盤後會自動存回 GitHub，隔天繼續追蹤 |
| 今天不想被吵 | `/mute`（隔天自動恢復） |
| 改參數 | 在 GitHub 網頁上打開 `config/settings.yaml` → 右上角鉛筆圖示編輯 → **Commit changes**。下一次執行才生效（正在跑的那次已經下載好舊版設定） |
| 臨時重跑（例如當天執行失敗） | Actions → 盤中監控 → Run workflow，「跑幾分鐘」**留空** = 正常盤中模式，跑到 13:35 |

## 注意事項

- **本機和雲端不要同時開**：兩邊用同一個 Bot Token 收訊息會互相搶（紀錄裡會看到 `Conflict`／409）。
  想在本機也用 Telegram，可以再跟 BotFather 建一個機器人，填在本機的 `config/secrets.yaml`。
- **本機改程式前先同步**：雲端會把 `/add`、`/remove` 的自選股 commit 回 GitHub，本機要改東西前先執行 `git pull`。
- **GitHub 排程會延遲**：通常晚幾分鐘，尖峰時可能晚到半小時，所以設定在 08:23 觸發，程式會自己等到 08:45 才開始。
- **長期不動的 repo**：GitHub 會停用 60 天沒有任何 commit 的公開 repo 的排程。workflow 每次執行最後一步會重新啟用一次，
  通常不會發生；萬一被停用，GitHub 會寄信通知，到 Actions 頁面按 **Enable workflow** 即可。
- **執行失敗會寄信**：GitHub 預設會寄 Email 通知 workflow 失敗。富果 Key 過期的話，Telegram 也會收到「❌ 啟動失敗」。
- **富果免費方案限制一樣適用**：WebSocket 即時追蹤 5 檔、REST 每分鐘 60 次，跟本機執行時相同。
- 檔案位置：排程設定在 `.github/workflows/monitor.yml`，雲端執行入口是 `cloud_run.py`，機器人是 `telegram_bot.py`，
  開始／結束時間在 `config/settings.yaml` 的 `cloud` 區塊。
