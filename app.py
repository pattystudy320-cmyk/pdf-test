import streamlit as st
import pdfplumber
import pandas as pd
import re
import io
from dateutil import parser

# ==========================================
# 0. 強制清除快取 (避免舊資料干擾)
# ==========================================
try:
    if hasattr(st, 'cache_data'):
        st.cache_data.clear()
    elif hasattr(st, 'experimental_memo'):
        st.experimental_memo.clear()
    elif hasattr(st, 'cache'):
        st.cache_resource.clear()
except:
    pass

# ==========================================
# 1. 全局配置與字典
# ==========================================
TARGET_ITEMS = [
    "Pb", "Cd", "Hg", "Cr6+", "PBBs", "PBDEs",
    "DEHP", "DBP", "BBP", "DIBP",
    "F", "Cl", "Br", "I",
    "PFOS", "PFAS", "DATE", "FILENAME"
]

# --- SGS 專用字典 ---
SGS_OPTIMIZED_MAP = {
    'Pb': ['Lead', 'Pb', '鉛', '铅'],
    'Cd': ['Cadmium', 'Cd', '鎘', '镉'],
    'Hg': ['Mercury', 'Hg', '汞'],
    'Cr6+': ['Hexavalent Chromium', 'Cr(VI)', '六價鉻', '六价铬', 'Hexavalent', 'Cr6+'],
    'PBBs': ['Polybrominated biphenyls', 'PBB', '多溴聯苯', '多溴联苯', 'Sum of PBBs'],
    'PBDEs': ['Polybrominated diphenyl ethers', 'PBDE', '多溴二苯醚', 'Sum of PBDEs'],
    'DEHP': ['Bis(2-ethylhexyl) phthalate', 'DEHP', '鄰苯二甲酸二(2-乙基己基)酯', 'Di(2-ethylhexyl) phthalate'],
    'DBP': ['Dibutyl phthalate', 'DBP', '鄰苯二甲酸二丁酯'],
    'BBP': ['Butyl benzyl phthalate', 'BBP', '鄰苯二甲酸丁苄酯'],
    'DIBP': ['Diisobutyl phthalate', 'DIBP', '鄰苯二甲酸二異丁酯'],
    'F': ['Fluorine', '氟', 'Halogen-Fluorine'],
    'Cl': ['Chlorine', '氯', 'Halogen-Chlorine'],
    'Br': ['Bromine', '溴', 'Halogen-Bromine'],
    'I': ['Iodine', '碘', 'Halogen-Iodine'],
    'PFOS': ['Perfluorooctane sulfonic acid', 'PFOS', '全氟辛烷磺酸', 'Perfluorooctane Sulfonates'],
    'PFAS': ['PFAS']
}

# --- CTI/Intertek 通用字典 ---
UNIFIED_REGEX_MAP = {
    r"(?i)\b(Lead|Pb|铅)\b": "Pb",
    r"(?i)\b(Cadmium|Cd|镉)\b": "Cd",
    r"(?i)\b(Mercury|Hg|汞)\b": "Hg",
    r"(?i)\b(Hexavalent Chromium|Cr\(?VI\)?|六价铬)\b": "Cr6+",
    r"(?i)\b(DEHP|Di\(2-ethylhexyl\)\s*phthalate)\b": "DEHP",
    r"(?i)\b(DBP|Dibutyl\s*phthalate)\b": "DBP",
    r"(?i)\b(BBP|Butyl\s*benzyl\s*phthalate)\b": "BBP",
    r"(?i)\b(DIBP|Diisobutyl\s*phthalate)\b": "DIBP",
    r"(?i)(Fluorine|氟).*\((F|F-)\)": "F",
    r"(?i)(Chlorine|氯|氣).*\((Cl|Cl-)\)": "Cl",
    r"(?i)(Bromine|溴).*\((Br|Br-)\)": "Br",
    r"(?i)(Iodine|碘).*\((I|I-)\)": "I",
    r"(?i)(Perfluorooctane\s*sulfonic\s*acid\s*\(PFOS\)|PFOS.*(salts|及其盐)|全氟辛烷磺酸)": "PFOS"
}

