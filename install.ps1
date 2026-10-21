# 台股當沖即時監控系統 - 懶人安裝＋啟動腳本
# 由「安裝並啟動.bat」呼叫。第一次執行會自動：
#   1. 找 Python 3.10+，找不到就自動安裝（winget，失敗則改從 python.org 下載）
#   2. 建立專案專用的虛擬環境 .venv（不污染電腦其他 Python）
#   3. 安裝 requirements.txt 內所有套件（requirements.txt 沒變就略過）
#   4. 在桌面建立捷徑
#   5. 手機遠端：安裝 Tailscale、開防火牆（只放行 Tailscale 網段）、引導登入
#   6. 啟動系統並開啟瀏覽器
# 之後再執行只會做檢查，幾秒內就啟動。

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Set-Location -Path $PSScriptRoot

function Step($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }
function Fail($msg) {
    Write-Host ""
    Write-Host "[錯誤] $msg" -ForegroundColor Red
    Read-Host "按 Enter 結束"
    exit 1
}

# 已經在執行中：只開儀表板，避免 8000 port 被重複占用
try {
    $null = Invoke-WebRequest "http://localhost:8000/" -UseBasicParsing -TimeoutSec 2
    Write-Host "[OK] 系統已經在執行中，直接開啟儀表板"
    Start-Process "http://localhost:8000"
    Start-Sleep 2
    exit 0
} catch {}

# ---------- 1. Python ----------
function Find-Python {
    # 回傳 Python 3.10+ 的 exe 完整路徑，找不到回傳 $null。
    # 跳過 WindowsApps 的假 python.exe（只會跳出 Microsoft Store）。
    $candidates = @()
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        try { $candidates += (& py -3 -c "import sys;print(sys.executable)" 2>$null) } catch {}
    }
    $pyn = Get-Command python -ErrorAction SilentlyContinue
    if ($pyn -and $pyn.Source -notlike '*WindowsApps*') { $candidates += $pyn.Source }
    foreach ($dir in Get-ChildItem "$env:LOCALAPPDATA\Programs\Python" -Directory -ErrorAction SilentlyContinue) {
        $candidates += (Join-Path $dir.FullName 'python.exe')
    }
    foreach ($c in $candidates) {
        if (-not $c -or -not (Test-Path $c)) { continue }
        try {
            $ok = & $c -c "import sys;print(1 if sys.version_info>=(3,10) else 0)" 2>$null
            if ($ok -eq '1') { return $c }
        } catch {}
    }
    return $null
}

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path','User')
}

