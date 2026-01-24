pip install pdfplumber pandas
import pdfplumber
import pandas as pd
import re
from datetime import datetime
import os

# 定義要提取的化學物質關鍵字映射 (根據報告常見寫法)
# 格式: '標準欄位名': ['報告中可能的名稱1', '報告中可能的名稱2']
KEYWORDS_MAP = {
    'Pb': ['Lead', 'Pb'],
    'Cd': ['Cadmium', 'Cd'],
    'Hg': ['Mercury', 'Hg'],
    'Cr6+': ['Hexavalent Chromium', 'Cr(VI)', 'Cr6+'],
    'PBB': ['PBBs', 'Polybrominated biphenyls', 'Sum of PBBs'],
    'PBDE': ['PBDEs', 'Polybrominated diphenyl ethers', 'Sum of PBDEs'],
    'DEHP': ['DEHP', 'Bis(2-ethylhexyl) phthalate'],
    'DBP':  ['DBP', 'Dibutyl phthalate'],
    'BBP':  ['BBP', 'Butyl benzyl phthalate'],
    'DIBP': ['DIBP', 'Diisobutyl phthalate'],
    'F':    ['Fluorine', 'F', 'Halogen-Fluorine'],
    'CL':   ['Chlorine', 'Cl', 'Halogen-Chlorine'],
    'BR':   ['Bromine', 'Br', 'Halogen-Bromine'],
    'PFOS': ['Perfluorooctane sulfonates', 'PFOS'],
}

def parse_date(date_str):
    """將不同格式的日期統一轉換為 YYYY/MM/DD"""
    if not date_str:
        return None
    
    # 處理常見格式
    formats = [
        "%b %d, %Y",      # Feb 27, 2025 (SGS) [1]
        "%Y.%m.%d",       # 2025.06.16 (CTI)
        "%Y/%m/%d",
        "%d-%b-%Y"        # 27-Feb-2025 [2]
    ]
    
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            return dt
        except ValueError:
            continue
    return None

def extract_pdf_data(file_path):
    """從單個 PDF 提取數據"""
    data = {key: "N.D." for key in KEYWORDS_MAP.keys()} # 預設為 N.D.
    data['PFAS'] = "" # 預設空白
    data['DATE'] = None
    data['DATE_RAW'] = ""
    
    filename = os.path.basename(file_path)
    
    with pdfplumber.open(file_path) as pdf:
        full_text = ""
        
        # 1. 遍歷每一頁提取文字與表格
        for page in pdf.pages:
            text = page.extract_text()
            full_text += text + "\n"
            
            # --- 提取日期 (通常在第一頁或頁眉) ---
            if not data['DATE']:
                # 針對 SGS: Date: Feb 27, 2025 [1]
                date_match_sgs = re.search(r"Date:\s*([A-Z][a-z]{2}\s\d{1,2},\s\d{4})", text)
                # 針對 CTI: Date: 2025.03.05 [3] 或 27-Feb-2025 [2]
                date_match_cti = re.search(r"Date:\s*(\d{4}\.\d{2}\.\d{2})", text)
                date_match_cti_2 = re.search(r"Date:\s*(\d{1,2}-[A-Z][a-z]{2}-\d{4})", text)

                if date_match_sgs:
                    data['DATE'] = parse_date(date_match_sgs.group(1))
                elif date_match_cti:
                    data['DATE'] = parse_date(date_match_cti.group(1))
                elif date_match_cti_2:
                    data['DATE'] = parse_date(date_match_cti_2.group(1))

            # --- 提取化學物質數值 ---
            # 策略：逐行掃描或使用 pdfplumber 的 table 提取功能
            # 這裡使用簡化的行掃描邏輯，尋找 物質名稱 後面跟著的 "ND" 或 數字
            lines = text.split('\n')
            for line in lines:
                for key, keywords in KEYWORDS_MAP.items():
                    # 只有當該項目目前還是 N.D. 時才去尋找 (避免覆蓋)
                    # 或是如果找到具體數值，覆蓋掉 N.D.
                    for keyword in keywords:
                        # 簡單的正則表達式：匹配關鍵字，後續跟著 ND 或 數字
                        # 注意：需排除單位 mg/kg 等干擾
                        if keyword in line:
                            # 尋找行內的數字或 N.D.
                            # 排除掉類似 "ISO", "IEC" 之後的數字
                            val_match = re.search(r"(N\.D\.|ND|\<.*?|\d+(?:\.\d+)?)", line.split(keyword)[-1])
                            if val_match:
                                val = val_match.group(1)
                                if "N.D" in val or "ND" in val:
                                    continue # 保持預設，或者如果已經有數字則不覆蓋
                                elif re.match(r"^\d", val): # 如果是數字
                                    try:
                                        # 簡單過濾：如果是年份或法規編號忽略
                                        if float(val) > 2000 and "20" in val: pass 
                                        else: data[key] = float(val)
                                    except:
                                        pass

        # --- 判斷 PFAS ---
        # 邏輯：檢查 Test Requested 是否包含 "PFAS" 字串 [4, 5]
        # 通常 Test Requested 位於第一頁或第二頁
        test_requested_section = ""
        for i in range(min(3, len(pdf.pages))): # 只看前3頁
            test_requested_section += pdf.pages[i].extract_text()
        
        # 尋找 "Test Requested" 區塊並檢查內容
        if "Test Requested" in test_requested_section or "检测要求" in test_requested_section:
             if "PFAS" in test_requested_section: # 嚴格匹配 PFAS 字串
                 data['PFAS'] = "REPORT"
             else:
                 data['PFAS'] = "" # 沒有則留空 [5]

    return data

