# SOLOS XML → PCBAFCT.csv 轉檔工具

`solos_xml_to_pcbafct.py` — 將 TOPCON SOLOS 量測 XML（JOIA / `nsLM` 格式）轉成 **PCBAFCT 21 欄格式**的 CSV，
並依「測試項目對照表」自動填入**卡控上下限**與**判定結果**。

> **輸出檔名固定為 `Lensometer.csv`**（內容為 PCBAFCT 21 欄格式）。
> 輸入檔名格式：`<序號>_<YYYYMMDD>_<HHMMSS>_TOPCON_SOLOS_00000.xml`（序號讀自 XML 內容，與檔名無關）。
>
> 輸出格式與 `Maera_PCBAFCT.csv` 相同，卡控依「測試項目對照表」；已對照既有 `SOLOS_客戶測試項目結果_*.csv` 逐項驗證，**19 項 × 2 檔數值全數一致**。
> 上述為歷史驗證（含兩項 N/A 占位列）；現行版本依不測決議移除 Lens Tint Uniformity 與 UVB Mean Transmittance，預設輸出 **17 項**，不再保留這兩列。
>
> **適用範圍：本廠僅測試 Non-RX（平光）眼鏡。** 工具只支援平光配置 `D10` / `D11` / `D13` / `D14`（透明/染色）與 `D15`（平光太陽鏡片）；RX Capable（`D12` / `D16`）**已移除**。

---

## 目錄

