import streamlit as st
import pdfplumber
import pandas as pd
import re
import io
from dateutil import parser

# ==========================================
# 0. 強制清除快取
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

# --- SGS 專用字典 (含中文與英文) ---
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

PBB_SUBITEMS = r"(?i)(Monobromobiphenyl|Dibromobiphenyl|Tribromobiphenyl|Tetrabromobiphenyl|Pentabromobiphenyl|Hexabromobiphenyl|Heptabromobiphenyl|Octabromobiphenyl|Nonabromobiphenyl|Decabromobiphenyl|一溴联苯|二溴联苯|三溴联苯|四溴联苯|五溴联苯|六溴联苯|七溴联苯|八溴联苯|九溴联苯|十溴联苯)"
PBDE_SUBITEMS = r"(?i)(Monobromodiphenyl ether|Dibromodiphenyl ether|Tribromodiphenyl ether|Tetrabromodiphenyl ether|Pentabromodiphenyl ether|Hexabromodiphenyl ether|Heptabromodiphenyl ether|Octabromodiphenyl ether|Nonabromodiphenyl ether|Decabromodiphenyl ether|一溴二苯醚|二溴二苯醚|三溴二苯醚|四溴二苯醚|五溴二苯醚|六溴二苯醚|七溴二苯醚|八溴二苯醚|九溴二苯醚|十溴二苯醚)"

# 英文月份對照表 (含全名與縮寫)
MONTH_MAP = {
    "January": "01", "February": "02", "March": "03", "April": "04", "May": "05", "June": "06",
    "July": "07", "August": "08", "September": "09", "October": "10", "November": "11", "December": "12",
    "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04", "Jun": "06",
    "Jul": "07", "Aug": "08", "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12"
}

# ==========================================
# 2. 工具函數
# ==========================================
def clean_date_str(date_str):
    if not date_str: return "1900/01/01"
    clean_str = str(date_str).strip()
    # 英文月份轉換
    for mon, digit in MONTH_MAP.items():
        if re.search(r"(?i)\b" + mon + r"\b", clean_str):
            clean_str = re.sub(r"(?i)\b" + mon + r"\b", digit, clean_str)
            break
    clean_str = clean_str.replace("年", "/").replace("月", "/").replace("日", "").replace("-", "/")
    clean_str = re.split(r"(Page|頁)", clean_str, flags=re.IGNORECASE)
    try:
        dt = parser.parse(clean_str, fuzzy=True)
        return dt.strftime("%Y/%m/%d")
    except:
        return "1900/01/01"

def clean_value(val_str):
    if not val_str: return None
    val_str = str(val_str).strip()
    if val_str.lower() in ["mdl", "limit", "unit", "result", "loq", "requirement", "max"]:
        return None
    if re.search(r"(?i)(N\.?D\.?|Not Detected|<|Negative)", val_str):
        return "N.D."
    if re.search(r"(?i)(Positive)", val_str):
        return "POSITIVE"
    nums = re.findall(r"\d+\.?\d*", val_str)
    if nums:
        try:
            return float(nums)
        except:
            pass
    return None

def get_value_priority(val):
    if isinstance(val, (int, float)): return (3, val)
    if val in ["NEGATIVE", "POSITIVE"]: return (2, 0)
    if val == "N.D.": return (1, 0)
    return (0, 0)