# 加總項目 Regex
PBB_SUBITEMS = r"(?i)(Monobromobiphenyl|Dibromobiphenyl|Tribromobiphenyl|Tetrabromobiphenyl|Pentabromobiphenyl|Hexabromobiphenyl|Heptabromobiphenyl|Octabromobiphenyl|Nonabromobiphenyl|Decabromobiphenyl|一溴联苯|二溴联苯|三溴联苯|四溴联苯|五溴联苯|六溴联苯|七溴联苯|八溴联苯|九溴联苯|十溴联苯)"
PBDE_SUBITEMS = r"(?i)(Monobromodiphenyl ether|Dibromodiphenyl ether|Tribromodiphenyl ether|Tetrabromodiphenyl ether|Pentabromodiphenyl ether|Hexabromodiphenyl ether|Heptabromodiphenyl ether|Octabromodiphenyl ether|Nonabromodiphenyl ether|Decabromodiphenyl ether|一溴二苯醚|二溴二苯醚|三溴二苯醚|四溴二苯醚|五溴二苯醚|六溴二苯醚|七溴二苯醚|八溴二苯醚|九溴二苯醚|十溴二苯醚)"

# 英文月份對照表
MONTH_MAP = {
    "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04", "May": "05", "Jun": "06",
    "Jul": "07", "Aug": "08", "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
    "JAN": "01", "FEB": "02", "MAR": "03", "APR": "04", "MAY": "05", "JUN": "06",
    "JUL": "07", "AUG": "08", "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12"
}

# ==========================================
# 2. 工具函數
# ==========================================
def clean_date_str(date_str):
    if not date_str: return "1900/01/01"
    clean_str = str(date_str).strip()
    
    # 處理中文日期: 2024 年 04 月 01 日 -> 2024/04/01
    clean_str = clean_str.replace("年", "/").replace("月", "/").replace("日", "")
    
    # 英文月份轉換
    for mon, digit in MONTH_MAP.items():
        if mon in clean_str:
            clean_str = clean_str.replace(mon, digit)
            break
            
    # 移除 Page 1 of 16 這類雜訊
    clean_str = re.split(r"(Page|頁)", clean_str, flags=re.IGNORECASE)[0]
    
    try:
        dt = parser.parse(clean_str, fuzzy=True)
        return dt.strftime("%Y/%m/%d")
    except:
        return "1900/01/01"

def clean_value(val_str):
    if not val_str: return None
    val_str = str(val_str).strip()

    # 排除 MDL/Limit 等標題行
    if val_str.lower() in ["mdl", "limit", "unit", "result", "loq", "requirement"]:
        return None
    # 處理 N.D. / Negative
    if re.search(r"(?i)(N\.?D\.?|Not Detected|<|Negative)", val_str):
        return "N.D."

    if re.search(r"(?i)(Positive)", val_str):
        return "POSITIVE"
    # 提取數字
    nums = re.findall(r"\d+\.?\d*", val_str)
    if nums:
        try:
            return float(nums[0]) # 取第一個找到的數字
        except:
            pass
    return None

def get_value_priority(val):
    if isinstance(val, (int, float)): return (3, val)
    if val in ["NEGATIVE", "POSITIVE"]: return (2, 0)
    if val == "N.D.": return (1, 0)
    return (0, 0)

