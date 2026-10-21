@echo off
chcp 65001 >nul
title 台股當沖即時監控系統 - 安裝並啟動
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