# ==========================================
# 3. SGS 馬來西亞專用模組 (NEW)
# ==========================================
def parse_sgs_malaysia(pdf_obj, full_text, first_page_text):
    result = {k: None for k in SGS_OPTIMIZED_MAP.keys()}
    result['PFAS'] = ""
    result['DATE'] = ""

    # 1. 馬來西亞日期抓取 (支援 January 全名)
    # 格式: 23-January-2025, 23 Jan 2025
    lines = first_page_text.split('\n')
    for line in lines[:30]:
        if re.search(r"(?i)(Date|日期)", line):
            # 抓取 23-January-2025 或 23 Jan 2025
            match = re.search(r"(\d{1,2}[-.\s]+[A-Za-z]+[-.\s]+\d{4})", line)
            if match:
                result['DATE'] = clean_date_str(match.group(1))
                break

    # 2. 數據抓取 (嚴格依賴 Result 欄位索引)
    pbb_sum = 0; pbde_sum = 0; pbb_found = False; pbde_found = False
    
    with pdfplumber.open(pdf_obj) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                if not table: continue
                
                header_row_idx = -1
                result_col_idx = -1
                
                # 尋找表頭
                for r_idx, row in enumerate(table[:5]):
                    row_text = " ".join([str(cell).lower() for cell in row if cell])
                    # 必須包含 Test Parameter 或 Test Item，且包含 Result
                    if ("test parameter" in row_text or "test item" in row_text) and "result" in row_text:
                        header_row_idx = r_idx
                        # 找出 Result 所在的確切欄位索引
                        for c_idx, cell in enumerate(row):
                            if cell and re.search(r"(?i)(Result)", str(cell)):
                                result_col_idx = c_idx
                                break
                        break
                
                # 如果找不到明確的 Result 欄位，但表頭存在，嘗試用邏輯判斷
                # 馬來西亞報告 Result 通常在第 2 欄 (Index 1) 或 第 4 欄 (Index 3)
                # 絕對不能用「最右邊」，因為最右邊是 Limit/MDL
                if header_row_idx != -1 and result_col_idx == -1:
                     # 嘗試找尋非空、非 Unit、非 MDL、非 Limit 的中間欄位
                     pass 

                if header_row_idx == -1: continue

                # 遍歷數據
                for row in table[header_row_idx + 1:]:
                    if not row: continue
                    row_clean = [str(c) for c in row if c]
                    row_str = " ".join(row_clean).replace("\n", " ")
                    
                    if re.search(r"(?i)(PFOA|Perfluorooctanoic\s*Acid)", row_str) and "PFOA" not in SGS_OPTIMIZED_MAP: continue
                    if "PFAS" in row_str and not result['PFAS']: result['PFAS'] = "REPORT"

                    # 識別測項
                    matched_key = None
                    for key, keywords in SGS_OPTIMIZED_MAP.items():
                        if any(kw.lower() in row_str.lower() for kw in keywords):
                            if key == "PFOS" and re.search(r"(?i)(Total|PFOSF|Derivative)", row_str): continue
                            if key in ['F', 'Cl', 'Br', 'I'] and not re.search(r"\((F|Cl|Br|I)-?\)", row_str): continue
                            matched_key = key
                            break
                    
                    is_pbb = re.search(PBB_SUBITEMS, row_str)
                    is_pbde = re.search(PBDE_SUBITEMS, row_str)
                    
                    if not matched_key and not is_pbb and not is_pbde: continue

                    # 抓取數值
                    target_val = None
                    
                    # 策略 A: 如果有找到 Result 欄位索引，直接取值
                    if result_col_idx != -1 and result_col_idx < len(row):
                        target_val = str(row[result_col_idx])
                    
                    # 策略 B: 如果 PDF 表格黏連 (Result 欄位內容跑到 Item 欄位字串裡，例如 "N.D.Fluorine")
                    # 馬來西亞報告常發生這種情況，需要用 Regex 在整行字串中找 N.D.
                    if (not target_val or target_val.strip() == ""):
                        # 在整行中尋找 N.D. 且該 N.D. 不是 Item 的一部分
                        if "N.D." in row_str:
                             target_val = "N.D."
                        else:
                             # 找數字 (排除 Item 名稱中的數字)
                             nums = re.findall(r"\b\d+\.?\d*\b", row_str)
                             # 這裡很危險，因為 Limit 和 MDL 也是數字
                             # 通常結果如果是數字，會出現在 Unit 之前。
                             # 但簡單起見，如果 Strategy A 失敗，這裡先保守處理
                             pass

                    cleaned_val = clean_value(target_val)
                    
                    # 存入結果
                    if matched_key:
                        current_val = result.get(matched_key)
                        if get_value_priority(cleaned_val) > get_value_priority(current_val):
                            result[matched_key] = cleaned_val
                    elif is_pbb and isinstance(cleaned_val, (int, float)):
                        pbb_found = True; pbb_sum += cleaned_val
                    elif is_pbde and isinstance(cleaned_val, (int, float)):
                        pbde_found = True; pbde_sum += cleaned_val

    if "PFAS" in first_page_text: result["PFAS"] = "REPORT"
    result["PBBs"] = pbb_sum if pbb_found and pbb_sum > 0 else "N.D."
    result["PBDEs"] = pbde_sum if pbde_found and pbde_sum > 0 else "N.D."
    return result

