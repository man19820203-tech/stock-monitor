# 用手機（行動網路）看儀表板：Tailscale 設定步驟

## 原理

監控系統繼續跑在家裡的電腦上，手機只是用瀏覽器看儀表板。
Tailscale 會在你的電腦和手機之間建一條加密的私人網路，
手機不管是用 Wi-Fi 還是 4G/5G，都能連回家裡的電腦，
**不需要設定路由器、不需要對外開 port，外人連不進來**。
個人使用免費。

```
手機（4G/5G） ──加密通道── Tailscale ──加密通道── 家裡電腦（run_all.py，port 8000）
```

> 不要用路由器「連接埠轉送」把 8000 直接開到網際網路上：
> 儀表板目前沒有登入密碼，任何知道你 IP 的人都能看，還能新增/移除你的自選股。

---

## 一、電腦端（Windows，只要做一次）

1. 到 https://tailscale.com/download/windows 下載安裝。
2. 安裝完成後，右下角工作列會出現 Tailscale 圖示，點它 → **Log in**，
   用 Google／Microsoft／Apple 帳號登入（**記住你用哪個帳號，手機要用同一個**）。
3. 登入後，點工作列圖示，可以看到這台電腦的名稱和 Tailscale IP（`100.x.x.x` 開頭），
   也可以在 PowerShell 輸入下面指令查：
   ```powershell
   tailscale ip -4
   ```
4. **防止電腦睡眠**：設定 → 系統 → 電源 → 「螢幕和睡眠」，
   接上電源時的「睡眠」設成「永不」。電腦睡著，手機就看不到資料。
5. **Windows 防火牆**：第一次執行 `run_all.py` 時如果跳出「Windows 安全性警訊」，
   勾選「私人網路」後按「允許存取」。
   如果之前按過拒絕，手機會連不上，請用**系統管理員身分**開 PowerShell 補一條規則：
   ```powershell
   New-NetFirewallRule -DisplayName "台股當沖監控 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -RemoteAddress 100.64.0.0/10 -Action Allow
   ```
   （`100.64.0.0/10` 是 Tailscale 的網段，所以這條規則只放行從 Tailscale 連進來的裝置）

## 二、手機端

1. App Store／Google Play 搜尋 **Tailscale** 安裝。
2. 打開 App，用**跟電腦同一個帳號**登入，系統要求加入 VPN 設定時選「允許」。
3. 確認 App 裡的開關是「Connected」，清單中看得到你的電腦。

## 三、連線（HTTP，最簡單）

1. 電腦照平常方式啟動系統（雙擊捷徑或 `python run_all.py`）。
2. 手機**先關掉 Wi-Fi 改用行動網路**來測試，在瀏覽器輸入：
   ```
   http://電腦名稱:8000
   ```
   例如電腦名稱是 `x412-hj`，就輸入 `http://x412-hj:8000`。
   如果輸入名稱打不開，就改用 IP：`http://100.x.x.x:8000`。
3. 看到儀表板就成功了。建議加入書籤，或用瀏覽器選單的「加入主畫面」。

## 四、（選用）改用 HTTPS

用 HTTP 已經可以看盤。改用 HTTPS 的好處是瀏覽器把它當成安全網站，
之後如果要做手機推播通知，HTTPS 是必要條件。

1. 用瀏覽器打開 https://login.tailscale.com/admin/dns
   - 確認 **MagicDNS** 已啟用
   - 在 **HTTPS Certificates** 按 **Enable HTTPS**
2. 電腦上開 PowerShell 執行（只要做一次，重開機後會自動恢復）：
   ```powershell
   tailscale serve --bg 8000
   ```
3. 指令會印出一個網址，格式像這樣：
   ```
   https://x412-hj.tailxxxx.ts.net/
   ```
   手機改用這個網址開儀表板（不用加 `:8000`）。
4. 要取消：`tailscale serve reset`

> 注意是 `tailscale serve`，**不是** `tailscale funnel`。
> funnel 會把儀表板公開到整個網際網路上，不要用。

---

## 手機上的提醒功能

| 功能 | 手機上 |
|---|---|
| 符合關注條件時跳出彈窗 | ✅ 可以，只要儀表板頁面開著 |
| 提示音 | ✅ 可以，每次打開頁面都要先按一次「🔔 啟用提醒」；手機切到靜音／震動模式時聽不到 |
| 系統通知（鎖定畫面通知） | ❌ 目前不支援。Android Chrome 和 iPhone Safari 要有 Service Worker 才能發通知，iPhone 還必須先「加入主畫面」，這兩個目前都還沒做 |

另外，手機鎖定螢幕或切到別的 App 時，瀏覽器會暫停頁面，**頁面不會更新，也不會響**。
盤中要靠手機盯盤的話，請讓儀表板保持在前景，並把螢幕自動鎖定時間調長。

## 常見問題

- **手機打不開，一直轉圈**
  1. 手機和電腦的 Tailscale 都是 Connected 嗎？
  2. 電腦上的系統有在跑嗎？在電腦開 http://localhost:8000 試試
  3. 防火牆：照「一、電腦端」第 5 步補規則
- **打得開但一直是「尚無資料」**：這是資料來源的問題，跟手機無關，請先在電腦上確認儀表板正常。
- **公司或學校網路打不開**：有些網路會擋 VPN，改用手機行動網路試試看。
