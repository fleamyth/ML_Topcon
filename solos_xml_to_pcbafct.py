#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
solos_xml_to_pcbafct.py

將 TOPCON SOLOS (JOIA / nsLM) 量測 XML 轉成 PCBAFCT.csv（21 欄工廠測試紀錄格式），
並依「測試項目對照表」填入卡控 (LowerLimit / UpperLimit) 與判定 (ErrorCode / ErrorDescription)。

輸出欄位（與 Maera_PCBAFCT.csv 相同）：
    TSRID,SerialNumber,PartNumber,Operation,TesterName,TestName,TestVariation,
    ErrorCode,Operator,StartDateTime,EndDateTime,ProductionTest,TestSuiteName,
    ErrorDescription,MeasurementName,Value,Units,LowerLimit,UpperLimit,PegaCode,TicketID

--------------------------------------------------------------------------
卡控來源（「測試項目對照表」）
--------------------------------------------------------------------------
預設採用 D10 / D11 / D13 / D14「平光 (Plano)」配置：

    8.2.1 屈光度 Sphere            : -0.13 <= x <= 0.13  D
    8.2.2 散光   Cylinder          : x <= 0.13          D
    8.2.3 稜鏡不平衡 H (左右眼相減) : x <= 0.50          Δ
    8.2.3 稜鏡不平衡 V (左右眼相減) : x <= 0.25          Δ
    8.3.1.1 鏡片染色均勻度          : 客人同意不測，不輸出
    8.3.1.2 鏡片染色不平衡          : x <= 15            %
    8.3.2.1 可見光透光率 τv         : x > 40             %
    8.3.2.1 訊號透光率 τsig 紅      : x >= 8             %
    8.3.2.1 訊號透光率 τsig 黃      : x >= 6             %
    8.3.2.1 訊號透光率 τsig 綠      : x >= 6             %
    8.3.2.1 UVB 平均透光率          : 客人同意不測，不輸出
    8.3.2.1 UVA 平均透光率          : x <= 0.5 · τv      %

D15 (Plano, Sunlens) 之 τv 卡控為 8 % ~ 40 %（其餘同平光）。

(註 1) 本廠僅測試 Non-RX（平光）眼鏡。工具只支援平光配置
       D10 / D11 / D13 / D14 / D15；RX Capable（D12 / D16）已移除。
(註 2) XML 內沒有鏡片配置代碼。未指定 --config 時，依 XML 序號判斷：
    SAA671F 開頭使用 D15 太陽鏡片卡控，其餘維持 D10~D14 平光卡控。
    手動指定 --config 優先，配置會顯示於輸出訊息與 --with-note 備註欄。
    序號第 6–7 碼 HW ID 為 1C / 1G 時拒絕轉檔，--config 不可覆寫。

--------------------------------------------------------------------------
τsig（紅 / 黃 / 綠 訊號透光率）計算
--------------------------------------------------------------------------
依 ANSI Z80.3 §4.10 加權函數，權重取自「紅黃綠權重表」：

    τsig = Σ τ(λ)·W(λ) / Σ W(λ)      λ = 380..780 nm（步進 10 nm）

    紅色訊號 → 權重 = SA(λ)·τ_red(λ)·ȳ(λ)   （權重表 H 欄）
    黃色訊號 → 權重 = SA(λ)·τ_yellow(λ)·x̄(λ)（權重表 F 欄）
    綠色訊號 → 權重 = SA(λ)·τ_green(λ)·x̄(λ) （權重表 G 欄）

τ(λ) 直接取 XML <TransmittanceData> 於該波長的量測值。

--------------------------------------------------------------------------
用法
--------------------------------------------------------------------------
    # 單檔（實際檔名格式：<序號>_<YYYYMMDD>_<HHMMSS>_TOPCON_SOLOS_00000.xml）
    python solos_xml_to_pcbafct.py "SAA671D0000H_20261019_085916_TOPCON_SOLOS_00000.xml"

    # 批次（資料夾內所有 .xml）
    python solos_xml_to_pcbafct.py ./

    # 指定輸出檔 / 輸出資料夾、指定配置
    python solos_xml_to_pcbafct.py in.xml -o out.csv --config D15
    python solos_xml_to_pcbafct.py ./xmls -o ./csvs