# ==========================================
# 4. 標準 SGS 解析模組 (保留原本針對台灣/中國優化的版本)
# ==========================================
def parse_sgs_standard(pdf_obj, full_text, first_page_text):
    # 這是您之前 v6.5 的代碼，用於處理台灣和中國報告 (Limit 在中間，Result 在最右邊 A1/001)
    result = {k: None for k in SGS_OPTIMIZED_MAP.keys()}
    result['PFAS'] = ""
    result['DATE'] = ""

    lines = first_page_text.split('\n')
    for line in lines[:25]:
        if re.search(r"(?i)(Date|日期)", line) and not re.search(r"(?i)(Received|Testing|Period)", line):
            match_en = re.search(r"(?i)(?:Date|日期)\s*[:：]?\s*([A-Za-z]{3}\s+\d{1,2},?\s*\d{4}|\d{1,2}[-.\s][A-Za-z]{3}[-.\s]\d{4})", line)
            match_num = re.search(r"(?:Date|日期)\s*[:：]?\s*(\d{4}[-./年]\s?\d{1,2}[-./月]\s?\d{1,2})", line)
            if match_en: result['DATE'] = clean_date_str(match_en.group(1)); break
            elif match_num: result['DATE'] = clean_date_str(match_num.group(1)); break

    pbb_sum = 0; pbde_sum = 0; pbb_found = False; pbde_found = False
    
    with pdfplumber.open(pdf_obj) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                if not table: continue
                header_row_idx = -1; result_col_idx = -1; limit_col_idx = -1; mdl_col_idx = -1
                
                for r_idx, row in enumerate(table[:5]): 
                    row_str_lower = " ".join([str(cell).lower() for cell in row if cell])
                    if any(x in row_str_lower for x in ['test item', 'unit', 'mdl', 'limit', '測試項目']):
                        header_row_idx = r_idx
                        for c_idx, cell in enumerate(row):
                            cell_str = str(cell).strip()
                            if re.search(r"(?i)(Limit|限值)", cell_str): limit_col_idx = c_idx
                            elif re.search(r"(?i)(MDL|Method Det)", cell_str): mdl_col_idx = c_idx
                            elif re.search(r"(?i)(Result|No\.|結果|00\d|A\d|Sample)", cell_str): result_col_idx = c_idx
                        
                        if result_col_idx == -1:
                            for c_idx in range(len(row)-1, -1, -1):
                                if c_idx != limit_col_idx and c_idx != mdl_col_idx and row[c_idx]:
                                    result_col_idx = c_idx; break
                        break
                
                start_row = header_row_idx + 1 if header_row_idx != -1 else 0
                for row in table[start_row:]:
                    row_clean = [str(c) for c in row if c]
                    row_str = " ".join(row_clean).replace("\n", " ")
                    
                    if re.search(r"(?i)(Perfluorooctanoic\s*Acid)", row_str) and "PFOA" not in SGS_OPTIMIZED_MAP: continue
                    if "PFAS" in row_str and not result['PFAS']: result['PFAS'] = "REPORT"

                    matched_key = None
                    for key, keywords in SGS_OPTIMIZED_MAP.items():
                        if any(kw.lower() in row_str.lower() for kw in keywords):
                            if key == "PFOS" and re.search(r"(?i)(Total|PFOSF|Derivative)", row_str): continue
                            if key in ['F', 'Cl', 'Br', 'I'] and not re.search(r"\((F|Cl|Br|I)-?\)", row_str): continue
                            matched_key = key; break
                    
                    is_pbb = re.search(PBB_SUBITEMS, row_str); is_pbde = re.search(PBDE_SUBITEMS, row_str)
                    if not matched_key and not is_pbb and not is_pbde: continue

                    target_val_str = ""
                    if result_col_idx != -1 and result_col_idx < len(row): target_val_str = str(row[result_col_idx])
                    else:
                        for c_idx in range(len(row)-1, -1, -1):
                            if c_idx == limit_col_idx or c_idx == mdl_col_idx: continue
                            if row[c_idx]:
                                cell_s = str(row[c_idx]).strip()
                                if cell_s.lower() in ["mg/kg", "ppm", "%"]: continue
                                target_val_str = cell_s; break
                    
                    cleaned_val = clean_value(target_val_str)
                    if matched_key:
                        current_val = result.get(matched_key)
                        if get_value_priority(cleaned_val) > get_value_priority(current_val): result[matched_key] = cleaned_val
                    elif is_pbb and isinstance(cleaned_val, (int, float)): pbb_found = True; pbb_sum += cleaned_val
                    elif is_pbde and isinstance(cleaned_val, (int, float)): pbde_found = True; pbde_sum += cleaned_val

    if "PFAS" in first_page_text: result["PFAS"] = "REPORT"
    result["PBBs"] = pbb_sum if pbb_found and pbb_sum > 0 else "N.D."
    result["PBDEs"] = pbde_sum if pbde_found and pbde_sum > 0 else "N.D."
    return result

