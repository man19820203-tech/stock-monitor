# 台股當沖即時監控系統 - PowerShell 啟動腳本
#
# 為什麼用 PowerShell 而不是 .bat：
#   實測發現部分 Windows 環境（防毒/安全性原則/瀏覽器安全掃描）會攔截或拒絕執行 .bat 檔，
#   出現「此應用程式無法在您的電腦上執行」或下載後.bat檔案直接消失等狀況。
#   PowerShell 腳本配合捷徑上的 -ExecutionPolicy Bypass，不受這些限制影響，較穩定。
#
# 使用方式：
#   1. 建立一個桌面捷徑，「項目位置」填：
#      powershell.exe -ExecutionPolicy Bypass -File "完整路徑\start.ps1"
#   2. 雙擊該捷徑即可：自動裝套件 -> 產生模擬資料(如果需要) -> 啟動系統 -> 5秒後自動開瀏覽器
#
# 停止系統：直接關閉跳出來的視窗，或按 Ctrl+C。

Set-Location -Path $PSScriptRoot

Write-Host "==============================================="
Write-Host "  台股當沖即時監控系統 - 啟動中"
Write-Host "==============================================="
Write-Host ""

$pyCmd = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    $pyCmd = "py"
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $pyCmd = "python"
}

if (-not $pyCmd) {
    Write-Host "[錯誤] 找不到 Python，請先安裝 Python 3.10 以上版本"
    Write-Host "下載網址：https://www.python.org/downloads/"
    Write-Host "安裝時務必勾選 'Add python.exe to PATH'"
    Read-Host "按 Enter 結束"
    exit 1
}

Write-Host "使用的Python指令：$pyCmd"
Write-Host ""

Write-Host "[1/3] 檢查/安裝相依套件（第一次執行會比較久，之後會很快）..."
& $pyCmd -m pip install -q -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "[錯誤] 套件安裝失敗，請檢查網路連線後重試"
    Read-Host "按 Enter 結束"
    exit 1
}

if (-not (Test-Path "data\history\2408.csv")) {
    Write-Host "[2/3] 產生模擬歷史資料..."
    & $pyCmd scripts\gen_mock_history.py
} else {
    Write-Host "[2/3] 模擬歷史資料已存在，略過"
}

Write-Host "[3/3] 啟動系統，瀏覽器將在幾秒後自動開啟..."
Write-Host ""
Write-Host "若要停止系統，直接關閉這個視窗即可。"
Write-Host "==============================================="
Write-Host ""

# 瀏覽器由 run_all.py 在網頁伺服器就緒後自動開啟，這裡不再重複開（否則會開出兩個分頁）

& $pyCmd run_all.py

Write-Host ""
Write-Host "系統已停止。"
Read-Host "按 Enter 關閉視窗"
