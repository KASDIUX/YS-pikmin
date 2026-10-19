# Windows EXE 建置

使用 Python 3.12 x64 與 PyInstaller（建置時版本寫於發行說明）。先安裝 requirements-device.txt 與 pyinstaller，再於原始碼資料夾執行：

```powershell
python -m PyInstaller --noconfirm --clean --onedir --contents-directory "程式資料" --console --name "Pikmin launch" --add-data "web;web" --collect-all pymobiledevice3 --collect-all pytun_pmd3 --recursive-copy-metadata pymobiledevice3 --hidden-import prepare_device start.py
```

整個 dist/Pikmin launch 資料夾必須一起散佈，不能只複製 EXE。依賴全部位於「程式資料」資料夾；發行 ZIP 不加外層資料夾，頂層只有 EXE 和此資料夾。說明和授權也放入「程式資料」。收藏預設儲存於 %LOCALAPPDATA%/YS-pikmin/data。--device-cli 是封裝的裝置工具入口，通道會用同一個 EXE 啟動，不需外部 Python。