# ==========================================
# 5. CTI 與 INTERTEK 模組 (維持原樣)
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
                res_idx = -1
                for i, col in enumerate(table): # Check first row
                    if col and re.search(r"(?i)(Result|结果)", str(col)): res_idx = i; break
                if res_idx == -1: # Fallback
                     for i, col in enumerate(table):
                        if col and re.search(r"(?i)(MDL|LOQ|Limit)", str(col)): res_idx = i - 1 if i > 0 else i + 1; break
                if res_idx == -1: continue
                for row in table[1:]:
                    if len(row) <= res_idx: continue
                    row_str = " ".join([str(c) for c in row if c])
                    if re.search(r"(?i)(PFOA|Perfluorooctanoic)", row_str): continue
                    if "PFAS" in row_str and not result['PFAS']: result['PFAS'] = "REPORT"
                    val = clean_value(row[res_idx])
                    for pat, key in UNIFIED_REGEX_MAP.items():
                        if re.search(pat, row_str):
                            if key == "PFOS" and re.search(r"(?i)(Total|PFOSF)", row_str): continue
                            if val is not None:
                                cur = result.get(key)
                                if cur is None or cur == "N.D.": result[key] = val
                                elif isinstance(val, (int,float)) and isinstance(cur, (int,float)): result[key] = max(val, cur)
                            break
                    if re.search(PBB_SUBITEMS, row_str): pbb_found = True; pbb_sum += val if isinstance(val, (int,float)) else 0
                    if re.search(PBDE_SUBITEMS, row_str): pbde_found = True; pbde_sum += val if isinstance(val, (int,float)) else 0
    if "PFAS" in first_page_text: result["PFAS"] = "REPORT"
    result["PBBs"] = pbb_sum if pbb_found and pbb_sum > 0 else "N.D."
    result["PBDEs"] = pbde_sum if pbde_found and pbde_sum > 0 else "N.D."
    return result

