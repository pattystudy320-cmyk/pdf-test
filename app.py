import streamlit as st
import pdfplumber
import pandas as pd
import re
import io
from dateutil import parser

# ==========================================
# 0. 強制清除快取 (最優先執行)
# ==========================================
try:
    st.cache_data.clear()
except:
    try:
        st.experimental_memo.clear()
    except:
        pass

# ==========================================
# 1. 全局配置
# ==========================================

TARGET_ITEMS = [
    "Pb", "Cd", "Hg", "Cr6+", "PBBs", "PBDEs",
    "DEHP", "DBP", "BBP", "DIBP",
    "F", "Cl", "Br", "I",
    "PFOS", "PFAS", "DATE", "FILENAME"
]

# SGS 專用優化字典
SGS_OPTIMIZED_MAP = {
    'Pb': ['Lead', 'Pb', '鉛', '铅'],
    'Cd': ['Cadmium', 'Cd', '鎘', '镉'],
    'Hg': ['Mercury', 'Hg', '汞'],
    'Cr6+': ['Hexavalent Chromium', 'Cr(VI)', '六價鉻', '六价铬'],
    'PBBs': ['Polybrominated biphenyls', 'PBB', '多溴聯苯', '多溴联苯', 'Sum of PBBs'],
    'PBDEs': ['Polybrominated diphenyl ethers', 'PBDE', '多溴二苯醚', 'Sum of PBDEs'],
    'DEHP': ['Bis(2-ethylhexyl) phthalate', 'DEHP', '鄰苯二甲酸二(2-乙基己基)酯', 'Di(2-ethylhexyl) phthalate'],
    'DBP': ['Dibutyl phthalate', 'DBP', '鄰苯二甲酸二丁酯'],
    'BBP': ['Butyl benzyl phthalate', 'BBP', '鄰苯二甲酸丁苄酯'],
    'DIBP': ['Diisobutyl phthalate', 'DIBP', '鄰苯二甲酸二異丁酯'],
    'F': ['Fluorine', '氟'],
    'Cl': ['Chlorine', '氯'],
    'Br': ['Bromine', '溴'],
    'I': ['Iodine', '碘'],
    'PFOS': ['Perfluorooctane sulfonic acid', 'PFOS', '全氟辛烷磺酸', 'Perfluorooctane Sulfonates'],
    'PFAS': ['PFAS'] 
}

# 完整黑名單 (字串)
SGS_IGNORE_LIST = ['2', '5', '8', '10', '50', '100', '1000']

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
    
    # 英文月份轉換
    for mon, digit in MONTH_MAP.items():
        if mon in clean_str:
            clean_str = clean_str.replace(mon, digit)
            break
            
    clean_str = clean_str.replace("年", "/").replace("月", "/").replace("日", "")
    # 移除 Page 1 of 16
    clean_str = re.split(r"(Page|頁)", clean_str, flags=re.IGNORECASE)[0]
    
    try:
        dt = parser.parse(clean_str, fuzzy=True)
        return dt.strftime("%Y/%m/%d")
    except:
        return "1900/01/01"

def clean_value(val_str):
    if not val_str: return None
    val_str = str(val_str).strip()
    
    if re.search(r"\b\d{2,}-\d{2,}-\d{2,}\b", val_str): return None 
    if len(val_str) > 20 and not re.search(r"(negative|positive|n\.d\.)", val_str, re.I): return None

    if re.search(r"(?i)(n\.?d\.?|not detected|<)", val_str): return "N.D."
    if re.search(r"(?i)(negative|阴性|陰性)", val_str): return "NEGATIVE"
    if re.search(r"(?i)(positive|阳性|陽性)", val_str): return "POSITIVE"
    
    match = re.search(r"(\d+\.?\d*)", val_str)
    if match: return float(match.group(1))
    return None

def get_value_priority(val):
    if isinstance(val, (int, float)): return (3, val)
    if val in ["NEGATIVE", "POSITIVE"]: return (2, 0)
    if val == "N.D.": return (1, 0)
    return (0, 0)

# ==========================================
# 3. SGS 解析模組
# ==========================================