# ==========================================
# 3. SGS 解析模組 (v6.6 最終修正版)
# ==========================================
def parse_sgs(pdf_obj, full_text, first_page_text):
    result = {k: None for k in SGS_OPTIMIZED_MAP.keys()}
    result['PFAS'] = ""
    result['DATE'] = ""
    
    # --- 1. 日期抓取 (擴充版) ---
    # 擴大掃描行數到 40，並增加中文與連字號格式支援
    lines = first_page_text.split('\n')
    for line in lines[:40]:
        # 只要行內有 'Date' 或 '日期'，就嘗試解析
        if re.search(r"(?i)(Date|日期)", line) and not re.search(r"(?i)(Received|Testing|Period|接收|周期)", line):
            
            # 模式 A: 中文日期 (2024 年 04 月 01 日)
            match_chi = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", line)
            
            # 模式 B: 連字號英文 (04-Mar-2025)
            match_hyphen = re.search(r"(\d{1,2})\s*[-]\s*([A-Za-z]{3})\s*[-]\s*(\d{4})", line)
            
            # 模式 C: 標準混和 (Feb 27, 2025)
            match_mixed = re.search(r"(?i)(\d{2}[-.\s][A-Za-z]{3}[-.\s]\d{4}|\d{2}\s[A-Za-z]{3}\s\d{4})", line)
            
            # 模式 D: 純英文逗號 (Feb 27, 2025)
            match_en = re.search(r"(?i)([A-Za-z]{3}\s+\d{1,2},?\s*\d{4})", line)
            
            # 模式 E: 純數字斜線 (2025/02/27)
            match_num = re.search(r"(\d{4}[-./]\s?\d{1,2}[-./]\s?\d{1,2})", line)

            found_date_str = None
            if match_chi:
                found_date_str = f"{match_chi.group(1)}/{match_chi.group(2)}/{match_chi.group(3)}"
            elif match_hyphen:
                found_date_str = match_hyphen.group(0)
            elif match_mixed:
                found_date_str = match_mixed.group(0)
            elif match_en:
                found_date_str = match_en.group(0)
            elif match_num:
                found_date_str = match_num.group(0)
            
            if found_date_str:
                result['DATE'] = clean_date_str(found_date_str)
                break
    
    # --- 2. 數據抓取 (欄位定位法 - 消去法優化) ---
    pbb_sum = 0; pbde_sum = 0; pbb_found = False; pbde_found = False

    with pdfplumber.open(pdf_obj) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                if not table: continue

                # 尋找結果所在的欄位索引
                header_row_idx = -1
                result_col_idx = -1

                # 排除欄位關鍵字 (小寫)
                ignore_keywords = ['limit', 'unit', 'mdl', 'loq', 'test item', 'test method', 'cas', '限值', '單位', '測試項目', '方法']

                # 先掃描表頭
                for r_idx, row in enumerate(table[:5]):
                    row_text = [str(cell).lower() for cell in row if cell]
                    row_str_lower = " ".join(row_text)

                    # 判斷是否為表頭列
                    if any(x in row_str_lower for x in ['test item', 'unit', 'mdl', 'limit', '測試項目', '單位']):
                        header_row_idx = r_idx
                        
                        # 策略 1: 尋找明確標題 (Result, No.1, 結果)
                        # 必須同時「包含結果關鍵字」且「不包含 Limit/Unit」
                        for c_idx, cell in enumerate(row):
                            cell_str = str(cell).strip()
                            if re.search(r"(?i)(Result|No\.|結果)", cell_str) and not re.search(r"(?i)(Limit|Unit)", cell_str):
                                result_col_idx = c_idx
                                break
                        
                        # 策略 2 (新): 若找不到明確標題，使用「消去法」+「最右側原則」
                        # 排除掉 Limit, Unit, MDL, Test Item 之後，最右邊的那個通常就是 A1/001/結果
                        if result_col_idx == -1:
                            valid_candidates = []
                            for c_idx, cell in enumerate(row):
                                if not cell: continue
                                c_text = str(cell).lower().strip()
                                # 檢查是否為排除關鍵字
                                is_ignored = any(kw in c_text for kw in ignore_keywords)
                                if not is_ignored:
                                    valid_candidates.append(c_idx)
                            
                            if valid_candidates:
                                result_col_idx = valid_candidates[-1] # 取最右邊的一個
                        break
                
                # 遍歷數據列
                start_row = header_row_idx + 1 if header_row_idx != -1 else 0

                for row in table[start_row:]:
                    row_clean = [str(c) for c in row if c]
                    row_str = " ".join(row_clean).replace("\n", " ")

                    # 排除 PFOA (若需排除)
                    if re.search(r"(?i)(Perfluorooctanoic\s*Acid|全氟辛酸)", row_str) and "PFOA" not in SGS_OPTIMIZED_MAP: continue
                    if "PFAS" in row_str and not result['PFAS']: result['PFAS'] = "REPORT"
                    
                    # A. 識別測項
                    matched_key = None
                    for key, keywords in SGS_OPTIMIZED_MAP.items():
                        if any(kw.lower() in row_str.lower() for kw in keywords):
                            if key == "PFOS" and re.search(r"(?i)(Total|PFOSF|Derivative|总和|衍生物)", row_str): continue
                            if key in ['F', 'Cl', 'Br', 'I'] and not re.search(r"\((F|Cl|Br|I)-?\)", row_str): continue
                            matched_key = key
                            break
                    
                    is_pbb = re.search(PBB_SUBITEMS, row_str)
                    is_pbde = re.search(PBDE_SUBITEMS, row_str)

                    if not matched_key and not is_pbb and not is_pbde:
                        continue
                    
                    # B. 抓取數值 (使用欄位索引)
                    target_val_str = ""

                    if result_col_idx != -1 and result_col_idx < len(row):
                        target_val_str = str(row[result_col_idx])
                    else:
                        # 備用：倒著找最後一個非空值 (避開單位和Limit)
                        for cell in reversed(row):
                            if cell:
                                cell_s = str(cell).strip()
                                if cell_s.lower() in ["mg/kg", "ppm", "%"]: continue # 跳過單位
                                target_val_str = cell_s
                                break
                    
                    cleaned_val = clean_value(target_val_str)

                    # C. 存入結果
                    if matched_key:
                        current_val = result.get(matched_key)
                        if get_value_priority(cleaned_val) > get_value_priority(current_val):
                            result[matched_key] = cleaned_val
                    
                    elif is_pbb:
                        pbb_found = True
                        if isinstance(cleaned_val, (int, float)): pbb_sum += cleaned_val
                    
                    elif is_pbde:
                        pbde_found = True
                        if isinstance(cleaned_val, (int, float)): pbde_sum += cleaned_val
    
    # 處理總和項
    if "PFAS" in first_page_text: result["PFAS"] = "REPORT"
    result["PBBs"] = pbb_sum if pbb_found and pbb_sum > 0 else "N.D."
    result["PBDEs"] = pbde_sum if pbde_found and pbde_sum > 0 else "N.D."
    return result