def parse_intertek(pdf_obj, full_text, first_page_text):
    result = {k: None for k in TARGET_ITEMS if k not in ['FILENAME', 'DATE']}
    result['PFAS'] = ""; result['DATE'] = ""
    lines = first_page_text.split('\n')
    for line in lines[:25]:
        match = re.search(r"(?i)(?:Date|Issue Date)\s*[:：]?\s*([A-Za-z]{3}\s+\d{1,2},?\s*\d{4}|\d{4}[.\s]+\d{1,2}[.\s]+\d{1,2})", line)
        if match: result['DATE'] = clean_date_str(match.group(1)); break
    pbb_sum = 0; pbde_sum = 0; pbb_found = False; pbde_found = False
    with pdfplumber.open(pdf_obj) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                if not table: continue
                mdl_idx = -1
                for i, col in enumerate(table):
                    if col and re.search(r"(?i)(MDL|LOQ|Detection)", str(col)): mdl_idx = i; break
                if mdl_idx == -1: continue
                res_idx = -1
                if len(table) > 1: # Guess result based on MDL position
                     if mdl_idx + 1 < len(table) and re.search(r"(?i)Result", str(table[mdl_idx+1])): res_idx = mdl_idx + 1
                     elif mdl_idx - 1 >= 0: res_idx = mdl_idx - 1
                if res_idx == -1: continue
                for row in table[1:]:
                    if len(row) <= res_idx: continue
                    row_str = " ".join([str(c) for c in row if c])
                    val = clean_value(row[res_idx])
                    if re.search(r"(?i)(PFOA|Perfluorooctanoic)", row_str): continue
                    if "PFAS" in row_str and not result['PFAS']: result['PFAS'] = "REPORT"
                    for pat, key in UNIFIED_REGEX_MAP.items():
                        if re.search(pat, row_str):
                            if key == "PFOS" and re.search(r"(?i)(Total|PFOSF)", row_str): continue
                            if val is not None:
                                cur = result.get(key)
                                if cur is None or cur == "N.D.": result[key] = val
                                elif isinstance(val, (int,float)) and isinstance(cur, (int,float)): result[key] = max(val, cur)
                            break
                    if re.search(PBB_SUBITEMS, row_str): pbb_found = True; pbb_sum += val if isinstance(val, (int,float)) else 0
                    if re.search(PBDE_SUBITEMS, row_str): pbde_found = True; pbde_sum += val if isinstance(val, (int,float)) else 0
    if "PFAS" in first_page_text: result["PFAS"] = "REPORT"
    result["PBBs"] = pbb_sum if pbb_found and pbb_sum > 0 else "N.D."
    result["PBDEs"] = pbde_sum if pbde_found and pbde_sum > 0 else "N.D."
    return result

# ==========================================
# 6. 主程式 (識別廠商邏輯更新)
# ==========================================
def identify_vendor(first_page_text):
    text = first_page_text.lower()
    if "intertek" in text: return "INTERTEK"
    if "cti" in text or "华测" in text: return "CTI"
    if "sgs" in text:
        # 新增判斷：SGS 馬來西亞
        if "malaysia" in text: return "SGS_MALAYSIA"
        return "SGS"
    return "UNKNOWN"

def main():
    st.set_page_config(page_title="化學報告自動彙整系統 v7.0 (Malaysia Support)", layout="wide")
    st.title("🧪 化學測試報告自動彙整系統 v7.0")
    
    st.markdown("""
    **版本更新 v7.0：**
    - **新增 SGS 馬來西亞版支援：** 針對 Result 欄位在中間及日期格式 (January) 進行優化。
    - **SGS 標準版/CTI/Intertek：** 邏輯維持不變。
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
                        if len(pdf.pages) == 0: bucket_error.append(file.name); continue
                        
                        # [關鍵修正] 讀取第一頁
                        first_page_text = pdf.pages.extract_text()
                        if not first_page_text: bucket_error.append(f"{file.name} (無法讀取)"); continue
                        
                        full_text = ""
                        for page in pdf.pages:
                            txt = page.extract_text()
                            if txt: full_text += txt + "\n"
                        
                        vendor = identify_vendor(first_page_text)
                        data = None
                        
                        # 分流處理
                        if vendor == "SGS_MALAYSIA":
                            data = parse_sgs_malaysia(file, full_text, first_page_text)
                        elif vendor == "SGS":
                            data = parse_sgs_standard(file, full_text, first_page_text)
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
                cols = ["FILENAME", "DATE"] + [c for c in TARGET_ITEMS if c not in ["FILENAME", "DATE"]]
                avail_cols = [c for c in cols if c in df_final.columns]
                df_final = df_final[avail_cols]

                st.success(f"✅ 成功處理 {len(valid_results)} 份報告：")
                st.dataframe(df_final)

                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    df_final.to_excel(writer, index=False, sheet_name='Summary')
                output.seek(0)

                st.download_button(
                    label="📥 下載 Excel",
                    data=output,
                    file_name=f"Report_Summary_{pd.Timestamp.now().strftime('%Y%m%d')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            else:
                st.warning("未提取到有效數據。")

            if bucket_unknown or bucket_error:
                st.divider()
                st.subheader("⚠️ 異常報告")
                if bucket_unknown:
                    for name in bucket_unknown: st.write(f"- 🧪 未識別廠商: {name}")
                if bucket_error:
                    for name in bucket_error: st.write(f"- 🔴 錯誤: {name}")

if __name__ == "__main__":
    main()