1. [概觀](#1-概觀)
2. [環境需求](#2-環境需求)
3. [快速開始](#3-快速開始)
4. [命令列參數（完整）](#4-命令列參數完整)
5. [輸入：SOLOS XML 結構](#5-輸入solos-xml-結構)
6. [輸出：PCBAFCT.csv](#6-輸出pcbafctcsv)
7. [測試項目與對照表對應](#7-測試項目與對照表對應)
8. [卡控規格（完整）](#8-卡控規格完整)
9. [計算方法與公式](#9-計算方法與公式)
10. [判定規則](#10-判定規則)
11. [計算實例（逐步）](#11-計算實例逐步)
12. [驗證報告](#12-驗證報告)
13. [常見問題與疑難排解](#13-常見問題與疑難排解)
14. [詞彙表](#14-詞彙表)
15. [已知限制與待確認事項](#15-已知限制與待確認事項)
16. [附錄 A：紅黃綠權重表](#附錄-a紅黃綠權重表380780-nm)
17. [附錄 B：程式內部常數與調整方式](#附錄-b程式內部常數與調整方式)
18. [附錄 C：相關檔案與來源文件](#附錄-c相關檔案與來源文件)

---

## 1. 概觀

### 1.1 這支工具做什麼

| 步驟 | 說明 |
|---|---|
| ① 辨識 | 解析 SOLOS 量測 XML（JOIA 標準 `nsLM` 命名空間），取出屈光度、散光、稜鏡、透光率、逐波長光譜、瞳距等 |
| ② 計算 | 依定義換算需計算項目：稜鏡不平衡、染色不平衡、訊號透光率 τsig（ANSI Z80.3 加權）、UVA 上限 |
| ③ 套卡控 | 依「測試項目對照表」填入 `LowerLimit` / `UpperLimit` |
| ④ 判定 | 依卡控比對，產生 `ErrorCode` / `ErrorDescription`（`0`/`Success`、`NG`/`OutOfSpec`）；同意不測項目不輸出 |
| ⑤ 輸出 | 產生 PCBAFCT.csv（21 欄，UTF-8 BOM + CRLF） |

### 1.2 資料流

```
                測試項目對照表 ─┐
                     (卡控)      │
                                 ▼
  SOLOS XML ──► 解析 ──► 換算 ──► 套卡控 ──► 判定 ──► PCBAFCT 格式 (21 欄)
 (JOIA/nsLM)          (τsig 等)                           ├─ 每個 XML 一個檔
                                                          └─ 檔名 = Lensometer.csv
                                                             （批次：<序號>/Lensometer.csv）
```

### 1.3 檔案清單

| 檔案 | 角色 |
|---|---|
| `solos_xml_to_pcbafct.py` | 工具本體（單檔，標準庫即可執行） |
| `OP.dat` | 操作員工號（單行文字），轉檔時讀取程式所在目錄的此檔並填入所有 `Operator` 欄位 |
| `README.md` | 本文件 |
| `測試項目對照表.csv` / `Barney3_測試項目對照表_逐項明細.xlsx` | 卡控與權重來源 |
| `Maera_PCBAFCT.csv` | 輸出格式參考（PCBAFCT 21 欄） |
| `SOLOS_客戶測試項目結果_*.csv` | 逐項驗證參考 |
| `*_TOPCON_SOLOS_00000.xml` | 範例輸入 |

---

## 2. 環境需求

- **Python 3.8 以上**（本機已安裝 Python 3.13；一般 3.6+ 亦可）
- **無需額外套件**：只使用標準庫 `xml.etree.ElementTree`、`csv`、`argparse`、`datetime`、`os`、`glob`、`sys`
- 作業系統不限（Windows / macOS / Linux），輸出檔案可直接用 Excel 開啟

檢查：

```bat
python --version
```

---

## 3. 快速開始

> **實際檔名格式**：`<序號>_<YYYYMMDD>_<HHMMSS>_TOPCON_SOLOS_00000.xml`
> 例如 `SAA671D0000H_20261019_085916_TOPCON_SOLOS_00000.xml`，序號為 `SAA671D0000H`。
> 序號是從 XML 內容的 `nsCommon:Patient/ID` 讀取，**與檔名無關**。

```bat
:: 1) 單一 XML -> 產生 Lensometer.csv（與 XML 同資料夾）
python solos_xml_to_pcbafct.py "SAA671D0000H_20261019_085916_TOPCON_SOLOS_00000.xml"

:: 2) 整個資料夾批次轉檔，輸出到指定資料夾
python solos_xml_to_pcbafct.py ".\xmls" -o ".\csvs"

:: 3) 指定輸出檔名
python solos_xml_to_pcbafct.py in.xml -o PCBAFCT.csv
```

畫面輸出範例：

```
共 1 個 XML 檔。
------------------------------------------------------------------------
SN=SAA671D0000H 卡控配置：Config 未提供; 採 D10-D14 平光卡控
[OK] SAA671D0000H_20261019_085916_TOPCON_SOLOS_00000.xml  ->  Lensometer.csv  (PASS=17, FAIL=0, N/A=0)
```

---

## 4. 命令列參數（完整）

### 4.1 參數表

| 參數 | 型別 | 說明 | 預設 |
|---|---|---|---|
| `inputs` | 位置參數（1+） | 要處理的 XML 檔，或包含 XML 的資料夾；可一次多個 | 必填 |
| `-o`, `--output` | 字串 | 輸出檔（單檔）或輸出資料夾（多檔） | `Lensometer.csv`（與輸入同資料夾） |
| `--config` | `D10`/`D11`/`D13`/`D14`/`D15` | 手動指定鏡片配置，優先於 SN 自動判斷（僅 Non-RX 平光） | XML 序號第 6–7 碼 HW ID 為 `1F` 用 D15，其餘用 D10–D14 平光卡控；`1C`／`1G` 拒絕轉檔 |
| `--suite` | 字串 | `TestSuiteName` 欄位值 | `SOLOS_SpectaclesReport_1.3.0` |
| `--ticket` | 字串 | `TicketID` 欄位值 | `Debug` |
| `--with-note` | 旗標 | 額外輸出第 22 欄「備註」（說明卡控來源、計算方式） | 關閉（維持 21 欄） |
| `--include-extra` | 旗標 | 額外輸出未列卡控欄位：`Axis`、`BlueTransmittance`、`PD` | 關閉 |
| `-h`, `--help` | 旗標 | 顯示說明 | — |

### 4.2 `-o` 輸出規則

| 情況 | 判定 | 結果 |
|---|---|---|
| 1 個輸入，`-o` 指向檔案 | 輸出檔 | 寫入該檔名 |
| 多個輸入 | 輸出資料夾 | 每個 XML → `<序號>/Lensometer.csv`（子資料夾不存在會自動建立） |
| `-o` 指向既存資料夾，或結尾為 `\` / `/` | 輸出資料夾 | 單檔 → `Lensometer.csv`；多檔 → `<序號>/Lensometer.csv` |
| 省略 `-o` | 與輸入同資料夾 | 單檔 → `Lensometer.csv`；多檔 → `<序號>/Lensometer.csv` |

### 4.3 結束碼（Exit Code）

| 值 | 意義 |
|---|---|
| `0` | 全部成功 |
| `1` | 部分檔案解析失敗（其餘仍會完成） |
| `2` | 找不到任何 XML |

---

## 5. 輸入：SOLOS XML 結構

### 5.1 命名空間

```xml
<Ophthalmology
    xmlns:nsCommon="http://www.joia.or.jp/standardized/namespaces/Common"
    xmlns:nsLM="http://www.joia.or.jp/standardized/namespaces/LM">
```

- `nsCommon`：機台 / 病患（序號）等共用資訊
- `nsLM`：鏡片量測（LM = Lens Meter）
- 註：`<TransmittanceData>` 標籤**不帶**命名空間前置詞

### 5.2 元素對照表（XML → 用途）

| XML 路徑 | 用途 | 備註 |
|---|---|---|
| `nsCommon/Company` | 廠商 | `TOPCON` |
| `nsCommon/ModelName` | 機型 | `SOLOS` → `TestVariation` |
| `nsCommon/MachineNo` | 機號 | `00000` → `TesterName` 後綴 |
| `nsCommon/Date` + `Time` | 量測時間 | → `TSRID`、`StartDateTime` |
| `nsCommon/Patient/ID` | **序號** | → `SerialNumber` |
| `nsLM:Measure/nsLM:LM/nsLM:R` | 右眼量測 | |
| `nsLM:Measure/nsLM:LM/nsLM:L` | 左眼量測 | |
| `…/nsLM:Sphere unit="D"` | 屈光度 | |
| `…/nsLM:Cylinder unit="D"` | 散光 | |
| `…/nsLM:Axis unit="deg"` | 柱鏡軸 | 未列卡控 |
| `…/nsLM:PrismX unit="pri" base="out"` | 稜鏡水平 | 空值 = 0 |
| `…/nsLM:PrismY unit="pri" base="up"` | 稜鏡垂直 | 空值 = 0 |
| `…/nsLM:UVTransmittance unit="%"` | UVA 平均透光率 | |
| `…/nsLM:BlueTransmittance unit="%"` | 藍光透光率 | 未列卡控 |
| `…/nsLM:VisibleTransmittance unit="%"` | 可見光透光率 τv | |
| `…/TransmittanceData/TransmittanceValue` | 逐波長光譜 | 每點 `WaveLength` + `Transmittance` |
| `nsLM:Measure/nsLM:PD/Distance` | 瞳距 | 未列卡控 |
| `nsLM:Measure/nsLM:PD/DistanceR` | 右瞳距 | 未列卡控 |
| `nsLM:Measure/nsLM:PD/DistanceL` | 左瞳距 | 未列卡控 |

### 5.3 光譜資料範圍

- 波長：**315 – 800 nm**，每 1 nm 一點 → 共 **486 點 / 每眼**
- 每眼僅一組光譜 → 無法計算「鏡片內均勻度」（無 T01/T02 兩點資料）

---

## 6. 輸出：PCBAFCT.csv

### 6.1 21 欄定義

| # | 欄位 | 來源 / 值 | 格式範例 |
|---|---|---|---|
| 1 | `TSRID` | XML 日期+時間（`YYYYMMDDhhmmss`） | `20261009072121` |
| 2 | `SerialNumber` | XML `Patient/ID` | `S8A670R0007J` |
| 3 | `PartNumber` | （空） | |
| 4 | `Operation` | 固定 | `SpectaclesReport` |
| 5 | `TesterName` | `ModelName-MachineNo` | `SOLOS-00000` |
| 6 | `TestName` | 測試項目名 | `Sphere`、`Signal Transmittance` |
| 7 | `TestVariation` | `ModelName` | `SOLOS` |
| 8 | `ErrorCode` | 判定 | `0` / `NG` / （空） |
| 9 | `Operator` | 程式同目錄 `OP.dat` 內容，去除 BOM、前後空白與換行 | `OP123456` |
| 10 | `StartDateTime` | XML 日期+時間 | `10/09/2026 07:21:21 AM` |
| 11 | `EndDateTime` | 同 `StartDateTime` | `10/09/2026 07:21:21 AM` |
| 12 | `ProductionTest` | 固定 | `0` |
| 13 | `TestSuiteName` | `--suite` | `SOLOS_SpectaclesReport_1.3.0` |
| 14 | `ErrorDescription` | 判定 | `Success` / `OutOfSpec` |
| 15 | `MeasurementName` | 量測項代號 | `R_Sphere`、`L_tau_sig_r` |
| 16 | `Value` | 量測值 | `0.00`、`89`、`87.7` |
| 17 | `Units` | 單位 | `D`、`%`、`Δ` |
| 18 | `LowerLimit` | 卡控下限（依對照表） | `-0.13`、`40` |
| 19 | `UpperLimit` | 卡控上限（依對照表） | `0.13`、`0.50`、`44.50` |
| 20 | `PegaCode` | （空） | |
| 21 | `TicketID` | `--ticket` | `Debug` |
| （22） | `備註` | 僅 `--with-note` 時輸出 | 卡控來源、計算方式 |

### 6.2 編碼與換行

每次轉檔都會重新讀取程式所在目錄的 `OP.dat`（UTF-8，可含 BOM），一般測試與
`--include-extra` 的所有資料列都填入相同 `Operator`。未提供檔案時維持空白；
檔案存在但為空、多行或無法讀取時，轉檔失敗，避免寫入錯誤工號。
SFIS 上傳預設沿用 CSV 的 `Operator`；若 `solos_test.ini` 的 `operator` 非空，仍會以該設定覆寫上傳工號。

- 編碼：**UTF-8 with BOM**（`utf-8-sig`）→ Excel 直開不亂碼
- 換行：**CRLF**（`\r\n`，Windows 慣例）
- 含逗號的欄位自動加引號（`csv` 標準行為）

### 6.3 輸出檔名規則

- 輸出檔名固定為 **`Lensometer.csv`**（內容為 PCBAFCT 21 欄格式）
- 單檔：寫在與輸入 XML 同資料夾
- 批次（多檔）：每個 XML 放進自己的 `<序號>/` 子資料夾，避免同名覆蓋
- 以 `-o` 指定則依 [4.2](#42--o-輸出規則) 規則

### 6.4 範例輸出（節錄）

```csv
TSRID,SerialNumber,PartNumber,Operation,TesterName,TestName,TestVariation,ErrorCode,Operator,StartDateTime,EndDateTime,ProductionTest,TestSuiteName,ErrorDescription,MeasurementName,Value,Units,LowerLimit,UpperLimit,PegaCode,TicketID
20261009072121,S8A670R0007J,,SpectaclesReport,SOLOS-00000,Sphere,SOLOS,0,,10/09/2026 07:21:21 AM,10/09/2026 07:21:21 AM,0,SOLOS_SpectaclesReport_1.3.0,Success,R_Sphere,0.00,D,-0.13,0.13,,Debug
20261009072121,S8A670R0007J,,SpectaclesReport,SOLOS-00000,Cylinder,SOLOS,NG,,10/09/2026 07:21:21 AM,10/09/2026 07:21:21 AM,0,SOLOS_SpectaclesReport_1.3.0,OutOfSpec,L_Cylinder,-0.50,D,,0.13,,Debug
20261009072121,S8A670R0007J,,SpectaclesReport,SOLOS-00000,Signal Transmittance,SOLOS,0,,10/09/2026 07:21:21 AM,10/09/2026 07:21:21 AM,0,SOLOS_SpectaclesReport_1.3.0,Success,R_tau_sig_r,87.7,%,8,,,Debug
```

---

## 7. 測試項目與對照表對應

共 **17 列**（依對照表順序；R/L 分列；同意不測的鏡片染色均勻度與 UVB 不輸出，也不上傳 SFIS）：

| # | 客戶章節 | 客戶測試項目 | 客戶文件原文名稱 | 對應類型 | 眼別 | MeasurementName | Units |
|---|---|---|---|---|---|---|---|
| 1 | 8.2.1 | 屈光度（球面度數） | Refractive Power (4.9.1) | 直接對應 | 右 | `R_Sphere` | D |
| 2 | 8.2.1 | 屈光度（球面度數） | Refractive Power (4.9.1) | 直接對應 | 左 | `L_Sphere` | D |
| 3 | 8.2.2 | 散光（柱面度數） | Astigmatic Power (4.9.2) | 直接對應 | 右 | `R_Cylinder` | D |
| 4 | 8.2.2 | 散光（柱面度數） | Astigmatic Power (4.9.2) | 直接對應 | 左 | `L_Cylinder` | D |
| 5 | 8.2.3 | 稜鏡不平衡 — 水平 | Prismatic Imbalance (4.9.3) | 需計算 | 兩眼 | `Prism_Imbalance_H` | Δ |
| 6 | 8.2.3 | 稜鏡不平衡 — 垂直 | Prismatic Imbalance (4.9.3) | 需計算 | 兩眼 | `Prism_Imbalance_V` | Δ |
| 7 | 8.3.1.2 | 鏡片染色不平衡 | Lens Tint Imbalance (4.12) | 需計算 | 兩眼 | `Tint_Imbalance` | % |
| 8 | 8.3.2.1 | 可見光透光率 τv | Luminous Transmittance (τv) | 直接對應 | 右 | `R_tau_v` | % |
| 9 | 8.3.2.1 | 可見光透光率 τv | Luminous Transmittance (τv) | 直接對應 | 左 | `L_tau_v` | % |
| 10 | 8.3.2.1 | 紅色訊號透光率 τsig | Red Signal Transmittance (τsig) | 需計算 | 右 | `R_tau_sig_r` | % |
| 11 | 8.3.2.1 | 紅色訊號透光率 τsig | Red Signal Transmittance (τsig) | 需計算 | 左 | `L_tau_sig_r` | % |
| 12 | 8.3.2.1 | 黃色訊號透光率 τsig | Yellow Signal Transmittance (τsig) | 需計算 | 右 | `R_tau_sig_y` | % |
| 13 | 8.3.2.1 | 黃色訊號透光率 τsig | Yellow Signal Transmittance (τsig) | 需計算 | 左 | `L_tau_sig_y` | % |
| 14 | 8.3.2.1 | 綠色訊號透光率 τsig | Green Signal Transmittance (τsig) | 需計算 | 右 | `R_tau_sig_g` | % |
| 15 | 8.3.2.1 | 綠色訊號透光率 τsig | Green Signal Transmittance (τsig) | 需計算 | 左 | `L_tau_sig_g` | % |
| 16 | 8.3.2.1 | UVA 平均透光率 | UVA Mean Transmittance (315–380 nm) | 直接對應 | 右 | `R_UVA_Mean` | % |
| 17 | 8.3.2.1 | UVA 平均透光率 | UVA Mean Transmittance (315–380 nm) | 直接對應 | 左 | `L_UVA_Mean` | % |

> 對應類型定義 — **直接對應**：XML 直接輸出；**需計算**：由多筆資料換算；**無對應**：XML／儀器無此資料。

### 額外輸出（`--include-extra`，未列卡控）

| TestName | MeasurementName | Units | 說明 |
|---|---|---|---|
| `Axis` | `Right_Axis` / `Left_Axis` | deg | 柱鏡軸 |
| `BlueTransmittance` | `Right_BlueTransmittance` / `Left_BlueTransmittance` | % | 藍光透光率 |
| `Distance` | `PD_Distance` | mm | 瞳距（總） |
| `DistanceR` | `PD_DistanceR` | mm | 右瞳距 |
| `DistanceL` | `PD_DistanceL` | mm | 左瞳距 |

---

## 8. 卡控規格（完整）

### 8.1 D10 / D11 / D13 / D14（平光，預設）

| 項目 | LowerLimit | UpperLimit | 判定方式 |
|---|---|---|---|
| Sphere 屈光度 | `-0.13` | `0.13` | 區間 |
| Cylinder 散光 | （空） | `0.13` | 取**絕對值**比上限 |
| 稜鏡不平衡 H | （空） | `0.50` | 左右眼相減（絕對值） |
| 稜鏡不平衡 V | （空） | `0.25` | 左右眼相減（絕對值） |
| 鏡片染色不平衡 | （空） | `15` | 左右眼透過率差的絕對值（百分點） |
| 可見光透光率 τv | `40` | （空） | 下限 |
| 訊號透光率 紅 τsig | `8` | （空） | 下限 |
| 訊號透光率 黃 τsig | `6` | （空） | 下限 |
| 訊號透光率 綠 τsig | `6` | （空） | 下限 |
| UVA 平均透光率 | （空） | `0.5 · τv` | 依眼別動態上限 |

> 平光樣品卡控為 `±0.13 D`（Sphere）、`≦ 0.13 D`（Cylinder）。

### 8.2 D15（Plano, Sunlens）

與平光**相同，唯 τv 改為 `8` ~ `40` %**（下限 8、上限 40）。

### 8.3 RX Capable（D12 / D16）— 不支援

- 本廠**僅測試 Non-RX（平光）**，故工具**已移除** D12 / D16。
- 執行 `--config D12` 或 `--config D16` 會直接被拒絕（argparse 只接受 `D10/D11/D13/D14/D15`）。
- 若未來要測 RX，需先取得「處方公差」規則，再加入程式 [`CONFIG_LIMITS`](#附錄-b程式內部常數與調整方式)。

### 8.4 鏡片配置對照（來源：對照表）

| 配置代碼 | 鏡片配置 | Recipe | 說明 |
|---|---|---|---|
| D10 | Plano, Clear | POR（Plan of Record） | 平光透明，基準配置 |
| D11 | Plano, Tinted | Recipe #1 | 平光染色 |
| D12 | RX Capable | Recipe #2 | 可配度數 — **本廠不測 / 工具不支援** |
| D13 | Plano, Tinted | Recipe #3 | 平光染色 |
| D14 | Plano, Clear | Recipe #4 | 平光透明 |
| D15 | Plano, Sunlens | Recipe #5 | 平光太陽鏡片（τv 8–40%） |
| D16 | RX Capable | Recipe #6 | 可配度數 — **本廠不測 / 工具不支援** |

> **重要**：XML **不含**獨立配置代碼。未加 `--config` 時，以 XML 內容 `Patient/ID` 的序號判斷，不看檔名前綴：
> 只依序號第 **6–7 碼 HW ID** 判斷，不受前綴影響：`1F`（例如 `SAA671F…`／`SBA671F…`）自動套用 **D15（可見光透光率 8–40%）**；其餘允許的序號維持一般平光卡控。
> 已確認的 HW ID：**1F** 使用太陽鏡片；**1H／1B／1D／1E** 使用一般平光。
> **1C／1G 禁止測試**：檢查 XML `Patient/ID` 序號第 **6–7 碼**（例如 `SAA671C…`／`SAA671G…`），
> 不比對檔名或其他位置的相同字串。直接轉檔會報錯、回傳 1，且不產生該筆 CSV；`--config` 也不能略過。
> 自動流程會在 SFIS `/C` 前攔截，顯示錯誤、不轉檔、不上傳 SFIS，並依原流程封存 XML／PDF（不包含既有 CSV）。
> `--once` 回傳 72；持續模式記錄錯誤後等待下一筆。可用 `python solos_xml_to_pcbafct.py --check-serial <SN>` 單獨檢查。
> 例如 `SAA671D0001F` 的 HW ID 是 1D，尾碼 F 不會使它改用太陽鏡片標準。
> 每份 XML 分別選擇配置，因此同批可混合 D／F；實際序號與配置會顯示於輸出訊息，`--with-note` 會附上配置備註。
> 手動指定 `--config` 時優先採用指定值，並套用到該次命令的所有輸入檔案。

---

## 9. 計算方法與公式

### 9.1 直接對應

| 項目 | 來源 |
|---|---|
| Sphere / Cylinder | `nsLM:Sphere` / `nsLM:Cylinder`（原字串，如 `0.00`、`-0.50`） |
| UVA 平均透光率 | `nsLM:UVTransmittance` |
| 可見光透光率 τv | `nsLM:VisibleTransmittance` |

### 9.2 稜鏡不平衡（左右眼相減）

```
Prism_Imbalance_H = |PrismX_R − PrismX_L|
Prism_Imbalance_V = |PrismY_R − PrismY_L|
```
- 欄位無數值時視為 **0**（依對照表備註）

### 9.3 鏡片染色不平衡

```
Tint_Imbalance = |τv_R − τv_L|  （百分點）
```

直接使用 XML 的可見光透過率相減後取絕對值，不除以平均值。上限為 15 個百分點；CSV 的 `Units` 保留 `%` 以相容既有格式。任一眼缺值時輸出空白，雙眼皆為 0 時輸出 `0.00`。

### 9.4 訊號透光率 τsig（紅 / 黃 / 綠）— ANSI Z80.3 §4.10

```
τsig = Σ τ(λ)·W(λ) / Σ W(λ)        λ = 380..780 nm（步進 10 nm）
```

| 訊號 | 權重 `W(λ)` | 權重表欄位 |
|---|---|---|
| 紅色 | `SA(λ)·τ_red(λ)·ȳ(λ)` | H 欄 |
| 黃色 | `SA(λ)·τ_yellow(λ)·ȳ(λ)` | I 欄 |
| 綠色 | `SA(λ)·τ_green(λ)·ȳ(λ)` | J 欄 |

- `τ(λ)`：直接取 XML `<TransmittanceData>` 於該波長的量測值（**精確取點，不作區間平均**）
- 權重內嵌於程式（`W_H` / `W_I` / `W_J`），來源為「紅黃綠權重表」工作表
- 分母（權重和）：紅 `1.0884`、黃 `6.8340`、綠 `2.1652`
- 三色皆採光度 `ȳ` 權重；F/G 是 `x̄` 權重，K/L 是 `z̄` 權重，不混用於本項光度透過率。H/I/J 亦與提供的驗算表公式一致。

### 9.5 UVA 平均透光率上限

```
UpperLimit = 0.5 × τv      （分左右眼各別計算）
```
- 例：τv = 89 → 上限 `44.50` %；τv = 69 → 上限 `34.50` %

### 9.6 數值格式規則

| 項目 | 格式 | 範例 |
|---|---|---|
| Sphere / Cylinder | 沿用 XML 原字串 | `0.00`、`-0.00`、`-0.50` |
| UVA 值 | 沿用 XML 原字串 | `0` |
| τv 值 | 沿用 XML 原字串 | `89`、`69` |
| τsig | 小數 1 位 | `87.7` |
| 稜鏡不平衡 | 小數 3 位 | `0.000` |
| 染色不平衡 | 小數 2 位（百分點） | `0.00`、`2.00` |
| UVA 上限 | 小數 2 位 | `44.50` |
| 卡控值 | 與對照表一致（整數不補小數） | `-0.13`、`0.50`、`15`、`40`、`8` |

---

## 10. 判定規則

| 情況 | `ErrorCode` | `ErrorDescription` |
|---|---|---|
| 落在卡控內 | `0` | `Success` |
| 超出卡控 | `NG` | `OutOfSpec` |
| 客人同意不測項目 | 不輸出資料列 | 不輸出資料列 |

- 一般平光 D10/D11/D13/D14 的 τv 嚴格 **>40%**；D15 為 **8% <= τv <= 40%**。其餘數值上下限含邊界。
- 計算項目及 UVA 動態上限均以未取位的數值判定；小數位格式只影響 CSV 顯示。僅消除計算值界限比較中 `1e-12` 以內的浮點數誤差，不增加量測容差。
- CSV 的數字 `LowerLimit=40` 本身不能表示嚴格大於；SFIS 使用 CSV 的 `ErrorCode` 判定，不重新按數字上下限判定。
- `LowerLimit` / `UpperLimit` 留空 = 該側不設限。
- Cylinder 以**絕對值**與上限比較（如 `-0.50` → |−0.50| = 0.50 > 0.13 → FAIL）。
- 無卡控但 `--include-extra` 的欄位：一律 `Success`。

---

## 11. 計算實例（逐步）

以 **RX-S8A670R0007J**（序號 `S8A670R0007J`）為例：

**a) 稜鏡不平衡**（XML 中 `PrismX`/`PrismY` 皆為空 = 0）
```
H = |0 − 0| = 0.000   → 0.000 <= 0.50  PASS
V = |0 − 0| = 0.000   → 0.000 <= 0.25  PASS
```

**b) 鏡片染色不平衡**（τv_R = 89、τv_L = 89）
```
|89 − 89| = 0.00 個百分點   → 0.00 <= 15  PASS
右眼 12%、左眼 10%：|12 − 10| = 2.00 個百分點   → 2.00 <= 15  PASS
```

**c) 訊號透光率 τsig（SBA671F0006U 右眼）**
```
紅色 = Σ τ·H / Σ H = 21.721885…  → 21.7 %  PASS
黃色 = Σ τ·I / Σ I = 11.511092…  → 11.5 %  PASS
綠色 = Σ τ·J / Σ J = 12.986283…  → 13.0 %  PASS
```

**d) UVA 上限**
```
右眼 = 0.5 × 89 = 44.50 %；左眼 = 0.5 × 89 = 44.50 %
UVA 量測值 = 0  → 0 <= 44.50  PASS
```

**e) 散光（左眼）**
```
|−0.50| = 0.50 > 0.13  → FAIL (OutOfSpec)
```

---

## 12. 驗證報告

### 現行公式查核（2026-11-10）

依據為本 workspace 的原始 XML/PDF、三份 Excel 權重工作表、驗算表公式，以及使用者確認的染色不平衡定義。對照表引用的 ANSI 與客戶規格原文未完整提供；本查核不等同外部標準認證。

- `SBA671F0006U` 的 Excel 與 XML 左右眼各 486 點光譜完全一致；H/I/J 各 41 點與程式常數一致。
- 可見光及 UVA 直接取 XML；PDF 圖示為可見光右 12%、左 10%、UVA 雙眼 0%，相符。
- 染色不平衡依使用者確認採 `|R-L|`，上限 15 個百分點；原始 Excel 的「相對差異」文字仍保留為來源，並非相同公式。
- 稜鏡採左右差值絕對值，只有稜鏡空值依備註當 0；染色均勻度及 UVB 依同意不測，不輸出。
- `1F` 自動選 D15；一般平光嚴格大於 40%，D15 含 8% 與 40%。
- 測試包含單波長權重辨識、邊界值、取位前判定、浮點邊界，以及離線 BAT/CSV/SFIS payload 流程。

| 項目 | 右眼 | 左眼 |
|---|---:|---:|
| τv (%) | 12 | 10 |
| τsig 紅 (%) | 21.7 | 19.8 |
| τsig 黃 (%) | 11.5 | 9.3 |
| τsig 綠 (%) | 13.0 | 9.9 |
| UVA (%) / 上限 | 0 / 6.00 | 0 / 5.00 |

染色不平衡 `2.00`；本筆 D15 共 17 項 PASS。舊 log 不會自動更新，需重新轉檔。

### 歷史版本結果（非現行公式基準）

以下歷史結果含舊的相對差異與 F/G 權重；不得用來驗證現行 H/I/J 算法。歷史來源檔目前不全在 workspace，未宣稱重新驗證。

歷史版本輸出（含兩項 N/A 占位列）已與既有 `SOLOS_客戶測試項目結果_*.csv` 逐項自動比對；現行版本不輸出這兩項：
比對欄位 **Value / Units / LowerLimit / UpperLimit / ErrorCode / ErrorDescription / TSRID / SerialNumber / StartDateTime**。

| 裝置 | 比對項數 | 差異數 |
|---|---|---|
| RX-S8A670R0007J | 19 | **0** |
| NON-RX-S9A670P00013 | 19 | **0** |

| 項目 | RX 右 | RX 左 | NON-RX 右 | NON-RX 左 |
|---|---|---|---|---|
| Sphere (D) | 0.00 | 0.00 | 0.00 | -0.00 |
| Cylinder (D) | -0.00 | **-0.50 (FAIL)** | -0.00 | -0.00 |
| Prism H / V (Δ) | 0.000 / 0.000 | — | 0.000 / 0.000 | — |
| Tint Imbalance (%) | 0.00 | — | 8.33 | — |
| τv (%) | 89 | 89 | 69 | 75 |
| τsig 紅 (%) | 87.7 | 89.0 | 75.8 | 83.9 |
| τsig 黃 (%) | 87.9 | 88.7 | 74.5 | 81.4 |
| τsig 綠 (%) | 86.7 | 87.5 | 66.3 | 72.5 |
| UVA (%) / 上限 | 0 / 44.50 | 0 / 44.50 | 0 / 34.50 | 0 / 37.50 |
| Tint Uniformity / UVB | N/A | N/A | N/A | N/A |

- 亦已驗證：輸出為 UTF-8 BOM + CRLF、21 欄（`--with-note` 為 22 欄）。

---

## 13. 常見問題與疑難排解

| 問題 | 原因 / 解法 |
|---|---|
| 想指定太陽鏡片配置 | 加 `--config D15`（τv 改 8–40%） |
| 所有 τv 都 FAIL 但數值看起來正常 | 可能誤用 D15（8–40%）；平光樣品請勿加 `--config D15` |
| 想知道卡控是哪來的 | 加 `--with-note`，第 22 欄會說明；或看 console 開頭訊息 |
| 找不到輸出檔 | 預設與輸入同資料夾、檔名 `Lensometer.csv`（批次在 `<序號>/` 子資料夾）；或用 `-o` 指定 |
| 資料夾內有多層 XML | 目前**不遞迴**，只處理該層 `*.xml` / `*.XML`；請分別指定子資料夾 |
| 同名輸出被覆蓋 | 單檔重跑會覆蓋 `Lensometer.csv`；批次已用 `<序號>/` 子資料夾避免互蓋 |
| Excel 開啟亂碼 | 不應發生（UTF-8 BOM）；若用記事本另存請勿改為 ANSI |
| 判定邊界值算不算過 | 一般平光 τv=40% 不通過；D15 的 8% 與 40% 通過，其餘含邊界 |
| 需要處方（RX）公差 | 本廠只測 Non-RX，工具已移除 D12/D16；若日後要測 RX 需先提供處方公差 |
| 不小心用了 `--config D12` | 會被拒絕（本廠不測 RX）；平光樣品直接用預設即可 |

---

## 14. 詞彙表

| 詞彙 | 說明 |
|---|---|
| **JOIA** | 日本光學儀器工業會標準；本 XML 依其 `nsLM` 規範輸出 |
| **nsLM** | JOIA 鏡片量測（Lens Meter）命名空間 |
| **Sphere（S）** | 球面度數（屈光度） |
| **Cylinder（C）** | 柱面度數（散光） |
| **Axis** | 柱鏡軸角度 |
| **Prism / PrismX / PrismY** | 稜鏡度 / 水平分量 / 垂直分量 |
| **稜鏡不平衡** | 左右眼稜鏡量相減 |
| **τv** | 可見光透光率（Luminous Transmittance） |
| **τsig** | 訊號透光率（紅/黃/綠交通號誌加權） |
| **UVA / UVB** | 紫外線 A（315–380 nm）/ B（280–315 nm） |
| **ANSI Z80.3 §4.10** | 太陽鏡片訊號透光率加權計算標準 |
| **PCBAFCT** | 印刷電路板功能測試（此處指工廠測試紀錄格式） |
| **D10–D16** | 鏡片配置代碼；本廠僅用平光 `D10`/`D11`/`D13`/`D14`/`D15`（RX 的 D12/D16 不測） |

---

## 15. 已知限制與待確認事項

1. **XML 無獨立鏡片配置代碼** → 依 XML 序號第 6–7 碼 HW ID 判斷：`1F` 用 D15，其餘維持一般平光；`--config` 可手動覆寫，但 `1C`／`1G` 一律拒絕轉檔。其他新 HW ID 的規格需另行確認。
2. **RX Capable（D12 / D16）不支援** → 本廠僅測 Non-RX，工具已移除 D12/D16；如未來要測 RX 需提供處方公差。
3. **鏡片染色均勻度（8.3.1.1）** → 每眼僅一組光譜，無 T01/T02，無法計算；客人同意不測，已移除輸出。
4. **UVB 平均透光率** → 光譜自 315 nm 起，無法量測；客人同意不測，已移除輸出。
5. **錯誤碼格式** → 目前超標統一用 `OutOfSpec`；若要比照 Maera 的錯誤碼/訊息，需提供對照表。
6. **掃描不遞迴** → 多層目錄請分別處理。
7. **τsig 權重固定內嵌** → 若客人更新權重表，需同步更新 `W_H` / `W_I` / `W_J`。
8. **直接轉檔與自動流程的驗證不同** → 自動流程會檢查必要數值、光譜完整性與有限值；獨立使用轉檔 CLI 時，不應將缺資料的 XML 當成有效量測。只有稜鏡空值有當 0 的明確規格依據。

---

## 附錄 A：紅黃綠權重表（380–780 nm）

來源：`Barney3_測試項目對照表_逐項明細.xlsx` → 工作表「紅黃綠權重表」。程式使用 **H（紅·ȳ）**、**I（黃·ȳ）**、**J（綠·ȳ）** 三欄；其餘欄位保留備查。

| λ (nm) | F 黃·x̄ | G 綠·x̄ | H 紅·ȳ | I 黃·ȳ | J 綠·ȳ | K 黃·z̄ | L 綠·z̄ |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 380 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 390 | 0 | 0.0001 | 0 | 0 | 0 | 0 | 0.0004 |
| 400 | 0 | 0.0005 | 0 | 0 | 0 | 0 | 0.0024 |
| 410 | 0 | 0.0024 | 0 | 0 | 0.0001 | 0 | 0.0112 |
| 420 | 0 | 0.0104 | 0 | 0 | 0.0003 | 0 | 0.0498 |
| 430 | 0 | 0.0300 | 0 | 0 | 0.0012 | 0 | 0.1465 |
| 440 | 0 | 0.0482 | 0 | 0 | 0.0032 | 0 | 0.2415 |
| 450 | 0 | 0.0604 | 0 | 0 | 0.0068 | 0 | 0.3184 |
| 460 | 0 | 0.0653 | 0 | 0 | 0.0135 | 0 | 0.3747 |
| 470 | 0 | 0.0526 | 0 | 0 | 0.0245 | 0 | 0.3465 |
| 480 | 0 | 0.0296 | 0 | 0 | 0.0430 | 0 | 0.2514 |
| 490 | 0 | 0.0110 | 0 | 0 | 0.0715 | 0 | 0.1600 |
| 500 | 0 | 0.0018 | 0 | 0 | 0.1199 | 0 | 0.1009 |
| 510 | 0 | 0.0036 | 0 | 0 | 0.1943 | 0 | 0.0611 |
| 520 | 0 | 0.0241 | 0 | 0.0002 | 0.2704 | 0 | 0.0298 |
| 530 | 0.0017 | 0.0587 | 0 | 0.0086 | 0.3057 | 0.0004 | 0.0150 |
| 540 | 0.0721 | 0.0908 | 0 | 0.2370 | 0.2981 | 0.0050 | 0.0063 |
| 550 | 0.2633 | 0.1126 | 0 | 0.6046 | 0.2586 | 0.0053 | 0.0023 |
| 560 | 0.4661 | 0.1210 | 0 | 0.7801 | 0.2026 | 0.0031 | 0.0008 |
| 570 | 0.6747 | 0.1153 | 0 | 0.8428 | 0.1440 | 0.0019 | 0.0003 |
| 580 | 0.8850 | 0.0985 | 0 | 0.8403 | 0.0935 | 0.0016 | 0.0002 |
| 590 | 1.0707 | 0.0750 | 0 | 0.7897 | 0.0553 | 0.0011 | 0.0001 |
| 600 | 1.1843 | 0.0514 | 0.0002 | 0.7035 | 0.0305 | 0.0009 | 0 |
| 610 | 1.1879 | 0.0314 | 0.0254 | 0.5960 | 0.0158 | 0.0004 | 0 |
| 620 | 1.0725 | 0.0162 | 0.2243 | 0.4782 | 0.0072 | 0.0003 | 0 |
| 630 | 0.8517 | 0.0076 | 0.2838 | 0.3513 | 0.0031 | 0 | 0 |
| 640 | 0.6248 | 0.0033 | 0.2201 | 0.2441 | 0.0013 | 0 | 0 |
| 650 | 0.4150 | 0.0013 | 0.1448 | 0.1566 | 0.0005 | 0 | 0 |
| 660 | 0.2521 | 0.0005 | 0.0874 | 0.0933 | 0.0002 | 0 | 0 |
| 670 | 0.1391 | 0.0002 | 0.0482 | 0.0509 | 0.0001 | 0 | 0 |
| 680 | 0.0773 | 0.0001 | 0.0267 | 0.0281 | 0 | 0 | 0 |
| 690 | 0.0388 | 0 | 0.0134 | 0.0140 | 0 | 0 | 0 |
| 700 | 0.0201 | 0 | 0.0069 | 0.0072 | 0 | 0 | 0 |
| 710 | 0.0106 | 0 | 0.0037 | 0.0038 | 0 | 0 | 0 |
| 720 | 0.0054 | 0 | 0.0018 | 0.0019 | 0 | 0 | 0 |
| 730 | 0.0027 | 0 | 0.0009 | 0.0010 | 0 | 0 | 0 |
| 740 | 0.0014 | 0 | 0.0004 | 0.0004 | 0 | 0 | 0 |
| 750 | 0.0006 | 0 | 0.0002 | 0.0002 | 0 | 0 | 0 |
| 760 | 0.0004 | 0 | 0.0002 | 0.0002 | 0 | 0 | 0 |
| 770 | 0.0002 | 0 | 0 | 0 | 0 | 0 | 0 |
| 780 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| **Σ** | **9.3185** | **1.1239** | **1.0884** | 6.8340 | 2.1652 | 0.0200 | 2.1196 |

---

## 附錄 B：程式內部常數與調整方式

| 常數 | 位置 | 用途 |
|---|---|---|
| `PLANO` | 檔頭 | 平光卡控（字串值，整數不補小數） |
| `CONFIG_LIMITS` | 檔頭 | 各配置卡控（Non-RX：`D10`/`D11`/`D13`/`D14`/`D15`） |
| `CONFIG_NOTE` | 檔頭 | 各配置的說明文字 |
| `W_H` / `W_I` / `W_J` | 檔頭 | τsig 光度權重（紅·ȳ / 黃·ȳ / 綠·ȳ） |
| `WAVELENGTHS` | 檔頭 | 權重表波長（380–780，步進 10） |
| `HEADER` | 檔頭 | 21 欄表頭 |

**調整卡控範例**：把 D15 的 τv 下限由 8 改為 10：

```python
'D15': dict(PLANO, tv_low='10', tv_high='40', tv_low_exclusive=False),
```

**新增配置範例**（例如 D20）：在 `CONFIG_LIMITS` / `CONFIG_NOTE` 加入 `'D20'` 項目即可（`--config` 會自動接受）。

---

## 附錄 C：相關檔案與來源文件

| 類別 | 檔案 / 文件 |
|---|---|
| 客戶需求 | 10019804-000 Rev A（A.3 / 2026-09-24） |
| 儀器規格 | 10019712-000 Rev B.2 |
| 儀器手冊 | SOLOS 焦度計使用說明書（中文）rev18 |
| 數據檔標準 | JOIA 標準（含廠商中文標註），如 `*_TOPCON_SOLOS_00000.xml` |
| 卡控來源 | `測試項目對照表.csv` / `測試項目對照表_匯出.xlsx` / `Barney3_測試項目對照表_逐項明細.xlsx` |
| 格式參考 | `Maera_PCBAFCT.csv` |
| 驗證參考 | `SOLOS_客戶測試項目結果_*.csv` |
| 既有原始輸出 | `SOLOS_SpectaclesReport_*.csv`（21 欄，未填卡控） |

### 對照表原始備註（摘要）

1. 本表為「一項目一列、一列一數值」逐項明細版；大項已依「參數 × 配置」拆分。
2. 對應類型：直接對應 / 需計算 / 無對應。
3. UVB（280–315 nm）：客人決定改由原料端管控，不納入儀器量測。
4. τsig：SOLOS 透射率表僅直接顯示 UVA / 藍光 / 可見光三波段，τsig 需由全光譜依 ANSI Z80.3 §4.10 自行計算。
5. 鏡片染色均勻度：每眼僅一組光譜，無 T01/T02 兩點資料，無法計算。
6. 稜鏡不平衡：XML 標註僅列 `nsLM:Prism`；客戶要求水平/垂直 → 對應 `PrismX(base="out")` / `PrismY(base="up")`（本例四欄皆空 = 0）。
7. 章節編號：廠商標「8.1」與客戶文件「8.2」不一致，需確認文件版本。
8. τv 與染色不平衡共用 `VisibleTransmittance` 欄位。
9. 數據檔另有輸出但未列卡控：藍光透光率、柱鏡軸、瞳距、鏡片類型（本例為空）。
10. 來源文件如本附錄。

---

## 附錄 D：SOLOS 自動測試流程（BAT）

`run_solos_test.bat` 以 SOLOS 產出檔為觸發主幹，自動串起 SFIS 在站檢查與測試資料上傳，
並以 screen-diag 顯示目前狀態。

### D.1 流程

1. **監看**：持續檢查本資料夾，等待最新的一組
   `<SN>_<YYYYMMDD>_<HHMMSS>_TOPCON_SOLOS_00000.xml` +
   `<SN>_<YYYYMMDD>_<HHMMSS>_Spectacles_Report.pdf` **完整生成**
  （從首次偵測開始，兩檔大小與修改時間連續 `stable_seconds` 秒不變；預設 5 秒，
  保留舊時間戳的複製檔也必須等待。PDF 頭為 `%PDF` 且尾含 `%%EOF`，XML 必要量測資料齊全）。
   等待期間以 screen-diag 顯示藍底 `waiting for (xml) files...`。
2. **在站檢查**：`python RESTSFIS-diag\RESTSFIS-diag.py /C -sn <SN>`（exit 0 = PASS 才續行）。
3. **轉檔**：`python solos_xml_to_pcbafct.py "<XML>" -o output\<SN>\Lensometer.csv`。
  轉檔程式依 XML 內 SN 第 6–7 碼 HW ID 自動選擇標準：`1F` 使用 D15 太陽鏡片，其餘維持一般平光，`1C`／`1G` 拒絕轉檔；BAT 不需額外參數。
4. **上傳過站**：`python RESTSFIS-diag\RESTSFIS-diag.py /UP -log <Lensometer.csv>`。
5. **封存**：不論上傳成功、SFIS 在站／上傳失敗或轉檔失敗，都將本組 XML、PDF 存入
  `archive\<XML檔名主體>_<唯一代碼>\`。全部複製成功後才移除監看目錄中的 XML/PDF，
  最後登記已處理；量測 FAIL 及離線模式也會保存。只有本輪轉檔成功才附上 CSV 快照，
  不會把同序號的舊 CSV 誤當成本輪結果。封存不代表 SFIS 過站成功，失敗狀態仍記錄於日誌。
  每次使用獨立子資料夾，不覆蓋同名檔；`output\<SN>\Lensometer.csv` 仍保留最新結果。
6. **返回等待**：持續模式在每輪成功、失敗或等待逾時後，都回到等待 XML/PDF。
   結果／錯誤視窗若需確認，按 Enter 或關閉結果清單後繼續。`--once` 則結束並回傳該輪結束碼。

封存失敗時不登記完成，保留尚未移除的來源與已成功複製的備份，日誌記錄錯誤。
本次 BAT 執行期間會暫緩檔案大小、修改時間與檔案身分皆未變動的該組來源，避免立即無限重測；
仍可接收新的 XML/PDF，原組任一檔案改變後也可再次處理。修復封存問題後亦可重新啟動 BAT，
加上 `--use-existing` 重試留存的完整來源。

完整性檢查會確認 XML 的序號、有效量測日期時間、機型／機號、左右眼 Sphere、Cylinder、
UVA、可見光透光率與 PrismX／PrismY 欄位，以及每眼完整的 315–800 nm 光譜
（每 1 nm 一點，數值不得為空、非數字或 NaN／Infinity，波長不可重複）。
依既有規格，存在但空白的稜鏡欄位仍視為 0；缺少欄位則繼續等待。
觀察期間檔案變動或消失會重置該檔計時，內容檢查後會再次確認檔案是否改變。
資料不完整時不進入 SFIS／轉檔／封存，原因寫入日誌，等到完整或 `wait_timeout` 逾時。

BAT 使用同一個 Python 監看程序保留觀察狀態。`--check-once` 只觀察一次，不會跨程序保存狀態；
在穩定秒數大於 0 時，不適合用獨立呼叫的輪詢方式等待完成。`--stable 0` 僅供診斷或測試跳過穩定等待。
這些檢查可降低讀取半成品的風險，但無法保證產檔端稍後不再改寫；嚴格的完成保證仍需產檔端完成標記或交付協定。
更新程式或設定後，請重新啟動 BAT 才會套用。

結果顯示：
- 轉檔 **全部 PASS** → screen-diag 綠底 `PASS`（`-bg 0x008800`）。
- 有 **FAIL** → `UILogResult-auto.exe -log Lensometer.csv /F`。

### D.2 檔案

| 檔案 | 角色 |
|---|---|
| `run_solos_test.bat` | 流程主幹（唯一進入點） |
| `tools\wait_for_solos_pair.py` | 監看 / 配對 / 完整性檢查 / 讀設定（純標準庫） |
| `tools\test_wait_for_solos_pair.py` | 上述工具的單元測試（`python -m unittest discover -s tools`） |
| `solos_test.ini` | 流程設定（TEST / SCREEN） |
| `logs\`、`output\`、`archive\` | 執行日誌、最新 CSV、原始檔與歷史 CSV（已納入 `.gitignore`） |

### D.3 設定（`solos_test.ini`）

`[TEST]`：`watch_dir`、`xml_pattern`、`pdf_suffix`、`poll_seconds`、`stable_seconds`、
`wait_timeout`、`use_existing`、`output_dir`、`archive_dir`、`converter`、`sfis_script`、`sfis_ini`、
`operator`、`dry_run`、`retry`、`once`。
`[SCREEN]`：`enabled`、`screen_dir`（含 `Screen-diag.exe` 與 `UILogResult-auto.exe` 的資料夾）、
`screen_exe`、`uilogresult_exe`、`wait_message`、`pass_message`。
（SFIS 連線參數 `base_url` / `process` / `equipment` 仍在 `RESTSFIS-diag\RESTSFIS-diag.ini`。）

### D.4 用法

```bat
run_solos_test.bat                 :: 持續執行（產線）
run_solos_test.bat --once          :: 只跑一輪後結束
run_solos_test.bat --dry-run       :: 呼叫 RESTSFIS-diag 時加上 --dry-run（不發網路）
run_solos_test.bat --use-existing  :: 採用資料夾中既有的 XML+PDF（離線驗證用）
run_solos_test.bat --no-screen     :: 不啟動 screen-diag（無畫面 / 自動化）
run_solos_test.bat --no-sfis       :: 完全略過 SFIS 在站檢查與上傳
run_solos_test.bat --dir D:\SOLOS   :: 覆寫本次監看資料夾
run_solos_test.bat /?              :: 說明
```

離線驗證（不會發送任何 SFIS 請求）：

```bat
run_solos_test.bat --once --dry-run --use-existing --no-screen
```

完全不執行 SFIS 腳本：

```bat
run_solos_test.bat --once --no-sfis --use-existing --no-screen
```

兩種離線模式都會實際轉檔並封存來源，不是唯讀預覽。`--once` 只處理一組；
省略則持續等待與逐組處理，即使執行期間發生錯誤或等待逾時也不結束。
啟動前缺少 Python、腳本或設定等前置檢查失敗仍會停止，需先修復。
預設啟動時忽略既有 XML，需加 `--use-existing` 才處理既有檔。
`wait_timeout` 為每輪等待秒數，逾時後重新開始等待。封存後重跑請先把要重測的 XML/PDF **複製**到監看目錄，
不要移走唯一的封存備份。序號依 XML 的 `Patient/ID`，不是檔名前綴；同序號歷史結果請查封存目錄。

### D.5 screen-diag 狀態

沿用 ML_Robocal 的 screen-diag 慣例：`Screen-diag.exe -nl [-enter] /SS <字級> "<訊息> 0xFFFFFF -bg 0xRRGGBB`，
關閉用 `taskkill /IM Screen-diag.exe`。

| 狀態 | 顯示 | 指令 |
|---|---|---|
| 等待 XML+PDF | 藍底 `waiting for (xml) files...` | `Screen-diag.exe -nl /SS 55 "..." 0xFFFFFF -bg 0x223366` |
| 全 PASS | 綠底 `PASS` | `Screen-diag.exe -nl -enter /SS 200 "PASS" 0xFFFFFF -bg 0x008800` |
| 有 FAIL | 結果清單 | `UILogResult-auto.exe -log Lensometer.csv /F` |

### D.6 結束碼

下表為 `--once` 的回傳碼；持續模式將每輪狀態寫入日誌並重新等待，前置檢查失敗除外。
若同時發生 SFIS／轉檔與封存失敗，回傳 71，原失敗原因仍保留於日誌。

| 碼 | 意義 |
|---|---|
| 0 | 成功（或 `--once` 完成一輪） |
| 90 | 前置檢查失敗（缺 python / 腳本 / 設定） |
| 91 / 92 / 93 | 無法讀取序號 / 設定錯誤 / 等待逾時 |
| 80 / 81 / 83 | `/C` 路線被拒 / HTTP 失敗 / 用法錯誤 |
| 70 | 轉檔失敗 |
| 71 | 封存失敗（檢查日誌；已複製的資料不會因來源移除失敗而刪除） |
| 72 | 序號 HW ID 檢查失敗（1C／1G 禁止測試；不轉檔、不呼叫 SFIS） |
| 61 / 63 | `/UP` HTTP 失敗 / 用法錯誤 |

### D.7 前置條件

- **Python 3.8+** 在 PATH。
- `RESTSFIS-diag\RESTSFIS-diag.ini` 的 `base_url` / `process` / `equipment` 需為
  SFIS 團隊確認之正式值（`equipment` 留空時使用本機主機名）。
- `solos_test.ini` 的 `[SCREEN] screen_dir` 需指向含 `Screen-diag.exe` 與
  `UILogResult-auto.exe` 的資料夾（預設為 ML_Robocal 的 Tools 目錄）。

---

*文件涵蓋：`solos_xml_to_pcbafct.py` 全部功能、參數、輸入/輸出格式、卡控、計算、判定、驗證與來源；
以及 `run_solos_test.bat` 自動測試流程（附錄 D）。*
