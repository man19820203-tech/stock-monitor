@echo off
chcp 65001 >nul
setlocal
title 台股當沖即時監控系統
cd /d "%~dp0"

rem 系統已經在另一個視窗執行中：直接開啟儀表板，不重複啟動（避免 8000 port 被占用）
curl -s -o nul --max-time 2 http://localhost:8000/ 2>nul
if not errorlevel 1 (
  echo [OK] 系統已經在執行中，直接開啟儀表板：http://localhost:8000
  start "" "http://localhost:8000"
  timeout /t 3 >nul 2>nul
  exit /b 0
)

rem 實際的啟動流程（檢查 Python、安裝套件、產生模擬資料、啟動系統、開瀏覽器）都在 start.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
