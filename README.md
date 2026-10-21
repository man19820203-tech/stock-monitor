# 台股當沖即時監控＋候選排序＋交易劇本辨識系統

## 這是什麼／不是什麼

這是一套**可以在你自己的電腦或伺服器上長時間執行**的當沖監控系統骨架，
核心引擎（第一高低點偵測、技術指標、當沖分數、劇本辨識、突破品質、相對強弱）
已經是完整、可執行、有煙霧測試驗證過的邏輯，架構嚴格遵守：

```
行情API → 程式計算 → 規則引擎 → AI解讀
```

**資料來源現況**：富果（Fugle）API 已經是**真的串接完成**，不是骨架
（`data_source/fugle_source.py`，依官方文件的 WebSocket aggregates 頻道 + REST 大盤查詢實作），
申請到 API Key 填進 config 就能用。元大 SPARK 因為需要元大證券開戶+審核，
還沒有帳號可以測試，`data_source/spark_source.py` 保留介面骨架與 TODO，
之後你申請到 SPARK 權限我再幫你補上真實串接。

**重要限制説明**：這套系統設計為長駐執行（盤中持續運算），必須跑在你自己
隨時開機、能連上券商 API 的電腦或伺服器上 —— 不能跑在對話用的雲端環境裡，
因為那個環境不會在盤中持續連線監控。另外，你問到「能不能用網路即時搜尋
的方式更新報價」：一般網頁搜尋工具沒有逐秒的即時行情推送能力，也不是
合法穩定的資料來源，不建議拿來做當沖監控，這也是為什麼系統設計成
「券商 API adapter」架構、且已經內建富果 API 當作 SPARK 審核期間的備援方案。

## 目前狀態：mock 模式（模擬資料）

`config/settings.yaml` 的 `data_source.provider` 目前設為 `mock`，
會用隨機漫步+偶發量能脈衝模擬盤中報價，讓你可以：
1. 立刻看到完整系統運作（三欄式儀表板、分數、劇本判斷、AI解讀文字）
2. 驗證所有規則、參數、劇本分類邏輯是否符合你的交易邏輯
3. 在等待 SPARK/富果 API 審核期間就能持續調整參數

## 如何接上真實資料（富果 Fugle API，推薦先走這條）

不需要富果證券開戶，只要註冊富果會員即可申請 API Key：

1. 到 https://developer.fugle.tw/ 註冊會員，再到
   https://developer.fugle.tw/docs/key/ 申請 API Key
   （免費方案：WebSocket 即時報價 5 檔訂閱、1 連線，足夠先跑預設的5檔 watchlist；
   免費方案有每分鐘60次的REST呼叫限制，主要影響大盤輪詢頻率，個股是WebSocket推播不受此限）
2. 安裝套件：
   ```bash
   pip install fugle-marketdata
   ```
3. 把 `config/secrets.example.yaml` 複製成 `config/secrets.yaml`，填入 API Key
   （金鑰不寫在 `settings.yaml`，避免上傳 GitHub 時外流；`secrets.yaml` 已列入 `.gitignore`）：
   ```yaml
   data_source:
     fugle:
       api_token: "你的API Key"
   ```
   並確認 `config/settings.yaml` 的 `data_source.provider` 是 `fugle`。
4. 重新執行 `python run_all.py`（或雙擊啟動腳本），
   儀表板上就會是真實盤中報價，不再是模擬資料。

想接元大 SPARK（需要元大證券開戶+API使用資格審核）的話，
`data_source/spark_source.py` 已經寫好介面骨架與 TODO 註解，
等你申請到金鑰後跟我說，我幫你把實際串接補完，下游所有模組完全不用改。

下游所有模組（第一高低、指標、分數、劇本、網頁）完全不用因為換資料來源而改。

## 沒有一直開著的電腦：放到 GitHub 雲端執行 + Telegram 推播

每個交易日由 GitHub Actions 自動在雲端執行（public repo 免費），符合提醒條件就推播到 Telegram，
也能在 Telegram 用 `/top`、`/stock 2330`、`/add 2330` 查詢與管理自選股，收盤後自動結束。
設定步驟見 [docs/雲端部署_GitHub_Actions_Telegram.md](docs/雲端部署_GitHub_Actions_Telegram.md)。