def parse_sgs(pdf_obj, full_text, first_page_text):
    result = {k: None for k in SGS_OPTIMIZED_MAP.keys()}
    result['PFAS'] = ""
    result['DATE'] = ""

    # --- 1. 日期抓取 (修正版：以 Date: 切割) ---
    lines = first_page_text.split('\n')
    for line in lines[:20]: 
        # 必須包含 Date 關鍵字，且排除 Received/Testing
        if re.search(r"(?i)(Date|日期)\s*[:：]", line) and not re.search(r"(?i)(Received|Receiving|Testing|Period|接收|周期)", line):
            try:
                # 關鍵修正：使用 'Date' 或 '日期' 進行切割，丟棄前面的 Report No.
                parts = re.split(r"(?i)(Date|日期)\s*[:：]", line, 1)
                if len(parts) > 2:
                    date_content = parts[-1].strip() # 取最後一部分
                    result['DATE'] = clean_date_str(date_content)
                    if result['DATE'] != "1900/01/01":
                        break
            except:
                continue
    
    # --- 2. 數據抓取 ---
    pbb_sum = 0; pbde_sum = 0; pbb_found = False; pbde_found = False
    
    with pdfplumber.open(pdf_obj) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                if not table: continue
                
                for row in table: 
                    row_str = " ".join([str(c) for c in row if c]).replace("\n", " ")
                    
                    if re.search(r"(?i)(PFOA|Perfluorooctanoic\s*Acid|全氟辛酸)", row_str):
                        continue

                    if "PFAS" in row_str and not result['PFAS']:
                        result['PFAS'] = "REPORT"

                    # 識別測項
                    matched_key = None
                    for key, keywords in SGS_OPTIMIZED_MAP.items():
                        if any(kw.lower() in row_str.lower() for kw in keywords):
                            if key == "PFOS" and re.search(r"(?i)(Total|PFOSF|Derivative|总和|衍生物)", row_str):
                                continue
                            # 鹵素防呆
                            if key in ['F', 'Cl', 'Br', 'I'] and not re.search(r"\((F|Cl|Br|I)-?\)", row_str):
                                continue
                            matched_key = key
                            break
                    
                    # 加總項目識別
                    # PBBs/PBDEs 正則表達式
                    PBB_SUBITEMS = r"(?i)(Monobromobiphenyl|Dibromobiphenyl|Tribromobiphenyl|Tetrabromobiphenyl|Pentabromobiphenyl|Hexabromobiphenyl|Heptabromobiphenyl|Octabromobiphenyl|Nonabromobiphenyl|Decabromobiphenyl|一溴联苯|二溴联苯|三溴联苯|四溴联苯|五溴联苯|六溴联苯|七溴联苯|八溴联苯|九溴联苯|十溴联苯)"
                    PBDE_SUBITEMS = r"(?i)(Monobromodiphenyl ether|Dibromodiphenyl ether|Tribromodiphenyl ether|Tetrabromodiphenyl ether|Pentabromodiphenyl ether|Hexabromodiphenyl ether|Heptabromodiphenyl ether|Octabromodiphenyl ether|Nonabromodiphenyl ether|Decabromodiphenyl ether|一溴二苯醚|二溴二苯醚|三溴二苯醚|四溴二苯醚|五溴二苯醚|六溴二苯醚|七溴二苯醚|八溴二苯醚|九溴二苯醚|十溴二苯醚)"
                    
                    is_pbb = re.search(PBB_SUBITEMS, row_str)
                    is_pbde = re.search(PBDE_SUBITEMS, row_str)

                    if not matched_key and not is_pbb and not is_pbde:
                        continue 

                    # 抓取數值
                    value_candidates = re.findall(r"(?i)(N\.?D\.?|Negative|Positive|<\s*\d+\.?\d*|\b\d+\.?\d*\b)", row_str)
                    found_val = None
                    
                    # 由右向左
                    for raw_val in reversed(value_candidates):
                        # 1. 字串清洗 (移除 <, 移除單位)
                        check_str = raw_val.strip()
                        if "<" in check_str: check_str = check_str.replace("<", "").strip()
                        # 簡單剝離單位 (例如 1000mg)
                        check_str = re.split(r"[a-zA-Z]", check_str)[0] 
                        
                        # 2. 黑名單字串比對
                        if check_str in SGS_IGNORE_LIST:
                            continue 
                        
                        # 3. 轉數值後比對 (防漏)
                        cleaned = clean_value(raw_val)
                        if cleaned is None: continue
                        
                        if isinstance(cleaned, (int, float)):
                            if int(cleaned) == cleaned and str(int(cleaned)) in SGS_IGNORE_LIST:
                                continue

                        found_val = cleaned
                        break 
                    
                    if found_val is not None:
                        if matched_key:
                            current_val = result[matched_key]
                            if current_val is None or current_val == "N.D.":
                                result[matched_key] = found_val
                            elif isinstance(found_val, (int, float)) and isinstance(current_val, (int, float)):
                                result[matched_key] = max(found_val, current_val)
                        
                        if is_pbb:
                            pbb_found = True
                            if isinstance(found_val, (int, float)): pbb_sum += found_val
                        if is_pbde:
                            pbde_found = True
                            if isinstance(found_val, (int, float)): pbde_sum += found_val

    if "PFAS" in first_page_text: result["PFAS"] = "REPORT"
    result["PBBs"] = pbb_sum if pbb_found and pbb_sum > 0 else "N.D."
    result["PBDEs"] = pbde_sum if pbde_found and pbde_sum > 0 else "N.D."
    return result