# ==========================================
# 4. CTI/Intertek 解析模組 (維持原樣)
# ==========================================
def parse_cti(pdf_obj, full_text, first_page_text):
    result = {k: None for k in TARGET_ITEMS if k not in ['FILENAME', 'DATE']}
    result['PFAS'] = ""

    date_match = re.search(r"(?i)(?:Date|日期)\s*[:：]?\s*(\d{4}[-./年]\s?\d{1,2}[-./月]\s?\d{1,2}|\w{3}\.\s*\d{1,2},\s*\d{4})", first_page_text)
    result['DATE'] = clean_date_str(date_match.group(1)) if date_match else ""
    pbb_sum = 0; pbde_sum = 0; pbb_found = False; pbde_found = False
    with pdfplumber.open(pdf_obj) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                if not table: continue

                header = table[0]
                res_idx = -1
                for i, col in enumerate(header):
                    if col and re.search(r"(?i)(Result|结果)", str(col)):
                        res_idx = i; break
                
                if res_idx == -1:
                    for i, col in enumerate(header):
                        if col and re.search(r"(?i)(MDL|LOQ|RL|Limit)", str(col)):
                            res_idx = i - 1 if i > 0 else i + 1; break
                
                if res_idx == -1: continue
                for row_idx, row in enumerate(table[1:]):
                    if len(row) <= res_idx: continue
                    row_str = " ".join([str(c) for c in row if c]).replace("\n", " ")

                    if re.search(r"(?i)(PFOA|Perfluorooctanoic\s*Acid|全氟辛酸)", row_str): continue
                    if "PFAS" in row_str and not result['PFAS']: result['PFAS'] = "REPORT"
                    val = clean_value(row[res_idx])
                    
                    for pat, key in UNIFIED_REGEX_MAP.items():
                        if re.search(pat, row_str):
                            if key == "PFOS" and re.search(r"(?i)(Total|PFOSF|Derivative|总和|衍生物)", row_str): continue
                            if val is not None:
                                current_val = result.get(key)
                                if current_val is None or current_val == "N.D.": result[key] = val
                                elif isinstance(val, (int, float)) and isinstance(current_val, (int, float)):
                                    result[key] = max(val, current_val)
                            break
                    
                    if re.search(PBB_SUBITEMS, row_str):
                        pbb_found = True; pbb_sum += val if isinstance(val, (int, float)) else 0
                    if re.search(PBDE_SUBITEMS, row_str):
                        pbde_found = True; pbde_sum += val if isinstance(val, (int, float)) else 0
    
    if "PFAS" in first_page_text: result["PFAS"] = "REPORT"
    result["PBBs"] = pbb_sum if pbb_found and pbb_sum > 0 else "N.D."
    result["PBDEs"] = pbde_sum if pbde_found and pbde_sum > 0 else "N.D."
    return result