Step "[1/6] 檢查 Python（需要 3.10 以上）"
$pythonExe = Find-Python
if (-not $pythonExe) {
    Write-Host "找不到 Python，開始自動安裝（約 1~3 分鐘，請稍候）..."
    $installed = $false
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        try {
            winget install -e --id Python.Python.3.12 --scope user --silent `
                --accept-package-agreements --accept-source-agreements
            $installed = ($LASTEXITCODE -eq 0)
        } catch {}
    }
    if (-not $installed) {
        Write-Host "winget 不可用或失敗，改從 python.org 下載安裝程式..."
        $installer = Join-Path $env:TEMP 'python-3.12.8-amd64.exe'
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest 'https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe' `
                -OutFile $installer -UseBasicParsing
            Start-Process $installer -Wait `
                -ArgumentList '/quiet','InstallAllUsers=0','PrependPath=1','Include_launcher=1','Include_test=0'
        } catch {
            Fail "Python 自動安裝失敗（請檢查網路）。可手動安裝：https://www.python.org/downloads/ （安裝時勾選 Add python.exe to PATH），再重新執行本程式。"
        }
    }
    Refresh-Path
    $pythonExe = Find-Python
    if (-not $pythonExe) { Fail "Python 安裝後仍找不到。請關閉此視窗後再雙擊一次「安裝並啟動.bat」。" }
}
Write-Host "使用的 Python：$pythonExe"

# ---------- 2. 虛擬環境 ----------
Step "[2/6] 建立專案專用環境 (.venv)"
$venvPy = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$needNew = $true
if (Test-Path $venvPy) {
    # 複製到別台電腦的 .venv 會指向舊路徑而壞掉，這種情況要重建
    try { & $venvPy -c "import sys" 2>$null; if ($LASTEXITCODE -eq 0) { $needNew = $false } } catch {}
}
if ($needNew) {
    if (Test-Path '.venv') { Remove-Item '.venv' -Recurse -Force }
    & $pythonExe -m venv .venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPy)) { Fail "建立虛擬環境失敗" }
}

# ---------- 3. 套件 ----------
Step "[3/6] 安裝相依套件"
$reqHash = (Get-FileHash 'requirements.txt' -Algorithm SHA256).Hash
$marker = Join-Path $PSScriptRoot '.venv\requirements.hash'
$installedHash = if (Test-Path $marker) { (Get-Content $marker -Raw).Trim() } else { '' }
if ($installedHash -ne $reqHash) {
    & $venvPy -m pip install --disable-pip-version-check -q --upgrade pip
    & $venvPy -m pip install --disable-pip-version-check -r requirements.txt
    if ($LASTEXITCODE -ne 0) { Fail "套件安裝失敗，請檢查網路連線後重新執行" }
    Set-Content $marker $reqHash
} else {
    Write-Host "套件已是最新，略過"
}

# ---------- 4. 桌面捷徑 ----------
Step "[4/6] 桌面捷徑"
try {
    $desktop = [Environment]::GetFolderPath('Desktop')
    $lnk = Join-Path $desktop '台股當沖監控.lnk'
    $bat = Join-Path $PSScriptRoot '安裝並啟動.bat'
    if (-not (Test-Path $lnk)) {
        $sh = New-Object -ComObject WScript.Shell
        $s = $sh.CreateShortcut($lnk)
        $s.TargetPath = $bat
        $s.WorkingDirectory = $PSScriptRoot
        $s.Save()
        Write-Host "已建立桌面捷徑：台股當沖監控"
    } else {
        Write-Host "桌面捷徑已存在，略過"
    }
} catch {
    Write-Host "（建立桌面捷徑失敗，不影響使用）"
}

# 模擬資料（provider=mock 時才會用到，已存在就略過）
if (-not (Test-Path 'data\history\2408.csv')) {
    & $venvPy scripts\gen_mock_history.py
}

# ---------- 5. 手機遠端（Tailscale） ----------
Step "[5/6] 手機遠端連線（Tailscale）"
function Find-Tailscale {
    $t = Get-Command tailscale -ErrorAction SilentlyContinue
    if ($t) { return $t.Source }
    foreach ($p in "$env:ProgramFiles\Tailscale\tailscale.exe", "${env:ProgramFiles(x86)}\Tailscale\tailscale.exe") {
        if (Test-Path $p) { return $p }
    }
    return $null
}
$tsExe = $null
try {
    $tsExe = Find-Tailscale
    if (-not $tsExe) {
        Write-Host "尚未安裝 Tailscale，開始安裝（過程中可能跳出「是否允許變更」，請按「是」）..."
        $ok = $false
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            winget install -e --id Tailscale.Tailscale --silent --accept-package-agreements --accept-source-agreements
            $ok = ($LASTEXITCODE -eq 0)
        }
        if (-not $ok) {
            $msi = Join-Path $env:TEMP 'tailscale-setup.msi'
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest 'https://pkgs.tailscale.com/stable/tailscale-setup-latest-amd64.msi' -OutFile $msi -UseBasicParsing
            Start-Process msiexec.exe -Verb RunAs -Wait -ArgumentList '/i',"`"$msi`"",'/quiet','/norestart'
        }
        Refresh-Path
        $tsExe = Find-Tailscale
    }
} catch {
    Write-Host "（Tailscale 安裝失敗：$($_.Exception.Message)。本機使用不受影響，手機遠端可稍後手動安裝，見 docs\手機遠端連線_Tailscale.md）"
}

# 防火牆：只放行 Tailscale 網段 (100.64.0.0/10) 連 8000，需要系統管理員權限（只做一次）
try {
    if (-not (Get-NetFirewallRule -DisplayName '台股當沖監控 8000' -ErrorAction SilentlyContinue)) {
        Write-Host "設定防火牆（請在跳出的視窗按「是」）..."
        $fw = 'New-NetFirewallRule -DisplayName "台股當沖監控 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -RemoteAddress 100.64.0.0/10 -Action Allow | Out-Null'
        Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList '-NoProfile','-Command',$fw
    } else {
        Write-Host "防火牆規則已存在，略過"
    }
} catch {
    Write-Host "（防火牆規則未建立，手機可能連不上；本機使用不受影響）"
}

$phoneUrl = $null
if ($tsExe) {
    $ip = $null
    try { $ip = (& $tsExe ip -4 2>$null | Select-Object -First 1) } catch {}
    if ($ip -and $ip -match '^100\.') {
        $phoneUrl = "http://${ip}:8000"
    } else {
        Write-Host ""
        Write-Host "Tailscale 還沒登入。請在瀏覽器完成登入（建議用 Google 帳號，手機也要用同一個帳號）。" -ForegroundColor Yellow
        try { Start-Process $tsExe -ArgumentList 'login' -WindowStyle Hidden } catch {}
    }
}

# ---------- 6. 啟動 ----------
Step "[6/6] 啟動系統，瀏覽器會自動開啟 http://localhost:8000"
if ($phoneUrl) {
    Write-Host ""
    Write-Host "手機（需先安裝 Tailscale App 並登入同一帳號）請開：$phoneUrl" -ForegroundColor Green
} else {
    Write-Host "手機遠端：Tailscale 登入完成後，重新雙擊「安裝並啟動.bat」，這裡就會顯示手機要開的網址。"
}
Write-Host "若要停止系統，直接關閉這個視窗即可。"
Write-Host ""
$env:PYTHONIOENCODING = 'utf-8'
& $venvPy run_all.py

Write-Host ""
Write-Host "系統已停止。"
Read-Host "按 Enter 關閉視窗"