# ==========================================
# 4. CTI/Intertek (Regex版)
# ==========================================
# (此處為保持程式碼簡潔，沿用上版邏輯，省略重複部分)
# 實際執行時請務必包含完整的 parse_cti 和 parse_intertek 函數
# 為求完整，這裡重新列出

def parse_cti(pdf_obj, full_text, first_page_text):
    # CTI/Intertek 通用字典 (Regex版)
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

    result = {k: None for k in TARGET_ITEMS if k not in ['FILENAME', 'DATE']}
    result['PFAS'] = ""
    result['DATE'] = ""
    
    lines = first_page_text.split('\n')
    date_pat = re.compile(r"(20\d{2}[\.\-/]\d{2}[\.\-/]\d{2}|[A-Za-z]{3}\.?\s+\d{1,2},?\s+20\d{2})")
    for line in reversed(lines):
        if re.search(r"(?i)(Received|Testing|Period|Rev\.|Revis)", line): continue
        match = date_pat.search(line)
        if match:
            result['DATE'] = clean_date_str(match.group(0))
            break
            
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
                    
                    raw_val = str(row[res_idx]).strip()
                    val = clean_value(raw_val)
                    if re.search(r"^0\d+$", raw_val) or (re.search(r"^\d{1,3}$", raw_val) and "mg/kg" not in raw_val):
                        if row_idx + 1 < len(table[1:]):
                            next_row = table[1:][row_idx+1]
                            if len(next_row) > res_idx: val = clean_value(next_row[res_idx])
                    
                    if "PFAS" in row_str and not result.get('PFAS'): result['PFAS'] = "REPORT"

                    for pat, key in UNIFIED_REGEX_MAP.items():
                        if re.search(pat, row_str):
                            if key == "PFOS" and re.search(r"(?i)(Total|PFOSF|Derivative|总和|衍生物)", row_str): continue
                            if val is not None:
                                current_val = result.get(key)
                                if current_val is None or current_val == "N.D.": result[key] = val
                                elif isinstance(val, (int, float)) and isinstance(current_val, (int, float)): result[key] = max(val, current_val)
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
    # CTI/Intertek 通用字典 (Regex版)
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

    result = {k: None for k in TARGET_ITEMS if k not in ['FILENAME', 'DATE']}
    result['PFAS'] = ""
    result['DATE'] = ""

    lines = first_page_text.split('\n')
    date_pat = r"(?i)(?:Date|Issue Date|발행일자)\s*[:：]?\s*([A-Za-z]{3}\s+\d{1,2},?\s*\d{4}|\d{4}[.\s]+\d{1,2}[.\s]+\d{1,2})"
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
                header = table[0]
                mdl_idx = -1
                for i, col in enumerate(header):
                    if col and re.search(r"(?i)(MDL|RL|Limit of Detection|검출한계)", str(col)):
                        mdl_idx = i; break
                if mdl_idx == -1: continue 
                
                res_idx = -1
                if len(table) > 1:
                    row1 = table[1]
                    left_val = str(row1[mdl_idx-1]) if mdl_idx > 0 else ""
                    right_val = str(row1[mdl_idx+1]) if mdl_idx + 1 < len(row1) else ""
                    if re.search(r"(?i)(N\.?D|Negative|<)", left_val): res_idx = mdl_idx - 1
                    elif re.search(r"(?i)(N\.?D|Negative|<)", right_val): res_idx = mdl_idx + 1
                    elif mdl_idx + 1 < len(header) and re.search(r"(?i)(Result|결과)", str(header[mdl_idx+1])): res_idx = mdl_idx + 1
                    elif mdl_idx - 1 >= 0 and re.search(r"(?i)(Result|结果)", str(header[mdl_idx-1])): res_idx = mdl_idx - 1
                if res_idx == -1: continue

                for row in table[1:]:
                    if len(row) <= res_idx: continue
                    row_str = " ".join([str(c) for c in row if c]).replace("\n", " ")
                    val = clean_value(row[res_idx])
                    if "PFAS" in row_str and not result.get('PFAS'): result['PFAS'] = "REPORT"
                    for pat, key in UNIFIED_REGEX_MAP.items():
                        if re.search(pat, row_str):
                            if val is not None:
                                current_val = result.get(key)
                                if current_val is None or current_val == "N.D.": result[key] = val
                                elif isinstance(val, (int, float)) and isinstance(current_val, (int, float)): result[key] = max(val, current_val)
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