本機執行 `run_all.py` 時，只要在 `config/secrets.yaml` 填了 Telegram Token，機器人也會一起啟動。

## Windows 雙擊啟動（最簡單，推薦用 PowerShell 版）

前提：電腦要先裝好 Python 3.10+（安裝時勾選 "Add python.exe to PATH"，
下載：https://www.python.org/downloads/）。裝好之後：

1. 建立一個桌面捷徑：桌面按右鍵 → 新增 → 捷徑，「項目位置」貼上（把路徑換成你實際的路徑）：
   ```
   powershell.exe -ExecutionPolicy Bypass -File "完整路徑\tw_daytrade_monitor\start.ps1"
   ```
   命名為「台股當沖監控系統」，完成。
2. 之後每次要用，直接雙擊桌面上那個捷徑即可 —— 它會自動檢查/安裝套件、
   啟動系統，並在幾秒後自動打開瀏覽器顯示儀表板。
3. 要停止系統，把跳出來的視窗關掉就可以了。

> 實測發現部分 Windows 環境（防毒軟體、瀏覽器下載安全掃描、公司/家用電腦的安全性原則）
> 會攔截或拒絕執行 `.bat` 檔案，出現「此應用程式無法在您的電腦上執行」或下載後
> `.bat` 檔案直接消失等狀況。改用 PowerShell（`start.ps1`）搭配捷徑上的
> `-ExecutionPolicy Bypass` 實測比較穩定，不受這些限制影響，所以這是目前推薦的方式。
> 舊的 `一鍵啟動.bat`／`建立桌面捷徑.bat` 還留在專案裡給沒遇到攔截問題的人用，
> 兩者功能一樣，選一個能在你電腦上正常執行的就好。

> 這些都是「一鍵啟動腳本」，不是傳統的 `.exe` 安裝程式 —— 因為要包裝成
> 免安裝 Python 的 `.exe`（例如用 PyInstaller），必須在 Windows 環境上實際打包，
> 我這邊的開發環境是 Linux 雲端容器，無法產生 Windows 執行檔。
> 如果你之後想要真正的 `.exe`，可以在你的 Windows 電腦上安裝好 Python 後執行：
> `pip install pyinstaller` 然後 `pyinstaller --onefile run_all.py`，
> 或跟我說，我可以把打包設定檔（`.spec`）先寫好給你，你只要在自己電腦上跑打包指令即可。

想手動用命令列執行、或想在 Mac/Linux 上跑，請看下面「安裝與執行」。

## 用手機看儀表板

儀表板已支援手機版面（寬度 768px 以下時，三欄會改成上下排列，警報欄放在最上面）。
- **同一個 Wi-Fi**：手機瀏覽器開 `http://電腦的區網IP:8000` 就能看。
- **用行動網路（4G/5G）在外面看**：請照 [docs/手機遠端連線_Tailscale.md](docs/手機遠端連線_Tailscale.md) 設定，
  不要用路由器把 8000 port 開到網際網路上（儀表板目前沒有登入密碼）。

## 安裝與執行（命令列，跨平台）

```bash
pip install -r requirements.txt

# (可選) 產生 mock 模式用的「昨日同時段量」模擬資料
python scripts/gen_mock_history.py

# 一鍵啟動：資料處理 + 網頁儀表板
python run_all.py
```

打開瀏覽器 http://localhost:8000 即可看到三欄式盤中儀表板
（多方TOP10｜空方TOP10｜警報），每 3 秒自動更新。

### 驗證核心邏輯（不需要等真的到09:00）

```bash
python scripts/smoke_test.py
```

會用合成的 tick 序列，直接驗證「第一高低點偵測 → 指標 → 分數 → 劇本 → AI解讀」
整條管線，印出類似：

