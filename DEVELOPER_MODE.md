# YS-pikmin 開發者模式工具 1.0.0

用於已信任電腦的新 iPhone，讓手機設定顯示「開發者模式」選項，並在手動開啟後讀取真實狀態。這是獨立工具，YS-pikmin 主程式不必同時執行。

## EXE 使用方式

1. 全部解壓縮，保留「開發者模式工具.exe」旁的「程式資料」資料夾。
2. Windows 安裝 Apple Devices，使用支援資料傳輸的 USB 線連接 iPhone。
3. 解鎖 iPhone，在 Apple Devices 與手機完成「信任這部電腦」配對。
4. 執行「開發者模式工具.exe」，選擇正確的 USB 手機。
5. 若已顯示「已開啟」，不用再處理。否則按「顯示開發者模式選項」。
6. 手機「設定 → 隱私權與安全性 → 開發者模式」，手動開啟並依提示重啟。
7. 開機後解鎖，閱讀手機提示並確認開啟、輸入密碼。
8. USB 接回同一支手機，在工具按「確認已開啟」；讀取成功後再使用 YS-pikmin。

## 操作範圍

- 只處理 USB 且已信任電腦的手機；多台手機需要明確選擇，操作前重新驗證識別碼。
- 只向 AMFI 發送顯示選項的 action 0；不自動啟用、重啟、確認重啟提示、掛載開發者映像或模擬定位。
- 不安裝 Python、不自動升級套件、不修改防毒或系統信任憑證。
- 「選項顯示成功」不代表「開發者模式已開啟」，必須在手機完成流程後再讀取確認。
- 手機重啟期間暫時消失屬於預期現象，重新解鎖並接回 USB 後再檢查。
- 讀不到狀態或手機拒絕時，顯示錯誤，不改用直接啟用的備援方法。
- 關閉工具即可結束；工具沒有背景常駐程序。手機上的設定由使用者自行管理。

## 原始碼與建置

使用 Python 3.12、Tk 8.6 與 requirements-device.txt（pymobiledevice3 9.29.1）。安裝依賴後執行 `python developer_mode_tool.py`，或在本專案使用 Developer_Mode.cmd。一般使用者下載完整 EXE 包，不需另外安裝 Python。

建置指令：

```powershell
python -m PyInstaller --noconfirm --onedir --windowed --contents-directory "程式資料" --name "開發者模式工具" --collect-all pymobiledevice3 --collect-all pytun_pmd3 --recursive-copy-metadata pymobiledevice3 developer_mode_tool.py
```

`--self-check --report <檔案>` 只檢查元件，`--check-usb --report <檔案>` 只讀取 USB 手機狀態，兩者都不顯示選項或修改手機設定。報告不寫入手機名稱、完整識別碼或配對金鑰。

依 GPL-3.0 發布，授權見 LICENSE。裝置通訊使用 pymobiledevice3 的既有信任配對與 AMFI 顯示選項協定。

## 本版驗證範圍

- 12 項工具單元測試通過，涵蓋錯誤手機、未信任、拔線、網路裝置排除、狀態不明、拒絕回應、逾時與取消清理。
- 專案完整測試 85 項：84 通過、1 項依環境略過。
- Windows x64 EXE 在移除外部 Python 路徑後通過元件檢查，包含 pymobiledevice3 9.29.1 與 Tk 8.6.15。
- EXE 已通過實體 iPhone 的 USB 唯讀掃描，正確讀取「開發者模式已開啟」，沒有修改手機設定或傳送定位。
- 獨立視窗已確認建立，正常關閉後沒有殘留工具程序；畫面擷取工具逾時，尚未完成截圖版面檢查。
- 尚未對新的實體手機執行「顯示選項 → 手機手動開啟 → 重啟確認」完整流程。單元測試使用模擬裝置，不能代替這項實機驗證。
- 此工具獨立發布，不需與 YS-pikmin 主程式同時執行。
