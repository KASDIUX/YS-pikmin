# v0.2.0 驗證紀錄

驗證日期：2026-10-18。環境：Windows、Python 3.12.10、pymobiledevice3 9.29.1、Node.js（JavaScript 語法檢查）。

## 自動測試

31 項 unittest 通過（包含原有 18 項）：

- 地理距離、跨日期變更線、終點與重複座標、非有限數值及範圍驗證。
- GPX 往返、多段拒絕、DTD 拒絕與格式錯誤。
- 等速行走、暫停續走、休眠不跳步、起點接續與循環回程。
- 模擬裝置斷線時停止且不切換預覽；還原失敗不回報成功。
- 暫停保留位置，停止傳送清除指令。
- 收藏持久化、損毀資料保留。
- 寫入 token、Origin／Host 檢查、靜態檔案範圍限制、API GPX 匯出。
- Windows 同一連接埠禁止多個伺服器同時監聽；重複啟動會開啟既有介面。
- 三種道路端點、經緯度順序、道路回程、幾何及貼齊距離驗證。
- 429、逾時、非 JSON 回應、過大回應、查詢節流及輸入限制。
- 道路查詢不阻塞停止指令；過期結果不能覆蓋新路線。
- 道路路線不自動加入未規劃的起點連接或回程；道路設定可持久化。
- 查詢失敗不會中斷既有手機狀態，也不會抹掉原路線。

Windows 沙箱對 `tempfile.TemporaryDirectory` 產生的 ACL 造成存取拒絕；同一組測試於正常本機權限執行後全部通過。測試資料限定在工作區，未使用真實手機。

## 人工介面測試

使用本機瀏覽器檢查地圖載入、示範路線、開始／暫停、座標及進度更新、路線儲存。UI 測試資料存於工作區的獨立 QA 資料夾，不隨交付包提供。

v0.2.0 使用獨立本機測試埠驗證「沿道路規劃」：6 個示範途經點產生 89 個步行軌跡點、約 1.52 公里。確認未先套用起點時會阻止開始，選取並套用起點後可進行預覽。另以 2 個公開示範點實際查詢步行回程，回傳 45 個軌跡點、約 772 公尺，最大貼齊距離 49.6 公尺。

外部實際查詢使用公開的台北 101 周邊示範座標，沒有傳送使用者的實際 GPS 或手機資料。服務設定依據：[FOSSGIS 服務說明](https://routing.openstreetmap.de/about.html)、[公開端點設定](https://github.com/fossgis-routing-server/osrm-frontend/blob/master/src/leaflet_options.js)、[OSRM API](https://project-osrm.org/docs/v5.24.0/api/)。

## 尚未驗證

- 真實 iPhone 的 USB／Wi-Fi 通訊、iOS 相容性與實際定位變化。
- 騎車／開車端點的真實服務回應（端點選擇及資料流程已用自動測試驗證；實際外部查詢測試以步行為主）。
- Apple 開發者映像下載／掛載與系統管理員通道啟動。
- 不同 Windows 電腦的安裝流程。
- Pikmin Bloom 內的效果。

裝置模組已匯入成功；tunneld、mounter CLI 選項已對照本機指定版本的 help。沒有為了測試而啟動管理員服務、配對手機或傳送模擬座標。


## v0.3.0 一鍵連線驗證

37 項單元測試通過，新增自動連線、取消、重複點擊、多裝置選擇、UAC 失敗、舊版 iOS 跳過通道與重用既有通道測試。
實機 iOS 26.6.2 USB：從無通道狀態按連接，經 Windows 管理員確認後自動啟動通道並連接成功；停止模擬定位指令已成功送出，手機實際位置需在手機地圖確認。沒有傳送新的測試座標。Wi-Fi 尚未實機驗證。


## v0.4.0 EXE 驗證

PyInstaller 6.22.3，Python 3.12.10，Windows x64。37 項單元測試通過。EXE 在 PATH 僅含 Windows System32、不含外部 Python 的子行程中通過：啟動、HTML 與 JS 資源、時區資料、預覽行走與暫停、pymobiledevice3 版本讀取與 USB 裝置掃描。內建 remote tunneld、mounter auto-mount、developer dvt 指令的 help 均成功。未在全新 Windows 電腦測試，也未用此 EXE 傳送新的實機模擬座標。


## v0.5.0 EXE 驗證

45 項單元測試通過。新版 EXE 在不含外部 Python PATH 的環境通過啟動、網頁資源、住家保存／傳送回家、單次搖桿步進、步進後無背景漂移、預覽行走、套件版本及 USB 掃描呼叫。內建 tunneld、auto-mount、DVT help 均可載入。本次 USB 掃描未找到手機，因此沒有進行新版實機定位傳送；Wi-Fi 也未實測。


## Wi-Fi 診斷（2026-10-19）

同一區網下，Apple 無線服務 TCP 62078 可連線，使用既有 USB 配對資料的直接 TCP lockdown 驗證成功，並確認是同一支 iOS 26.6.2 手機。自動裝置／Bonjour 掃描未找到 Network 裝置。經使用者確認 UAC 後測試 Wi-Fi CoreDeviceProxy 通道，連續兩次在 request_tunnel_establish 握手時收到 ConnectionTerminatedError；未進入 RSD 或定位服務，沒有傳送座標。結果為無線配對服務可用，但目前定位通道未驗證成功，不能宣稱 Wi-Fi 定位可用。此測試沒有更動手機無線偵錯設定。


## 指定 IP 調整

53 項測試通過，包含私有 IP 驗證、首次裝置身分需求、無效 IP 不留下連線工作、確認目標後不借用 USB 共用通道，以及子程序失敗後停止心跳。新增指定 IP 入口與專用 TCP 通道生命週期。實機配對可用，但 iOS 26.6.2 拔除 USB 後仍在 Wi-Fi CoreDeviceProxy 握手遭 ConnectionTerminatedError。已依使用者同意暫時開啟 Wi-Fi 偵錯測試，結果仍失敗，且已確認恢復原值 False。沒有傳送定位座標。


## v0.6.0 EXE 驗證

53 項單元測試通過。Python 3.12.10 / PyInstaller 6.22.3 建置 Windows x64 EXE；頂層只有 Pikmin launch.exe 與「程式資料」資料夾。EXE 在 PATH 僅含 Windows System32、移除 PYTHONHOME/PYTHONPATH 的環境通過啟動、網頁資源、分類收藏、固定高度樣式、住家、搖桿步進與無背景漂移、路線預覽、套件版本、USB 掃描（1 支手機）。內建 tunneld、auto-mount、DVT help 正常，Wi-Fi helper 能載入並拒絕無效工作目錄。此 EXE 測試未建立實機定位連線或傳送座標；同日原始碼版已重新驗證 USB 定位服務連線。Wi-Fi 定位仍失敗，尚未在全新 Windows 完整驗證。