def aggregate_reports(valid_results):
    if not valid_results: return pd.DataFrame()
    final_row = {k: None for k in TARGET_ITEMS}
    
    sorted_by_pb = sorted(
        valid_results, 
        key=lambda x: (
            get_value_priority(x.get("Pb"))[0],
            get_value_priority(x.get("Pb"))[1],
            x.get("DATE", "1900/01/01")
        ), 
        reverse=True
    )
    final_row["FILENAME"] = sorted_by_pb[0]["FILENAME"]
    all_dates = [r.get("DATE", "1900/01/01") for r in valid_results if r.get("DATE")]
    final_row["DATE"] = max(all_dates) if all_dates else "Unknown"

    for key in TARGET_ITEMS:
        if key in ["FILENAME", "DATE"]: continue
        best_val = None
        for res in valid_results:
            val = res.get(key)
            if get_value_priority(val) > get_value_priority(best_val):
                best_val = val
        final_row[key] = best_val
    return pd.DataFrame([final_row])

def main():
    st.set_page_config(page_title="化學報告自動彙整系統 v4.9 (Final Debug)", layout="wide")
    st.title("🧪 化學測試報告自動彙整系統 v4.9 (Final Debug)")
    st.markdown("""
    **版本修正：**
    1. **日期精準切割**：改用 `Date:` 關鍵字切割，徹底解決發行日抓錯問題。
    2. **黑名單增強**：加入 `split` 邏輯，防止單位沾黏導致比對失敗 (解決 1000 殘留問題)。
    3. **快取歸零**：強制刷新運算結果。
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
                        if len(pdf.pages) == 0:
                            bucket_error.append(file.name)
                            continue
                        
                        first_page_text = pdf.pages[0].extract_text()
                        if not first_page_text:
                            bucket_error.append(f"{file.name} (無法讀取)")
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
                        if "SGS" in first_page_text:
                            data = parse_sgs(file, full_text, first_page_text)
                        else:
                            bucket_unknown.append(file.name)
                            continue

                    if data:
                        data["FILENAME"] = file.name
                        valid_results.append(data)
                    else:
                        bucket_error.append(f"{file.name} (解析失敗)")

                except Exception as e:
                    bucket_error.append(f"{file.name} (錯誤: {str(e)})")
                
                progress_bar.progress((i + 1) / len(uploaded_files))

            status_text.text("分析完成！")

            if valid_results:
                df_final = aggregate_reports(valid_results)
                cols = ["FILENAME", "DATE"] + [c for c in TARGET_ITEMS if c not in ["FILENAME", "DATE"]]
                df_final = df_final[cols]
                
                st.success(f"✅ 成功處理 {len(valid_results)} 份報告：")
                st.dataframe(df_final)
                
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    df_final.to_excel(writer, index=False, sheet_name='Summary')
                output.seek(0)
                
                st.download_button(
                    label="📥 下載 Excel",
                    data=output,
                    file_name=f"Merged_Report_{pd.Timestamp.now().strftime('%Y%m%d')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            else:
                st.warning("未提取到有效數據。")

            if bucket_unknown or bucket_error:
                st.divider()
                st.subheader("⚠️ 異常報告")
                if bucket_unknown:
                    for name in bucket_unknown: st.write(f"- 🟡 未識別: {name}")
                if bucket_error:
                    for name in bucket_error: st.write(f"- 🔴 錯誤: {name}")

if __name__ == "__main__":
    main()