def aggregate_reports(file_paths):
    """加總多份報告的邏輯"""
    
    aggregated_data = {key: 0.0 for key in KEYWORDS_MAP.keys()} # 用於比較大小，初始0
    final_display_data = {key: "N.D." for key in KEYWORDS_MAP.keys()} # 最終顯示
    
    max_date = datetime.min
    max_pb_value = -1.0
    file_with_max_pb = ""
    pfas_status = "" # 只要有一份是 REPORT 就顯示? 這裡依照指示：個別判斷，但彙總表邏輯需統一
    
    # 這裡的邏輯：若多份報告Test Requested都沒PFAS，則總表空白。
    # 若有任何一份有出現PFAS字串，邏輯上應顯示REPORT，但根據您的指示"PFAS僅需判斷報告中有檢測項目"，通常是指單一報告。
    # 彙總邏輯：如果所有報告都沒出現 PFAS 字串，則空白。
    
    for f_path in file_paths:
        data = extract_pdf_data(f_path)
        filename = os.path.basename(f_path)
        
        # 1. 日期取最新 [5]
        if data['DATE'] and data['DATE'] > max_date:
            max_date = data['DATE']
            
        # 2. PFAS 判斷 (聯集)
        if data['PFAS'] == "REPORT":
            pfas_status = "REPORT"
            
        # 3. 數值取最大值 (數字 > N.D.) [5]
        # 先處理 Pb 以決定 FILE NAME
        pb_val = data['Pb']
        current_pb_num = 0.0
        if isinstance(pb_val, (int, float)):
            current_pb_num = pb_val
        
        if current_pb_num > max_pb_value:
            max_pb_value = current_pb_num
            file_with_max_pb = filename # [5] 規則1
            
        # 處理所有化學物質
        for key in KEYWORDS_MAP.keys():
            val = data[key]
            # 如果是數字
            if isinstance(val, (int, float)):
                # 如果當前最大值是數字，比較大小
                if isinstance(aggregated_data[key], (int, float)):
                    if val > aggregated_data[key]:
                        aggregated_data[key] = val
                        final_display_data[key] = val # 更新顯示值
                else:
                    # 之前是 N.D.，現在是數字，直接覆蓋
                    aggregated_data[key] = val
                    final_display_data[key] = val
            # 如果是 N.D.，且當前紀錄也是 N.D. (初始)，則保持 N.D.
            # 如果當前已經有數字，則忽略 N.D.

    # 格式化日期
    final_date_str = max_date.strftime("%Y/%m/%d") if max_date != datetime.min else ""
    
    # 建立最終 Row
    row = {
        'FILE NAME': file_with_max_pb,
        **final_display_data,
        'PFAS': pfas_status,
        'DATE': final_date_str
    }
    
    return row

# --- 使用範例 ---
# 假設您將檔案放在當前目錄的 'reports' 資料夾下
# file_list = [os.path.join('reports', f) for f in os.listdir('reports') if f.endswith('.pdf')]

# 這裡模擬輸入檔案路徑 (請替換為您實際的檔案路徑)
# file_list = [
#     "1.價啣 S1000-2M.pdf", 
#     "2.中文_Prepreg S1000-2MB.pdf", 
#     "CTI_鍍金層.pdf", 
#     # ... 其他報告
# ]

# 執行彙總 (需有實際檔案才能執行)
# result_row = aggregate_reports(file_list)

# 轉換為 DataFrame 並顯示
# df = pd.DataFrame([result_row])
# 調整欄位順序
# cols = ['FILE NAME', 'Pb', 'Cd', 'Hg', 'Cr6+', 'PBB', 'PBDE', 'DEHP', 'DBP', 'BBP', 'DIBP', 'F', 'CL', 'BR', 'PFOS', 'PFAS', 'DATE']
# df = df[cols]
# print(df)
# df.to_excel("Summary_Report.xlsx", index=False)