```
南亞科（2408）目前判定為「強勢突破」劇本。09:00起第一觀察區間形成第一高 499、
第一低 495。現價突破第一高，成交量為近5分鐘均量的1.0倍，已站穩。
突破品質評估：有效突破機率 70%。相對大盤 +5.36%，走勢偏強。
目前劇本符合訊號 3/5 項。多方分數 94.0 / 空方分數 40.0（僅供排序參考，非買賣建議）。
```

## 專案結構

```
config/settings.yaml     所有可調參數（成交額門檻、量能倍數、第一波分鐘數、
                          突破幅度%、MA參數、相對大盤門檻、排除規則...全部在這）
config_loader.py          統一設定讀取，支援熱重載
market_time.py            統一用 Asia/Taipei 時間（避免伺服器時區是UTC造成誤判）
models.py                  共用資料結構 (Tick / Bar / FirstRangeState / StockDailyState)
data_source/
  base.py                  資料來源抽象介面
  mock_source.py           模擬資料來源（目前使用中）
  spark_source.py          元大SPARK adapter骨架（待你補API串接）
  fugle_source.py          富果 adapter，已依官方文件實際串接完成（非骨架）
  factory.py                依 config 建立對應的資料來源
engine/
  first_range.py            ★第一波高低點偵測（最重要模組）
  indicators.py             1分K/5分K、MA5/20/60、量能倍數、內外盤比、距漲跌停
  market_context.py         大盤/族群相對強弱計算
scoring/
  score_engine.py            當沖分數：流動性20+趨勢20+量能20+相對強弱20+突破品質20
scenario/
  breakout_quality.py         突破品質評分（有量/站穩/大盤同步/族群同步/壓力關卡）
  scenario_engine.py           ★10種劇本規則分類（最重要模組）
ai_narrator/
  narrator.py                  規則式模板生成中文解讀（可選接Claude API潤飾文字）
web/
  app.py, templates/dashboard.html   三欄式即時儀表板
review/
  daily_review.py               盤後自動檢討，存成JSON供未來回測
main.py / run_all.py            主流程整合
scripts/
  gen_mock_history.py            產生 mock 模式用的模擬歷史量資料
  smoke_test.py                   離線管線驗證測試
```

## 所有規則都可調（不用改程式碼）

打開 `config/settings.yaml`，重點區塊：

- `first_range`：第一觀察區間分鐘數（5/10/15）、突破幅度門檻%、假突破偵測秒數
- `liquidity` / `volume`：成交額與量能倍數的門檻與對應得分（breakpoints）
- `relative_strength`：相對大盤強弱的門檻
- `breakout_quality`：突破品質判斷的量能倍數、壓力關卡距離%
- `chase_risk`：乖離率過大扣分的門檻
- `score_weights`：五大構面權重（目前均為20，可依回測結果調整）
- `universe`：追蹤清單、排除ETF/處置股、價格區間
- `groups`：族群成分股（半導體/記憶體/PCB/載板/被動元件/散熱/AI伺服器/金融/航運）

改完存檔即可生效（`config_loader.py` 支援熱重載，網頁API每次請求都會重讀設定）。

## 10種劇本分類邏輯（scenario/scenario_engine.py）

強勢突破、假突破、反彈空、開低築底、跌深反彈、破低續殺、開高走低、
V型反轉、箱型震盪、無方向／禁止交易 —— 全部是明確的 if/else 規則，
可稽核、可回測、可調整，AI 不參與分類判斷，只負責把分類結果翻成中文敘述。

## 全市場自動掃描（免費方案版）

回答「可以透過API看所有台股資料並分析嗎？」——**可以，但免費方案下不是逐秒即時的**，
以下是實際限制與這套系統的做法：

**限制是什麼：**
- 富果沒有「一次拿全市場報價」的批次端點，只能一檔一檔查（`intraday/quote/{symbol}`）
- 免費方案 REST 限 60次/分鐘，全台上市股約1000多檔，掃一輪最快也要 20-30 分鐘
- WebSocket 即時訂閱（五檔/內外盤/逐秒K棒）免費方案只有 5 檔額度，不可能全市場都即時