預設輸出檔名：Lensometer.csv（寫在輸入檔同一資料夾；
批次多檔時，每個 XML 會放進各自的「序號」子資料夾，避免同名覆蓋）。
輸出編碼 UTF-8 with BOM、換行 CRLF（與既有 CSV 一致）。
"""

import argparse
import csv
import os
import sys
import glob
import xml.etree.ElementTree as ET
from datetime import datetime

# ===========================================================================
# 內嵌權重表（來源：Barney3_測試項目對照表_逐項明細.xlsx / 工作表「紅黃綠權重表」）
#   WAVELENGTHS : 波長 (nm)，380~780，步進 10
#   W_F : SA(λ)·τsig(λ)·x̄(λ)  黃色訊號
#   W_G : SA(λ)·τsig(λ)·x̄(λ)  綠色訊號
#   W_H : SA(λ)·τsig(λ)·ȳ(λ)  紅色訊號
#   W_I / W_J / W_K / W_L : 權重表其餘欄位（備查用，目前未參與判定）
# ===========================================================================
WAVELENGTHS = [380, 390, 400, 410, 420, 430, 440, 450, 460, 470, 480, 490, 500,
               510, 520, 530, 540, 550, 560, 570, 580, 590, 600, 610, 620, 630,
               640, 650, 660, 670, 680, 690, 700, 710, 720, 730, 740, 750, 760,
               770, 780]
W_F = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.0017, 0.0721, 0.2633,
       0.4661, 0.6747, 0.885, 1.0707, 1.1843, 1.1879, 1.0725, 0.8517, 0.6248,
       0.415, 0.2521, 0.1391, 0.0773, 0.0388, 0.0201, 0.0106, 0.0054, 0.0027,
       0.0014, 0.0006, 0.0004, 0.0002, 0]
W_G = [0, 0.0001, 0.0005, 0.0024, 0.0104, 0.03, 0.0482, 0.0604, 0.0653,
       0.0526, 0.0296, 0.011, 0.0018, 0.0036, 0.0241, 0.0587, 0.0908, 0.1126,
       0.121, 0.1153, 0.0985, 0.075, 0.0514, 0.0314, 0.0162, 0.0076, 0.0033,
       0.0013, 0.0005, 0.0002, 0.0001, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
W_H = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
       0.0002, 0.0254, 0.2243, 0.2838, 0.2201, 0.1448, 0.0874, 0.0482, 0.0267,
       0.0134, 0.0069, 0.0037, 0.0018, 0.0009, 0.0004, 0.0002, 0.0002, 0, 0]
W_I = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.0002, 0.0086, 0.237,
       0.6046, 0.7801, 0.8428, 0.8403, 0.7897, 0.7035, 0.596, 0.4782, 0.3513,
       0.2441, 0.1566, 0.0933, 0.0509, 0.0281, 0.014, 0.0072, 0.0038, 0.0019,
       0.001, 0.0004, 0.0002, 0.0002, 0, 0]
W_J = [0, 0, 0, 0.0001, 0.0003, 0.0012, 0.0032, 0.0068, 0.0135, 0.0245,
       0.043, 0.0715, 0.1199, 0.1943, 0.2704, 0.3057, 0.2981, 0.2586, 0.2026,
       0.144, 0.0935, 0.0553, 0.0305, 0.0158, 0.0072, 0.0031, 0.0013, 0.0005,
       0.0002, 0.0001, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
W_K = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.0004, 0.005, 0.0053,
       0.0031, 0.0019, 0.0016, 0.0011, 0.0009, 0.0004, 0.0003, 0, 0, 0, 0, 0,
       0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
W_L = [0, 0.0004, 0.0024, 0.0112, 0.0498, 0.1465, 0.2415, 0.3184, 0.3747,
       0.3465, 0.2514, 0.16, 0.1009, 0.0611, 0.0298, 0.015, 0.0063, 0.0023,
       0.0008, 0.0003, 0.0002, 0.0001, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
       0, 0, 0, 0, 0, 0]

HEADER = [
    'TSRID', 'SerialNumber', 'PartNumber', 'Operation', 'TesterName', 'TestName',
    'TestVariation', 'ErrorCode', 'Operator', 'StartDateTime', 'EndDateTime',
    'ProductionTest', 'TestSuiteName', 'ErrorDescription', 'MeasurementName',
    'Value', 'Units', 'LowerLimit', 'UpperLimit', 'PegaCode', 'TicketID',
]

# ---------------------------------------------------------------------------
# 各配置之卡控（'' 代表不設限）
#   sphere_low / sphere_high : 屈光度 D
#   cyl_high                 : 散光 D
#   prism_h_high / prism_v_high : 稜鏡不平衡 Δ
#   tint_imbal_high          : 鏡片染色不平衡 %
#   tv_low / tv_high         : 可見光透光率 τv %
#   sig_r_low/sig_y_low/sig_g_low : 訊號透光率 %
#   uv_factor                : UVA 上限 = uv_factor · τv
# ---------------------------------------------------------------------------
# 卡控一律以「字串」保存，數值呈現即與「測試項目對照表」一致（整數不補小數）。
PLANO = dict(sphere_low='-0.13', sphere_high='0.13', cyl_high='0.13',
             prism_h_high='0.50', prism_v_high='0.25', tint_imbal_high='15',
             tv_low='40', tv_high='', sig_r_low='8', sig_y_low='6',
             sig_g_low='6', uv_factor=0.5)

# 本廠僅測試 Non-RX（平光）眼鏡，故只保留平光相關配置：
#   D10 / D11 / D13 / D14 = 平光（透明 / 染色），卡控相同
#   D15                   = 平光太陽鏡片，僅 τv 卡控不同（8%~40%）
# 已移除 RX Capable（D12 / D16），因為不測 RX。
CONFIG_LIMITS = {
    'D10': dict(PLANO),                                   # Plano, Clear
    'D11': dict(PLANO),                                   # Plano, Tinted
    'D13': dict(PLANO),                                   # Plano, Tinted
    'D14': dict(PLANO),                                   # Plano, Clear
    'D15': dict(PLANO, tv_low='8', tv_high='40'),         # Plano, Sunlens: 8%~40%
}

CONFIG_NOTE = {
    'D10': 'D10 Plano, Clear (平光透明)',
    'D11': 'D11 Plano, Tinted (平光染色)',
    'D13': 'D13 Plano, Tinted (平光染色)',
    'D14': 'D14 Plano, Clear (平光透明)',
    'D15': 'D15 Plano, Sunlens (平光太陽鏡片, τv 8-40%)',
}

# 輸出檔名（固定）。可用 -o 覆寫；批次多檔時會放在各自的「序號」子資料夾下。
OUTPUT_NAME = 'Lensometer.csv'


# ===========================================================================
# 工具函式
# ===========================================================================
def local(tag):
    """去掉 XML namespace，取標籤本名。"""
    return tag.rsplit('}', 1)[-1]


def find_child(elem, name):
    if elem is None:
        return None
    for c in elem:
        if local(c.tag) == name:
            return c
    return None


def child_text(elem, name):
    c = find_child(elem, name)
    return None if c is None else c.text


def to_float(text):
    if text is None:
        return None
    text = text.strip()
    if text == '':
        return None
    try:
        return float(text)
    except ValueError:
        return None


def fnum(text):
    """轉 float；空值 / 無值 => 0.0（對照表：無數值代表量測值為 0）。"""
    v = to_float(text)
    return 0.0 if v is None else v


def fmt3(x):
    return '%.3f' % x


def fmt2(x):
    return '%.2f' % x


def fmt1(x):
    return '%.1f' % x


def weighted_transmittance(spectrum, weights):
    """τsig = Σ τ(λ)·W(λ) / Σ W(λ)，於權重表波長上取 XML 量測值。"""
    num = 0.0
    den = 0.0
    for lam, w in zip(WAVELENGTHS, weights):
        if not w:
            continue
        den += w
        t = spectrum.get(lam)
        if t is not None:
            num += t * w
    return (num / den) if den else None


# ===========================================================================
# XML 解析
# ===========================================================================
def validate_serial_number(serial):
    hw_id = serial[5:7]
    if hw_id in ('1C', '1G'):
        raise ValueError('SN=%s: unsupported HW ID %s' % (serial, hw_id))


def parse_solos_xml(path):
    root = ET.parse(path).getroot()

    common = find_child(root, 'Common')
    company = child_text(common, 'Company')
    model = child_text(common, 'ModelName')
    machine = child_text(common, 'MachineNo')
    date = child_text(common, 'Date')
    time = child_text(common, 'Time')
    patient = find_child(common, 'Patient')
    serial = child_text(patient, 'ID')
    validate_serial_number(serial or '')

    measure = find_child(root, 'Measure')
    lm = find_child(measure, 'LM')
    eyes = {}
    for eye in ('R', 'L'):
        el = find_child(lm, eye)
        d = {'spectrum': {}}
        if el is not None:
            for c in el:
                n = local(c.tag)
                if n == 'TransmittanceData':
                    sp = {}
                    for tv in c:
                        wl = to_float(child_text(tv, 'WaveLength'))
                        tr = to_float(child_text(tv, 'Transmittance'))
                        if wl is not None and tr is not None:
                            sp[int(wl)] = tr
                    d['spectrum'] = sp
                else:
                    d[n] = c.text
        eyes[eye] = d

    pd = find_child(measure, 'PD')
    pd_vals = {
        'Distance': child_text(pd, 'Distance'),
        'DistanceR': child_text(pd, 'DistanceR'),
        'DistanceL': child_text(pd, 'DistanceL'),
    }

    return {
        'company': company, 'model': model or '', 'machine': machine or '',
        'date': date, 'time': time, 'serial': serial or '',
        'eyes': eyes, 'pd': pd_vals,
        'path': path,
    }


# ===========================================================================
# 建立輸出資料列
# ===========================================================================
def make_row(base, test_name, measurement, value, units, low, high,
             applicable=True, judge_abs=False, note=''):
    err_code, err_desc = '0', 'Success'
    if not applicable:
        value = ''
        err_code, err_desc = '', 'NotApplicable'
    elif value not in (None, ''):
        v = to_float(value)
        ok = True
        if v is not None:
            lo = to_float(low)
            hi = to_float(high)
            if judge_abs:
                # 卡控針對「大小」（如散光 |Cylinder| <= 0.13）
                v = abs(v)
                lo = None
            if lo is not None and v < lo:
                ok = False
            if hi is not None and v > hi:
                ok = False
        if not ok:
            err_code, err_desc = 'NG', 'OutOfSpec'
    row = dict(base)
    row.update({
        'TestName': test_name,
        'MeasurementName': measurement,
        'Value': '' if value is None else value,
        'Units': units,
        'LowerLimit': low,
        'UpperLimit': high,
        'ErrorCode': err_code,
        'ErrorDescription': err_desc,
    })
    if note:
        row['備註'] = note
    return row


def build_rows(xml, limits, config_note, with_note):
    model = xml['model']
    serial = xml['serial']
    r, l = xml['eyes']['R'], xml['eyes']['L']

    # TSRID / 時間
    dt = None
    if xml['date'] and xml['time']:
        try:
            dt = datetime.strptime('%s %s' % (xml['date'], xml['time']),
                                   '%Y-%m-%d %H:%M:%S')
        except ValueError:
            dt = None
    tsrid = dt.strftime('%Y%m%d%H%M%S') if dt else ''
    sdt = dt.strftime('%m/%d/%Y %I:%M:%S %p') if dt else ''

    base = {
        'TSRID': tsrid,
        'SerialNumber': serial,
        'PartNumber': '',
        'Operation': 'SpectaclesReport',
        'TesterName': ('%s-%s' % (model, xml['machine'])).strip('-'),
        'TestVariation': model,
        'ErrorCode': '0',
        'Operator': '',
        'StartDateTime': sdt,
        'EndDateTime': sdt,
        'ProductionTest': '0',
        'TestSuiteName': None,   # 於 main 覆寫
        'ErrorDescription': 'Success',
        'PegaCode': '',
        'TicketID': 'Debug',
    }

    # ---- 直接量測值 ----
    sphere_r, sphere_l = r.get('Sphere'), l.get('Sphere')
    cyl_r, cyl_l = r.get('Cylinder'), l.get('Cylinder')
    uv_r, uv_l = r.get('UVTransmittance'), l.get('UVTransmittance')
    tv_r = to_float(r.get('VisibleTransmittance'))
    tv_l = to_float(l.get('VisibleTransmittance'))

    # ---- 需計算值 ----
    prism_h = abs(fnum(r.get('PrismX')) - fnum(l.get('PrismX')))
    prism_v = abs(fnum(r.get('PrismY')) - fnum(l.get('PrismY')))
    if tv_r is not None and tv_l is not None and (tv_r + tv_l) != 0:
        tint_imbal = abs(tv_r - tv_l) / ((tv_r + tv_l) / 2.0) * 100.0
    else:
        tint_imbal = None
    sig_rr = weighted_transmittance(r['spectrum'], W_H)
    sig_lr = weighted_transmittance(l['spectrum'], W_H)
    sig_ry = weighted_transmittance(r['spectrum'], W_F)
    sig_ly = weighted_transmittance(l['spectrum'], W_F)
    sig_rg = weighted_transmittance(r['spectrum'], W_G)
    sig_lg = weighted_transmittance(l['spectrum'], W_G)

    uv_lim_r = fmt2(limits['uv_factor'] * tv_r) if tv_r is not None else ''
    uv_lim_l = fmt2(limits['uv_factor'] * tv_l) if tv_l is not None else ''

    def cfg_note(txt):
        return txt if with_note else ''

    rows = []
    a = lambda *args, **kw: rows.append(
        make_row(base, *args, note=cfg_note(kw.pop('n', '')), **kw))

    # 8.2.1 屈光度
    a('Sphere', 'R_Sphere', sphere_r, 'D', limits['sphere_low'],
      limits['sphere_high'], n=config_note)
    a('Sphere', 'L_Sphere', sphere_l, 'D', limits['sphere_low'],
      limits['sphere_high'], n=config_note)
    # 8.2.2 散光（卡控針對大小 |Cylinder| <= 0.13）
    a('Cylinder', 'R_Cylinder', cyl_r, 'D', '',
      limits['cyl_high'], judge_abs=True, n=config_note)
    a('Cylinder', 'L_Cylinder', cyl_l, 'D', '',
      limits['cyl_high'], judge_abs=True, n=config_note)
    # 8.2.3 稜鏡不平衡
    a('Prism Imbalance', 'Prism_Imbalance_H', fmt3(prism_h), 'Δ', '',
      limits['prism_h_high'], n='左右眼相減; ' + config_note)
    a('Prism Imbalance', 'Prism_Imbalance_V', fmt3(prism_v), 'Δ', '',
      limits['prism_v_high'], n='左右眼相減; ' + config_note)
    # 8.3.1.2 鏡片染色不平衡
    a('Lens Tint Imbalance', 'Tint_Imbalance',
      fmt2(tint_imbal) if tint_imbal is not None else '', '%', '',
      limits['tint_imbal_high'], n='|τv_R-τv_L|/平均; ' + config_note)
    # 8.3.2.1 可見光透光率 τv
    a('Luminous Transmittance', 'R_tau_v',
      r.get('VisibleTransmittance'), '%', limits['tv_low'], limits['tv_high'],
      n=config_note)
    a('Luminous Transmittance', 'L_tau_v',
      l.get('VisibleTransmittance'), '%', limits['tv_low'], limits['tv_high'],
      n=config_note)
    # 8.3.2.1 訊號透光率 τsig
    a('Signal Transmittance', 'R_tau_sig_r',
      fmt1(sig_rr) if sig_rr is not None else '', '%',
      limits['sig_r_low'], '', n='ANSI Z80.3 §4.10 加權')
    a('Signal Transmittance', 'L_tau_sig_r',
      fmt1(sig_lr) if sig_lr is not None else '', '%',
      limits['sig_r_low'], '', n='ANSI Z80.3 §4.10 加權')
    a('Signal Transmittance', 'R_tau_sig_y',
      fmt1(sig_ry) if sig_ry is not None else '', '%',
      limits['sig_y_low'], '', n='ANSI Z80.3 §4.10 加權')
    a('Signal Transmittance', 'L_tau_sig_y',
      fmt1(sig_ly) if sig_ly is not None else '', '%',
      limits['sig_y_low'], '', n='ANSI Z80.3 §4.10 加權')
    a('Signal Transmittance', 'R_tau_sig_g',
      fmt1(sig_rg) if sig_rg is not None else '', '%',
      limits['sig_g_low'], '', n='ANSI Z80.3 §4.10 加權')
    a('Signal Transmittance', 'L_tau_sig_g',
      fmt1(sig_lg) if sig_lg is not None else '', '%',
      limits['sig_g_low'], '', n='ANSI Z80.3 §4.10 加權')
    # 8.3.2.1 UVA（上限 = 0.5·τv）
    a('UVA Mean Transmittance', 'R_UVA_Mean', uv_r, '%', '', uv_lim_r,
      n='上限=0.5·τv; ' + config_note)
    a('UVA Mean Transmittance', 'L_UVA_Mean', uv_l, '%', '', uv_lim_l,
      n='上限=0.5·τv; ' + config_note)

    return rows


def build_extra_rows(xml, with_note):
    """對照表備註 9：數據檔另有輸出但客戶未列為卡控（Axis / Blue / PD）。"""
    r, l = xml['eyes']['R'], xml['eyes']['L']
    base = {
        'TSRID': '', 'SerialNumber': xml['serial'], 'PartNumber': '',
        'Operation': 'SpectaclesReport',
        'TesterName': ('%s-%s' % (xml['model'], xml['machine'])).strip('-'),
        'TestVariation': xml['model'], 'ErrorCode': '0', 'Operator': '',
        'StartDateTime': '', 'EndDateTime': '', 'ProductionTest': '0',
        'TestSuiteName': None, 'ErrorDescription': 'Success', 'PegaCode': '',
        'TicketID': 'Debug',
    }
    rows = []
    for eye, d in (('Right', r), ('Left', l)):
        rows.append(make_row(base, 'Axis', '%s_Axis' % eye, d.get('Axis'),
                             'deg', '', '', note='未列卡控' if with_note else ''))
        rows.append(make_row(base, 'BlueTransmittance', '%s_BlueTransmittance' % eye,
                             d.get('BlueTransmittance'), '%', '', '',
                             note='未列卡控' if with_note else ''))
    pd = xml['pd']
    for name, key in (('Distance', 'Distance'), ('DistanceR', 'DistanceR'),
                      ('DistanceL', 'DistanceL')):
        rows.append(make_row(base, name, 'PD_%s' % key, pd.get(key), 'mm', '',
                             '', note='未列卡控' if with_note else ''))
    return rows


# ===========================================================================
# 輸出
# ===========================================================================
def write_csv(path, rows, with_note):
    header = list(HEADER)
    if with_note:
        header.append('備註')
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=header, lineterminator='\r\n',
                           extrasaction='ignore')
        w.writeheader()
        for row in rows:
            w.writerow(row)


def summarize(rows):
    ok = ng = na = 0
    for row in rows:
        if row['ErrorDescription'] == 'NotApplicable':
            na += 1
        elif row['ErrorCode'] == 'NG':
            ng += 1
        else:
            ok += 1
    return ok, ng, na


# ===========================================================================
# 主程式
# ===========================================================================
def collect_inputs(paths):
    files = []
    for p in paths:
        if os.path.isdir(p):
            files.extend(sorted(glob.glob(os.path.join(p, '*.xml'))))
            files.extend(sorted(glob.glob(os.path.join(p, '*.XML'))))
        else:
            files.append(p)
    # 去重、保留順序
    seen = set()
    uniq = []
    for f in files:
        a = os.path.abspath(f)
        if a not in seen:
            seen.add(a)
            uniq.append(f)
    return uniq


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='SOLOS (JOIA nsLM) XML -> PCBAFCT.csv (21 欄)，卡控依「測試項目對照表」。')
    ap.add_argument('inputs', nargs='*', help='XML 檔或包含 XML 的資料夾')
    ap.add_argument('--check-serial', metavar='SN',
                    help='僅檢查序號 HW ID；1C / 1G 回傳 1，不轉檔')
    ap.add_argument('-o', '--output', default=None,
                    help='輸出檔（單檔）或輸出資料夾（多檔）')
    ap.add_argument('--config', default=None, choices=sorted(CONFIG_LIMITS.keys()),
                    help='鏡片配置（僅 Non-RX 平光）：D10/D11/D13/D14 或 D15 太陽鏡片；'
                        '未指定時 XML 序號 SAA671F 開頭使用 D15，其餘用一般平光卡控')
    ap.add_argument('--suite', default='SOLOS_SpectaclesReport_1.3.0',
                    help='TestSuiteName 欄位值')
    ap.add_argument('--ticket', default='Debug', help='TicketID 欄位值')
    ap.add_argument('--with-note', action='store_true',
                    help='額外輸出「備註」欄（第 22 欄）')
    ap.add_argument('--include-extra', action='store_true',
                    help='額外輸出未列卡控之欄位 (Axis / BlueTransmittance / PD)')
    args = ap.parse_args(argv)
    if args.check_serial is not None:
        if args.inputs:
            ap.error('--check-serial cannot be combined with XML inputs')
        try:
            validate_serial_number(args.check_serial)
        except ValueError as exc:
            print('[FAIL] %s' % exc, file=sys.stderr)
            return 1
        return 0
    if not args.inputs:
        ap.error('XML inputs are required unless --check-serial is used')

    operator_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'OP.dat')
    try:
        with open(operator_path, encoding='utf-8-sig') as operator_file:
            operator = operator_file.read().strip()
        if not operator or len(operator.splitlines()) != 1:
            raise ValueError('OP.dat must contain one non-empty operator ID')
    except FileNotFoundError:
        operator = ''
    except (OSError, UnicodeError, ValueError) as exc:
        print('[FAIL] Cannot read operator file %s: %s' % (operator_path, exc),
              file=sys.stderr)
        return 1

    files = collect_inputs(args.inputs)
    if not files:
        print('找不到任何 XML 檔。', file=sys.stderr)
        return 2

    multi = len(files) > 1
    out_is_dir = bool(args.output) and (multi or os.path.isdir(args.output)
                                        or args.output.endswith(('/', '\\')))

    print('共 %d 個 XML 檔。' % len(files))
    print('-' * 72)

    rc = 0
    for f in files:
        try:
            xml = parse_solos_xml(f)
        except Exception as exc:  # noqa: BLE001
            print('[FAIL] 解析失敗 %s : %s' % (f, exc), file=sys.stderr)
            rc = 1
            continue

        selected_config = args.config
        if selected_config is None and xml['serial'].startswith('SAA671F'):
            selected_config = 'D15'
        limits = CONFIG_LIMITS.get(selected_config, PLANO)
        config_note = (CONFIG_NOTE[selected_config] if selected_config
                       else 'Config 未提供; 採 D10-D14 平光卡控')
        print('SN=%s 卡控配置：%s' % (xml['serial'], config_note))

        rows = build_rows(xml, limits, config_note, args.with_note)
        if args.include_extra:
            rows.extend(build_extra_rows(xml, args.with_note))
        for row in rows:
            row['TestSuiteName'] = args.suite
            row['TicketID'] = args.ticket
            row['Operator'] = operator

        # 決定輸出路徑：輸出檔名固定為 OUTPUT_NAME（Lensometer.csv）
        if args.output and not out_is_dir:
            outpath = args.output                       # 指定為檔案
        else:
            base = args.output if args.output else os.path.dirname(os.path.abspath(f))
            if multi:
                # 多檔時放進「序號」子資料夾，避免同名覆蓋
                outpath = os.path.join(base, xml['serial'] or 'output', OUTPUT_NAME)
            else:
                outpath = os.path.join(base, OUTPUT_NAME)

        os.makedirs(os.path.dirname(os.path.abspath(outpath)), exist_ok=True)
        write_csv(outpath, rows, args.with_note)
        ok, ng, na = summarize(rows)
        print('[OK] %s  ->  %s   (PASS=%d, FAIL=%d, N/A=%d)'
              % (os.path.basename(f), os.path.relpath(outpath), ok, ng, na))

    return rc


if __name__ == '__main__':
    sys.exit(main())