def parse_intertek(pdf_obj, full_text, first_page_text):
    result = {k: None for k in TARGET_ITEMS if k not in ['FILENAME', 'DATE']}
    result['PFAS'] = ""
    result['DATE'] = ""
    lines = first_page_text.split('\n')
    date_pat = r"(?i)(?:Date|Issue Date| 발행일자 )\s*[:：]?\s*([A-Za-z]{3}\s+\d{1,2},?\s*\d{4}|\d{4}[.\s]+\d{1,2}[.\s]+\d{1,2})"
    for line in lines[:25]:
        match = re.search(date_pat, line)
        if match:
            result['DATE'] = clean_date_str(match.group(1))
            break
    
    pbb_sum = 0; pbde_sum = 0; pbb_found = False; pbde_found = False
    with pdfplumber.open(pdf_obj) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                if not table: continue

                header = [str(c).replace("\n", " ") for c in table[0]]
                mdl_idx = -1
                for i, col in enumerate(header):
                    if col and re.search(r"(?i)(MDL|LOQ|Detection| 검출한계 )", str(col)):
                        mdl_idx = i; break
                
                if mdl_idx == -1: continue

                res_idx = -1
                if len(table) > 1:
                    row1 = table[1]
                    left_val = str(row1[mdl_idx-1]) if mdl_idx > 0 else ""
                    right_val = str(row1[mdl_idx+1]) if mdl_idx + 1 < len(row1) else ""

                    if re.search(r"(?i)(N\.?D|Negative|<)", left_val): res_idx = mdl_idx - 1
                    elif re.search(r"(?i)(N\.?D|Negative|<)", right_val): res_idx = mdl_idx + 1
                    elif mdl_idx + 1 < len(header) and re.search(r"(?i)(Result| 결과 )", str(header[mdl_idx+1])): res_idx = mdl_idx + 1
                    elif mdl_idx - 1 >= 0 and re.search(r"(?i)(Result|结果)", str(header[mdl_idx-1])): res_idx = mdl_idx - 1
                
                if res_idx == -1: continue
                for row in table[1:]:
                    if len(row) <= res_idx: continue
                    row_str = " ".join([str(c) for c in row if c]).replace("\n", " ")
                    val = clean_value(row[res_idx])

                    if re.search(r"(?i)(PFOA|Perfluorooctanoic\s*Acid|全氟辛酸)", row_str): continue
                    if "PFAS" in row_str and not result['PFAS']: result['PFAS'] = "REPORT"

                    for pat, key in UNIFIED_REGEX_MAP.items():
                        if re.search(pat, row_str):
                            if key == "PFOS" and re.search(r"(?i)(Total|PFOSF|Derivative|总和|衍生物)", row_str): continue
                            if val is not None:
                                current_val = result.get(key)
                                if current_val is None or current_val == "N.D.": result[key] = val
                                elif isinstance(val, (int, float)) and isinstance(current_val, (int, float)):
                                    result[key] = max(val, current_val)
                            break
                    
                    if re.search(PBB_SUBITEMS, row_str):
                        pbb_found = True; pbb_sum += val if isinstance(val, (int, float)) else 0
                    if re.search(PBDE_SUBITEMS, row_str):
                        pbde_found = True; pbde_sum += val if isinstance(val, (int, float)) else 0
    
    if "PFAS" in first_page_text: result["PFAS"] = "REPORT"
    result["PBBs"] = pbb_sum if pbb_found and pbb_sum > 0 else "N.D."
    result["PBDEs"] = pbde_sum if pbde_found and pbde_sum > 0 else "N.D."
    return result