**這套系統怎麼做（兩層架構）：**
1. **第一層：全市場REST掃描**（`data_source/market_scanner.py`）—— 背景執行緒，
   每隔 `universe.scan.scan_interval_minutes`（預設20分鐘）把全上市股票掃一輪
   （已套用排除ETF、價格區間、成交額初篩），依「成交額 x 波動幅度」概略排序，
   取前 `universe.scan.top_n_candidates`（預設5）名當候選。
   → 這一層是「雷達」，抓的是「這幾分鐘誰在動」，不是逐秒報價。
2. **第二層：WebSocket即時追蹤**（既有的 `FugleDataSource` + `main.py` 全部評分/劇本引擎）——
   把第一層篩出的候選動態訂閱進來，只有這幾檔才有真正逐秒即時的五檔、內外盤、
   K棒、第一波高低、劇本判斷、當沖分數。候選輪動時（新的漲上來、舊的退出候選）
   會自動取消訂閱舊的、訂閱新的，並把過期的資料從網頁儀表板移除。

**如何啟用：** `config/settings.yaml` 裡 `data_source.provider: fugle` 且
`universe.auto_scan: true` 且 `universe.scan.enabled: true`（目前預設都是開的），
系統啟動時若偵測到條件符合會自動跑，終端機會印出
「全市場自動掃描已啟動」的訊息。`universe.watchlist` 預設是空的——代表**完全交給
掃描結果決定要追蹤誰**，每次啟動、每一輪掃描都會重新選出當下最新的前N名候選。
如果你想要「這幾檔一定要看，掃描結果是額外加開的」，把代碼填回 `watchlist` 即可，
這幾檔不會受掃描結果影響、永遠保留。

**每次啟動都要重新乾等20-30分鐘才有候選嗎？** 不用。系統會把每次掃描完成的結果
存成「當日快取」（`data/scan_cache.json`），只要是同一個交易日內重新啟動程式，
會先套用上次掃描的候選名單立刻開始追蹤，背景同時會馬上重新掃一輪去更新成最新結果
（掃完後候選名單才會真正變成當下最新的）。隔天（跨交易日）快取會自動失效不使用，
因為隔天的成交量/漲跌都是全新的，用昨天的候選沒有意義，那時就會老實乾等一輪新掃描。

**升級付費方案後怎麼調：** 把 `universe.scan.rest_calls_per_minute`
（Developer方案600次/分）、`universe.scan.top_n_candidates` 和
`universe.scan.max_websocket_subscriptions`（Developer方案300檔訂閱）調大，
掃描間隔 `scan_interval_minutes` 也可以調短（額度夠的話甚至可以做到全市場幾分鐘一輪），
完全不用改程式邏輯。

## 下一步建議（依你選的「先做核心引擎」之後的路線圖）

1. **驗證邏輯**：用 mock 模式跑一整天（或用 smoke_test.py 測試更多情境），
   確認10種劇本的判斷條件符合你的實際交易邏輯，這是最關鍵的一步。
2. **接真即時資料**：申請富果 API Key（見上方步驟，已經可以直接用），
   之後若要換成/加上元大 SPARK，跟我說我再補上 spark_source.py 的實際串接。
3. **全市場自動掃描**：已經實作完成（見下方「全市場自動掃描」章節），
   目前用的是免費方案假設下的保守參數，之後升級付費方案後可以把
   `config/settings.yaml` 的 `universe.scan.*` 調大即可，邏輯不用改。
4. **盤後回測**：`review/daily_review.py` 目前是簡化版成功率統計，
   累積真實資料後，需要把「候選成立當下價位 vs 之後表現」做成完整的
   逐筆回測，才能真正用來調整 `score_weights` 等參數權重。
5. **部署**：建議用 `systemd` 或 `pm2`/`supervisor` 讓 `run_all.py` 開盤前自動啟動、
   收盤後自動跑 `review/daily_review.py`。

## 重要聲明

本系統所有分數、劇本分類、突破品質評估，都是規則式統計方法，
**不構成任何投資建議，不保證獲利，AI解讀文字也刻意避免使用「一定」「保證」等字眼**。
所有交易決策與風險由使用者自行負責。