# ==========================================
# 5. 主程式
# ==========================================
def identify_vendor(first_page_text):
    text = first_page_text.lower()
    if "intertek" in text: return "INTERTEK"
    if "cti" in text or "华测" in text: return "CTI"
    if "sgs" in text: return "SGS"
    return "UNKNOWN"

def main():
    st.set_page_config(page_title="化學報告自動彙整系統 v6.6 (All Fixed)", layout="wide")
    st.title("🧪 化學測試報告自動彙整系統 v6.6")

    st.markdown("""
    **SGS 專屬修正說明 (最終修正版)：**
    1. **已修復 'list object' 錯誤：** 確保正確讀取 PDF 第一頁 [pdf.pages[0]]。
    2. **日期格式增強：** 支援中文日期 (`2024 年...`)、連字號 (`04-Mar...`) 及混合格式。
    3. **結果欄位智慧定位：** 採用「消去法」，自動排除 Limit/Unit/MDL，鎖定最右側結果欄 (解決 A1/001 標題問題)。
    """)
    
    uploaded_files = st.file_uploader("請上傳 PDF 報告 (支援多檔)", type="pdf", accept_multiple_files=True)

    if uploaded_files:
        if st.button("開始分析"):
            valid_results = []
            bucket_unknown = []
            bucket_error = []

            progress_bar = st.progress(0)
            status_text = st.empty()

            for i, file in enumerate(uploaded_files):
                status_text.text(f"正在處理: {file.name}...")
                try:
                    with pdfplumber.open(file) as pdf:
                        # [安全性檢查] 確保檔案有頁面
                        if len(pdf.pages) == 0:
                            bucket_error.append(file.name)
                            continue

                        # [修正] 使用 [0] 讀取第一頁，修復 list object error
                        first_page_text = pdf.pages[0].extract_text()

                        if not first_page_text:
                            bucket_error.append(f"{file.name} (第一頁無法讀取)")
                            continue

                        full_text = ""
                        for page in pdf.pages:
                            txt = page.extract_text()
                            if txt: full_text += txt + "\n"

                        vendor = identify_vendor(first_page_text)
                        data = None

                        if vendor == "SGS":
                            data = parse_sgs(file, full_text, first_page_text)
                        elif vendor == "CTI":
                            data = parse_cti(file, full_text, first_page_text)
                        elif vendor == "INTERTEK":
                            data = parse_intertek(file, full_text, first_page_text)
                        else:
                            bucket_unknown.append(file.name)
                            continue

                        if data:
                            data['FILENAME'] = file.name
                            valid_results.append(data)
                        else:
                            bucket_error.append(f"{file.name} (解析失敗)")

                except Exception as e:
                    bucket_error.append(f"{file.name} (錯誤: {str(e)})")

                progress_bar.progress((i + 1) / len(uploaded_files))

            status_text.text("分析完成！")

            if valid_results:
                df_final = pd.DataFrame(valid_results)

                # 欄位排序
                cols = ["FILENAME", "DATE"] + [c for c in TARGET_ITEMS if c not in ["FILENAME", "DATE"]]
                # 確保欄位存在，避免 KeyError
                available_cols = [c for c in cols if c in df_final.columns]
                df_final = df_final[available_cols]

                st.success(f" ✅  成功處理 {len(valid_results)} 份報告：")
                st.dataframe(df_final)

                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    df_final.to_excel(writer, index=False, sheet_name='Summary')
                output.seek(0)

                st.download_button(
                    label=" 📥  下載 Excel",
                    data=output,
                    file_name=f"Report_Summary_{pd.Timestamp.now().strftime('%Y%m%d')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            else:
                st.warning("未提取到有效數據。")

            if bucket_unknown or bucket_error:
                st.divider()
                st.subheader(" ⚠ ️ 異常報告")
                if bucket_unknown:
                    for name in bucket_unknown: st.write(f"- 🧪 未識別廠商: {name}")
                if bucket_error:
                    for name in bucket_error: st.write(f"-  🔴  錯誤: {name}")

if __name__ == "__main__":
    main()
