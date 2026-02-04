import streamlit as st
import pdfplumber
import pandas as pd
import io
import re
from datetime import datetime

# =============================================================================
# 1. [Core 1] v63.15 經典關鍵字庫 (給 CTI & 標準 SGS 使用)
# =============================================================================

OUTPUT_COLUMNS = [
    "Pb", "Cd", "Hg", "Cr6+", "PBB", "PBDE", 
    "DEHP", "BBP", "DBP", "DIBP", 
    "PFOS", "PFAS", "F", "CL", "BR", "I", 
    "日期", "檔案名稱"
]

# v63.15 設定：包含 PFOS 短關鍵字
SIMPLE_KEYWORDS = {
    "Pb": ["Lead", "鉛", "Pb"],
    "Cd": ["Cadmium", "鎘", "Cd"],
    "Hg": ["Mercury", "汞", "Hg"],
    "Cr6+": ["Hexavalent Chromium", "六價鉻", "Cr(VI)", "Chromium VI", "Hexavalent Chromium"],
    "DEHP": ["DEHP", "Di(2-ethylhexyl) phthalate", "Bis(2-ethylhexyl) phthalate"],
    "BBP": ["BBP", "Butyl benzyl phthalate"],
    "DBP": ["DBP", "Dibutyl phthalate"],
    "DIBP": ["DIBP", "Diisobutyl phthalate"],
    "PFOS": ["Perfluorooctane sulfonates", "Perfluorooctane sulfonate", "Perfluorooctane sulfonic acid", "全氟辛烷磺酸", "Perfluorooctane Sulfonamide", "PFOS and its salts", "PFOS 及其盐", "PFOS"],
    "F": ["Fluorine", "氟"],
    "CL": ["Chlorine", "氯"],
    "BR": ["Bromine", "溴"],
    "I": ["Iodine", "碘", "lodine"]
}

# v63.15 設定：包含所有單項 PBB/PBDE
GROUP_KEYWORDS = {
    "PBB": [
        "Polybrominated Biphenyls", "PBBs", "Sum of PBBs", "多溴聯苯總和", "多溴聯苯之和",
        "Polybromobiphenyl", "Monobromobiphenyl", "Dibromobiphenyl", "Tribromobiphenyl", 
        "Tetrabromobiphenyl", "Pentabromobiphenyl", "Hexabromobiphenyl", 
        "Heptabromobiphenyl", "Octabromobiphenyl", "Nonabromobiphenyl", 
        "Decabromobiphenyl", "Monobrominated", "Dibrominated", "Tribrominated", 
        "Tetrabrominated", "Pentabrominated", "Hexabrominated", "Heptabrominated", 
        "Octabrominated", "Nonabrominated", "Decabrominated"
    ],
    "PBDE": [
        "Polybrominated Diphenyl Ethers", "PBDEs", "Sum of PBDEs", "多溴聯苯醚總和", "多溴二苯醚之和",
        "Polybromodiphenyl ether", "Monobromodiphenyl ether", "Dibromodiphenyl ether", "Tribromodiphenyl ether",
        "Tetrabromodiphenyl ether", "Pentabromodiphenyl ether", "Hexabromodiphenyl ether",
        "Heptabromodiphenyl ether", "Octabromodiphenyl ether", "Nonabromodiphenyl ether",
        "Decabromodiphenyl ether", "Monobrominated Diphenyl", "Dibrominated Diphenyl", "Tribrominated Diphenyl",
        "Tetrabrominated Diphenyl", "Pentabrominated Diphenyl", "Hexabrominated Diphenyl",
        "Heptabrominated Diphenyl", "Octabrominated Diphenyl", "Nonabrominated Diphenyl",
        "Decabrominated Diphenyl"
    ]
}

PFAS_SUMMARY_KEYWORDS = ["Per- and Polyfluoroalkyl Substances", "PFAS", "全氟/多氟烷基物質"]
MSDS_HEADER_KEYWORDS = ["content", "composition", "concentration", "含量", "成分"]

# =============================================================================
# 2. [Core 2] v63.28 馬來西亞專用設定 (完全隔離)
# =============================================================================

MY_ITEM_RULES = {
    "Pb": r"Lead\s*\(Pb\)",
    "Cd": r"Cadmium\s*\(Cd\)",
    "Hg": r"Mercury\s*\(Hg\)",
    "Cr6+": r"Hexavalent Chromium",
    "PBB": r"Sum of PBBs",
    "PBDE": r"Sum of PBDEs",
    "DEHP": r"DEHP|Di\(2-ethylhexyl\)\s*phthalate",
    "BBP": r"BBP|Benzyl\s*butyl\s*phthalate",
    "DBP": r"DBP|Dibutyl\s*phthalate",
    "DIBP": r"DIBP|Diisobutyl\s*phthalate",
    "F": r"\bFluorine\b",
    "CL": r"\bChlorine\b",
    "BR": r"\bBromine\b",
    "I": r"\bIodine\b",
    "PFOS": r"PFOS",
    "PFAS": r"PFAS"
}

MY_MDL_BLOCKLIST = {
    "Pb": [2.0], "Cd": [2.0], "Hg": [2.0], "Cr6+": [8.0, 10.0],
    "F": [50.0], "CL": [50.0], "BR": [50.0], "I": [50.0],
    "DEHP": [50.0], "BBP": [50.0], "DBP": [50.0], "DIBP": [50.0]
}

MONTH_MAP = {
    'jan': 1, 'january': 1, 'feb': 2, 'february': 2, 'mar': 3, 'march': 3,
    'apr': 4, 'april': 4, 'may': 5, 'jun': 6, 'june': 6, 'jul': 7, 'july': 7,
    'aug': 8, 'august': 8, 'sep': 9, 'september': 9, 'sept': 9, 'oct': 10, 'october': 10,
    'nov': 11, 'november': 11, 'dec': 12, 'december': 12
}

# =============================================================================
# 3. 共用輔助函式
# =============================================================================

def clean_text(text):
    if not text: return ""
    return str(text).replace('\n', ' ').strip()

def is_valid_date(dt):
    if 2000 <= dt.year <= 2030: return True
    return False

def is_suspicious_limit_value(val):
    try:
        n = float(val)
        # v63.35 Fix: 加入小數點 MDL 黑名單，防止 SGS 誤抓 0.003
        if n in [1000.0, 100.0, 50.0, 25.0, 10.0, 5.0, 2.0, 0.003, 0.005, 0.01, 0.05, 0.050, 0.0005]: return True
        return False
    except: return False

def parse_value_priority(value_str):
    raw_val = clean_text(value_str)
    if "(" in raw_val and ")" in raw_val:
        if re.search(r"\(\d+\)", raw_val):
            raw_val = raw_val.split("(")[0].strip()
    val = raw_val.replace("mg/kg", "").replace("ppm", "").replace("%", "").replace("µg/cm²", "").strip()
    
    if not val: return (0, 0, "")
    val_lower = val.lower()
    
    if val_lower in ["result", "limit", "mdl", "loq", "rl", "unit", "method", "004", "001", "no.1", "---", "-", "limits", "n.a.", "/"]: 
        return (0, 0, "")
    if re.search(r"\d+-\d+-\d+", val): return (0, 0, "") 
    
    num_only_match = re.search(r"^([\d\.]+)$", val)
    if num_only_match:
        if is_suspicious_limit_value(num_only_match.group(1)): return (0, 0, "")

    if "nd" in val_lower or "n.d." in val_lower or "<" in val_lower: return (1, 0, "N.D.")
    if "negative" in val_lower or "陰性" in val_lower: return (2, 0, "NEGATIVE")
    
    num_match = re.search(r"^([\d\.]+)(.*)$", val)
    if num_match:
        try:
            number = float(num_match.group(1))
            return (3, number, val)
        except: pass
    return (0, 0, val)

def format_output_value(val):
    """v63.35: 格式化輸出，如果是整數 float 則轉 int，否則保留"""
    try:
        f = float(val)
        if f.is_integer():
            return str(int(f))
        return str(f)
    except:
        return str(val)

def identify_company(text):
    txt = text.lower()
    if "sgs" in txt: return "SGS"
    if "intertek" in txt: return "INTERTEK"
    if "cti" in txt or "centre testing" in txt: return "CTI"
    if "ctic" in txt: return "CTIC"
    return "OTHERS"

# =============================================================================
# 4. [Core 2] SGS 馬來西亞專用引擎 (v63.28 邏輯 - 保持不動)
# =============================================================================

def extract_date_malaysia_v7(text):
    match = re.search(r"(REPORTED DATE|TEST REPORT REPORTED DATE)\s*[:\-]?\s*([^\n]+)", text, re.IGNORECASE)
    if match:
        date_str = match.group(2).strip()
        try:
            date_str = re.sub(r"[^a-zA-Z0-9\s]", " ", date_str)
            parts = date_str.split()
            d, m, y = None, None, None
            for p in parts:
                if p.isdigit():
                    if len(p) == 4: y = int(p)
                    elif int(p) <= 31: d = int(p)
                elif p.lower() in MONTH_MAP:
                    m = MONTH_MAP[p.lower()]
            if d and m and y:
                dt = datetime(y, m, d)
                if is_valid_date(dt): return dt
        except: pass
    return None

def extract_result_malaysia_v7(text, keyword, item_name):
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.search(keyword, line, re.IGNORECASE):
            if item_name == "DEHP":
                context = " ".join(lines[i:i+4])
            else:
                context = " ".join(lines[i:i+2])

            if item_name == "DEHP":
                context = re.sub(r"2-ethylhexyl", " ", context, flags=re.IGNORECASE)
                context = re.sub(r"Di\(2-", " ", context, flags=re.IGNORECASE)

            context = re.sub(r"mg/kg|ppm|%|wt%", " ", context, flags=re.IGNORECASE)
            context = re.sub(r"\(?CAS\s*No\.?[\s\d-]+\)?", " ", context, flags=re.IGNORECASE)
            context = re.sub(r"IEC\s*62321[-\d:+A]*", " ", context, flags=re.IGNORECASE)
            context = re.sub(r"\b(19|20)\d{2}\b", " ", context) 
            context = re.sub(r"(Max|Limit|MDL|LOQ)\s*\d+(\.\d+)?", " ", context, flags=re.IGNORECASE)

            nd_pattern = r"(\bN\s*\.?\s*D\s*\.?\b)|(Not\s*Detected)"
            if re.search(nd_pattern, context, re.IGNORECASE): return "N.D."
            if re.search(r"NEGATIVE", context, re.IGNORECASE): return "NEGATIVE"

            nums = re.findall(r"\b\d+(?:\.\d+)?\b", context)
            if not nums: return "N.D."

            final_val = None
            if item_name in ["PBB", "PBDE"]:
                final_val = nums[0]
            else:
                if len(nums) >= 2:
                    candidate = nums[0]
                    try:
                        f_val = float(candidate)
                        if 1990 <= f_val <= 2030 and f_val.is_integer(): candidate = nums[1]
                    except: pass
                    final_val = candidate
                elif len(nums) == 1:
                    return "N.D."

            if final_val:
                try:
                    val_float = float(final_val)
                    if item_name in MY_MDL_BLOCKLIST:
                        if val_float in MY_MDL_BLOCKLIST[item_name]: return "N.D."
                    return final_val
                except: pass
    return ""

def process_malaysia_engine(pdf, filename):
    data_pool = {key: [] for key in OUTPUT_COLUMNS if key not in ["日期", "檔案名稱"]}
    full_text = ""
    for p in pdf.pages: full_text += (p.extract_text() or "") + "\n"
    report_date = extract_date_malaysia_v7(pdf.pages[0].extract_text() or "")
    for col_key in OUTPUT_COLUMNS:
        if col_key in ["日期", "檔案名稱"]: continue
        keyword = MY_ITEM_RULES.get(col_key)
        if not keyword: continue
        val = extract_result_malaysia_v7(full_text, keyword, col_key)
        if val:
            prio = parse_value_priority(val)
            if prio[0] > 0:
                data_pool[col_key].append({"priority": prio, "filename": filename})
    date_candidates = []
    if report_date: date_candidates.append((100, report_date))
    return data_pool, date_candidates

# =============================================================================
# 5. [Core 1] CTI 專用引擎 (v63.35 修正: Max Rule + TBBP防呆)
# =============================================================================

def extract_dates_v63_13_global(text):
    candidates = []
    poison_kw = ["received", "receive", "expiry", "valid", "process"]
    backup_kw = ["testing", "period", "test"]
    clean_text_str = re.sub(r'[^a-z0-9]', ' ', text.lower())
    tokens = clean_text_str.split()
    for i in range(len(tokens) - 2):
        t1, t2, t3 = tokens[i], tokens[i+1], tokens[i+2]
        dt = None
        try:
            if t1 in MONTH_MAP and t2.isdigit() and t3.isdigit() and len(t3) == 4:
                m, d, y = MONTH_MAP[t1], int(t2), int(t3)
                dt = datetime(y, m, d)
            elif t1.isdigit() and t2 in MONTH_MAP and t3.isdigit() and len(t3) == 4:
                d, m, y = int(t1), MONTH_MAP[t2], int(t3)
                dt = datetime(y, m, d)
            elif t1.isdigit() and len(t1) == 4 and t2.isdigit() and t3.isdigit():
                y, m, d = int(t1), int(t2), int(t3)
                dt = datetime(y, m, d)
            if dt and is_valid_date(dt):
                start_lookback = max(0, i - 10)
                context_window = tokens[start_lookback : i]
                score = 100 
                if any(p in context_window for p in poison_kw): score = -1000 
                elif any(b in context_window for b in backup_kw): score = 10 
                candidates.append((score, dt))
        except: pass
    return candidates

def process_cti_engine(pdf, filename):
    data_pool = {key: [] for key in OUTPUT_COLUMNS if key not in ["日期", "檔案名稱"]}
    
    text_for_dates = ""
    for p in pdf.pages[:3]: text_for_dates += (p.extract_text() or "") + " " 
    date_candidates = extract_dates_v63_13_global(text_for_dates)

    for page in pdf.pages:
        tables = page.extract_tables()
        for table in tables:
            if not table or len(table) < 2: continue
            
            mdl_col_idx = -1
            item_col_idx = -1
            cols = len(table[0])
            
            # 1. 尋找 MDL 欄位
            for c in range(cols):
                header = clean_text(table[0][c]).lower()
                if "mdl" in header or "loq" in header:
                    mdl_col_idx = c
                    break
            
            if mdl_col_idx == -1:
                for c in range(cols):
                    num_count = 0
                    row_count = 0
                    for r in range(1, len(table)):
                        val = clean_text(table[r][c]).replace("mg/kg", "").strip()
                        if not val: continue
                        row_count += 1
                        if val in ["2", "5", "10", "50", "100", "0.01", "20", "25"]: num_count += 1
                    if row_count > 0 and (num_count / row_count) >= 0.5:
                        mdl_col_idx = c
                        break
            
            if mdl_col_idx == -1: continue 

            # 2. 尋找 Item 欄位
            for c in range(cols):
                header = str(table[0][c]).lower()
                if "item" in header or "項目" in header or "项目" in header:
                    item_col_idx = c
                    break
            if item_col_idx == -1: item_col_idx = 0

            # 3. 多樣品欄位掃描 (Item ~ MDL)
            data_col_indices = []
            for c in range(item_col_idx + 1, mdl_col_idx):
                data_col_indices.append(c)
            
            if not data_col_indices:
                data_col_indices = [mdl_col_idx - 1]

            # 4. 逐行掃描
            for row in table:
                if len(row) <= mdl_col_idx: continue
                item_text = clean_text(row[item_col_idx]).lower()
                
                # [v63.35 Fix] TBBP-A 防呆
                if "tbbp" in item_text or "tetrabromo" in item_text: continue

                # [v63.35 Fix] 多樣品 Max Rule 邏輯
                valid_numbers = []
                has_negative = False
                has_nd = False
                
                for c_idx in data_col_indices:
                    if c_idx < len(row):
                        raw_val = clean_text(row[c_idx])
                        prio = parse_value_priority(raw_val)
                        if prio[0] == 3:
                            valid_numbers.append(prio[1])
                        elif prio[0] == 2:
                            has_negative = True
                        elif prio[0] == 1:
                            has_nd = True
                
                final_prio = (0, 0, "")
                if valid_numbers:
                    max_val = max(valid_numbers) # 取最大值
                    final_prio = (3, max_val, str(max_val))
                elif has_negative:
                    final_prio = (2, 0, "NEGATIVE")
                elif has_nd:
                    final_prio = (1, 0, "N.D.")
                
                if final_prio[0] == 0: continue

                # 關鍵字匹配 (無過濾)
                for key, kws in SIMPLE_KEYWORDS.items():
                    if any(kw.lower() in item_text for kw in kws):
                        data_pool[key].append({"priority": final_prio, "filename": filename})
                        break
                
                for key, kws in GROUP_KEYWORDS.items():
                    if any(kw.lower() in item_text for kw in kws):
                        data_pool[key].append({"priority": final_prio, "filename": filename})
                        break

    return data_pool, date_candidates

# =============================================================================
# 6. [Core 1] SGS 標準引擎 (v63.35 修正: TBBP防呆 + 0.003防呆)
# =============================================================================

def extract_dates_v60(text):
    lines = text.split('\n')
    candidates = []
    bonus_kw = ["report date", "issue date", "date:", "dated", "日期"]
    poison_kw = ["approve", "approved", "receive", "received", "receipt", "period", "expiry", "valid"]
    pat_chinese = r"(20\d{2})\s*年\s*(0?[1-9]|1[0-2])\s*月\s*(3[01]|[12][0-9]|0?[1-9])\s*日"
    pat_ymd = r"(20\d{2})[\.\/-](0?[1-9]|1[0-2])[\.\/-](3[01]|[12][0-9]|0?[1-9])"
    pat_dmy = r"(3[01]|[12][0-9]|0?[1-9])\s+([a-zA-Z]{3,})\s+(20\d{2})"
    pat_mdy = r"([a-zA-Z]{3,})\s+(3[01]|[12][0-9]|0?[1-9])\s+(20\d{2})"
    for line in lines:
        line_lower = line.lower()
        score = 1
        if any(bad in line_lower for bad in poison_kw): score = -100 
        elif any(good in line_lower for good in bonus_kw): score = 100 
        matches_cn = re.finditer(pat_chinese, line)
        for m in matches_cn:
            try:
                dt = datetime.strptime(f"{m.group(1)}-{m.group(2)}-{m.group(3)}", "%Y-%m-%d")
                if is_valid_date(dt): candidates.append((score, dt))
            except: pass
        clean_line = line.replace(".", " ").replace(",", " ").replace("-", " ").replace("/", " ")
        clean_line = clean_line.replace("年", " ").replace("月", " ").replace("日", " ")
        clean_line = " ".join(clean_line.split())
        for pat in [pat_ymd, pat_dmy, pat_mdy]:
            matches = re.finditer(pat, clean_line)
            for m in matches:
                try:
                    dt_str = " ".join(m.groups())
                    for fmt in ["%Y %m %d", "%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y"]:
                        try:
                            dt = datetime.strptime(dt_str, fmt)
                            if is_valid_date(dt): 
                                candidates.append((score, dt))
                                break
                        except: pass
                except: pass
    return candidates

def identify_columns_v60(table, company):
    item_idx = -1
    result_idx = -1
    mdl_idx = -1
    max_scan_rows = min(3, len(table))
    full_header_text = ""
    for r in range(max_scan_rows):
        full_header_text += " ".join([str(c).lower() for c in table[r] if c]) + " "
    is_msds_table = False
    if any(k in full_header_text for k in MSDS_HEADER_KEYWORDS) and "result" not in full_header_text: is_msds_table = True

    for r_idx in range(max_scan_rows):
        row = table[r_idx]
        for c_idx, cell in enumerate(row):
            txt = clean_text(cell).lower()
            if not txt: continue
            if "test item" in txt or "tested item" in txt or "parameter" in txt:
                if item_idx == -1: item_idx = c_idx
            if "mdl" in txt or "loq" in txt:
                if mdl_idx == -1: mdl_idx = c_idx
            if company == "SGS":
                 if ("result" in txt or "結果" in txt or re.search(r"00[1-9]", txt) or re.search(r"[a-zA-Z]\s*\.\s*[a-zA-Z]\d+", txt)):
                    if "cas" not in txt and "method" not in txt and "limit" not in txt:
                        if result_idx == -1: result_idx = c_idx
    
    if result_idx == -1 and company == "SGS" and mdl_idx != -1:
        forbidden_headers = ["unit", "method", "limit", "mdl", "loq", "item", "cas"]
        right_idx = mdl_idx + 1
        if right_idx < len(table[0]):
            header = clean_text(table[0][right_idx]).lower()
            if not any(fb in header for fb in forbidden_headers): result_idx = right_idx
        if result_idx == -1:
            left_idx = mdl_idx - 1
            if left_idx >= 0:
                header = clean_text(table[0][left_idx]).lower()
                if not any(fb in header for fb in forbidden_headers): result_idx = left_idx

    is_reference_table = False
    if is_msds_table or result_idx == -1: is_reference_table = True
    return item_idx, result_idx, is_reference_table, mdl_idx

def parse_text_lines_v60(text, data_pool, file_group_data, filename, company, targets=None):
    lines = text.split('\n')
    for line in lines:
        line_clean = clean_text(line)
        line_lower = line_clean.lower()
        if not line_clean: continue
        if any(bad in line_lower for bad in MSDS_HEADER_KEYWORDS): continue

        matched_simple = None
        for key, keywords in SIMPLE_KEYWORDS.items():
            if targets and key not in targets: continue
            # [v63.35 Fix] TBBP 防呆
            if key == "BBP" and ("tbbp" in line_lower or "tetrabromo" in line_lower): continue

            if key == "Cd" and any(bad in line_lower for bad in ["hbcdd", "cyclododecane", "ecd", "indeno"]): continue 
            if key == "F" and any(bad in line_lower for bad in ["perfluoro", "polyfluoro", "pfos", "pfoa", "全氟"]): continue
            if key == "BR" and any(bad in line_lower for bad in ["polybromo", "hexabromo", "monobromo", "dibromo", "tribromo", "tetrabromo", "pentabromo", "heptabromo", "octabromo", "nonabromo", "decabromo", "multibromo", "pbb", "pbde", "多溴", "六溴", "一溴", "二溴", "三溴", "四溴", "五溴", "七溴", "八溴", "九溴", "十溴", "二苯醚"]): continue
            if key == "Pb" and any(bad in line_lower for bad in ["pbb", "pbde", "polybrominated", "多溴"]): continue
            for kw in keywords:
                if kw.lower() in line_lower and "test item" not in line_lower:
                    matched_simple = key
                    break
            if matched_simple: break
        
        matched_group = None
        if not matched_simple:
            for group_key, keywords in GROUP_KEYWORDS.items():
                if targets and group_key not in targets: continue
                for kw in keywords:
                    if kw.lower() in line_lower:
                        matched_group = group_key
                        break
                if matched_group: break
        
        if matched_simple or matched_group:
            parts = line_clean.split()
            if len(parts) < 2: continue
            found_val = ""
            for part in reversed(parts):
                p_lower = part.lower()
                if p_lower in ["mg/kg", "ppm", "2", "5", "10", "50", "100", "1000", "0.1", "-", "---", "unit", "mdl"]: continue
                if "nd" in p_lower:
                    found_val = "N.D."
                    break
                if re.match(r"^\d+.*$", part): 
                    val_check = part.replace("▲", "").replace("△", "")
                    try:
                        f = float(val_check)
                        if f not in [100.0, 1000.0, 50.0]:
                            found_val = part
                            break
                    except: pass
            if found_val:
                priority = parse_value_priority(found_val)
                if priority[0] == 0: continue
                if matched_simple:
                    data_pool[matched_simple].append({"priority": priority, "filename": filename})
                elif matched_group:
                    file_group_data[matched_group].append(priority)

def process_halogen_block(pdf, filename, data_pool):
    for page in pdf.pages:
        text = (page.extract_text() or "").lower()
        if "halogen" in text:
            tables = page.extract_tables()
            for table in tables:
                if not table or len(table) < 2: continue
                for row in table:
                    clean_row = [clean_text(cell) for cell in row]
                    row_txt = "".join(clean_row).lower()
                    matched_key = None
                    if "fluorine" in row_txt: matched_key = "F"
                    elif "chlorine" in row_txt: matched_key = "CL"
                    elif "bromine" in row_txt: matched_key = "BR"
                    elif "iodine" in row_txt or "lodine" in row_txt: matched_key = "I"
                    if matched_key:
                        result_val = ""
                        for cell in reversed(clean_row):
                            c_lower = cell.lower()
                            if "mg/kg" in c_lower or "ppm" in c_lower or "limit" in c_lower or "unit" in c_lower: continue
                            if "nd" in c_lower or "n.d." in c_lower:
                                result_val = cell
                                break
                            if re.search(r"^\d+(\.\d+)?", cell):
                                if is_suspicious_limit_value(cell): continue
                                result_val = cell
                                break
                        if result_val:
                            priority = parse_value_priority(result_val)
                            if priority[0] > 0:
                                data_pool[matched_key].append({"priority": priority, "filename": filename})

def process_standard_engine(pdf, filename, company):
    data_pool = {key: [] for key in OUTPUT_COLUMNS if key not in ["日期", "檔案名稱"]}
    file_dates_candidates = []
    full_text_content = ""
    first_page_text = (pdf.pages[0].extract_text() or "").lower()
    if "per- and polyfluoroalkyl substances" in first_page_text or "pfas" in first_page_text:
        data_pool["PFAS"].append({"priority": (4, 0, "REPORT"), "filename": filename})

    for p in pdf.pages[:5]:
        txt = p.extract_text() or ""
        full_text_content += txt + "\n"
        file_dates_candidates.extend(extract_dates_v60(txt))

    file_group_data = {key: [] for key in GROUP_KEYWORDS.keys()}

    for page in pdf.pages:
        tables = page.extract_tables()
        for table in tables:
            if not table or len(table) < 2: continue
            item_idx, result_idx, is_skip, mdl_idx = identify_columns_v60(table, company)
            
            force_scan = False
            if is_skip:
                table_str = str(table).lower()
                if any(k in table_str for k in ["fluorine", "chlorine", "bromine", "iodine", "lodine"]):
                    force_scan = True
                    is_skip = False
                    if item_idx == -1: item_idx = 0
            if is_skip: continue
            
            for row in table:
                raw_item_cell = str(row[item_idx]) if item_idx < len(row) and row[item_idx] else ""
                raw_result_cell = str(row[result_idx]) if result_idx != -1 and result_idx < len(row) and row[result_idx] else ""
                
                rows_to_process = []
                if "\n" in raw_item_cell:
                    split_items = [x.strip() for x in raw_item_cell.split('\n') if x.strip()]
                    if "\n" in raw_result_cell:
                        split_results = [x.strip() for x in raw_result_cell.split('\n') if x.strip()]
                    else:
                        split_results = [raw_result_cell.strip()] if raw_result_cell.strip() else []
                    if len(split_items) > 1 and len(split_results) == 1:
                        for si in split_items:
                            virtual_row = list(row)
                            virtual_row[item_idx] = si
                            if result_idx != -1: virtual_row[result_idx] = split_results[0]
                            rows_to_process.append(virtual_row)
                    elif len(split_items) == len(split_results):
                        for si, sr in zip(split_items, split_results):
                            virtual_row = list(row)
                            virtual_row[item_idx] = si
                            if result_idx != -1: virtual_row[result_idx] = sr
                            rows_to_process.append(virtual_row)
                    else:
                        rows_to_process.append(row)
                else:
                    rows_to_process.append(row)

                for proc_row in rows_to_process:
                    clean_row = [clean_text(cell) for cell in proc_row]
                    row_txt = "".join(clean_row).lower()
                    if "test item" in row_txt or "result" in row_txt: continue
                    if not any(clean_row): continue
                    
                    target_item_col = item_idx if item_idx != -1 else 0
                    if target_item_col >= len(clean_row): continue
                    item_name = clean_row[target_item_col]
                    item_name_lower = item_name.lower()
                    if "pvc" in item_name_lower: continue

                    result = ""
                    if result_idx != -1 and result_idx < len(clean_row):
                        result = clean_row[result_idx]
                    
                    if result == "" and force_scan:
                        for cell in reversed(clean_row):
                            c_lower = cell.lower()
                            if "mg/kg" in c_lower or "ppm" in c_lower: continue
                            if "nd" in c_lower or "n.d." in c_lower:
                                result = cell
                                break
                            if re.search(r"^\d+(\.\d+)?", cell):
                                if is_suspicious_limit_value(cell): continue
                                result = cell
                                break

                    # MDL 數值排除法
                    if mdl_idx != -1 and mdl_idx < len(clean_row):
                        mdl_val = clean_text(clean_row[mdl_idx])
                        if result == mdl_val and result != "":
                            result = "" 

                    temp_priority = parse_value_priority(result)
                    if temp_priority[0] == 0:
                        for cell in reversed(clean_row):
                            c_lower = cell.lower()
                            if not cell: continue
                            if "nd" in c_lower or "n.d." in c_lower or "negative" in c_lower:
                                result = cell
                                break
                            if re.search(r"^\d+(\.\d+)?", cell):
                                if is_suspicious_limit_value(cell): continue
                                result = cell
                                break
                    
                    priority = parse_value_priority(result)
                    if priority[0] == 0: continue

                    for target_key, keywords in SIMPLE_KEYWORDS.items():
                        # [v63.35 Fix] TBBP 防呆
                        if target_key == "BBP" and ("tbbp" in item_name_lower or "tetrabromo" in item_name_lower): continue

                        if target_key == "Cd" and any(bad in item_name_lower for bad in ["hbcdd", "cyclododecane", "ecd", "indeno"]): continue
                        if target_key == "F" and any(bad in item_name_lower for bad in ["perfluoro", "polyfluoro", "pfos", "pfoa", "全氟"]): continue
                        if target_key == "BR" and any(bad in item_name_lower for bad in ["polybromo", "hexabromo", "monobromo", "dibromo", "tribromo", "tetrabromo", "pentabromo", "heptabromo", "octabromo", "nonabromo", "decabromo", "multibromo", "pbb", "pbde", "多溴", "六溴", "一溴", "二溴", "三溴", "四溴", "五溴", "七溴", "八溴", "九溴", "十溴", "二苯醚"]): continue
                        if target_key == "Pb" and any(bad in item_name_lower for bad in ["pbb", "pbde", "polybrominated", "多溴"]): continue

                        for kw in keywords:
                            if kw.lower() in item_name_lower:
                                if target_key == "PFOS" and "related" in item_name_lower: continue 
                                data_pool[target_key].append({"priority": priority, "filename": filename})
                    
                    for group_key, keywords in GROUP_KEYWORDS.items():
                        for kw in keywords:
                            if kw.lower() in item_name_lower:
                                file_group_data[group_key].append(priority)
                                break

    if not (data_pool["F"] and data_pool["CL"] and data_pool["BR"] and data_pool["I"]):
        process_halogen_block(pdf, filename, data_pool)

    if company == "SGS":
        missing_targets = []
        pb_data = [d for d in data_pool["Pb"] if d['filename'] == filename]
        halogen_data = []
        for h in ["F", "CL", "BR", "I"]:
            halogen_data.extend([d for d in data_pool[h] if d['filename'] == filename])
        pfos_data = [d for d in data_pool["PFOS"] if d['filename'] == filename]
        
        trigger_rescue = False
        if not pb_data: trigger_rescue = True
        if ("halogen" in full_text_content.lower() or "卤素" in full_text_content) and not halogen_data:
            trigger_rescue = True
        if "pfos" in full_text_content.lower() and not pfos_data:
            trigger_rescue = True

        if trigger_rescue:
             parse_text_lines_v60(full_text_content, data_pool, file_group_data, filename, company, targets=None)

    for group_key, values in file_group_data.items():
        if values:
            best_in_file = sorted(values, key=lambda x: (x[0], x[1]), reverse=True)[0]
            data_pool[group_key].append({"priority": best_in_file, "filename": filename})

    return data_pool, file_dates_candidates

# =============================================================================
# 7. 主程式與分流器
# =============================================================================

def process_files(files):
    results = []
    progress_bar = st.progress(0)
    
    for i, file in enumerate(files):
        try:
            with pdfplumber.open(file) as pdf:
                first_page_text = (pdf.pages[0].extract_text() or "").upper()
                company = identify_company(first_page_text)
                
                # 分流邏輯
                if "MALAYSIA" in first_page_text and "SGS" in first_page_text:
                    # 通道 A: 馬來西亞 (v63.28 核心 - 文字掃描)
                    data_pool, date_candidates = process_malaysia_engine(pdf, file.name)
                elif company == "CTI":
                    # 通道 B: CTI (v63.35 - Max Rule + TBBP防呆)
                    data_pool, date_candidates = process_cti_engine(pdf, file.name)
                else:
                    # 通道 C: 標準 SGS (v63.35 - 0.003排除 + TBBP防呆)
                    data_pool, date_candidates = process_standard_engine(pdf, file.name, company)
                
                final_row = {}
                valid_candidates = [d for d in date_candidates if d[0] > -50]
                if valid_candidates:
                    best_date = sorted(valid_candidates, key=lambda x: (x[0], x[1]), reverse=True)[0][1]
                    final_row["日期"] = best_date.strftime("%Y/%m/%d")
                else:
                    final_row["日期"] = ""
                
                final_row["檔案名稱"] = file.name
                
                for k in OUTPUT_COLUMNS:
                    if k in ["日期", "檔案名稱"]: continue
                    candidates = data_pool.get(k, [])
                    if candidates:
                        # 排序優先級 (3:Num, 2:Neg, 1:ND)
                        best = sorted(candidates, key=lambda x: (x['priority'][0], x['priority'][1]), reverse=True)[0]
                        # 數值格式化 (v63.35: 去除 .0)
                        final_row[k] = format_output_value(best['priority'][2])
                    else:
                        final_row[k] = ""
                
                results.append(final_row)

        except Exception as e:
            st.error(f"檔案 {file.name} 處理失敗: {e}")
            
        progress_bar.progress((i + 1) / len(files))
        
    return results

def find_report_start_page(pdf):
    for i in range(min(10, len(pdf.pages))):
        text = (pdf.pages[i].extract_text() or "").lower()
        if "test report" in text or "測試報告" in text: return i
    return 0

# =============================================================================
# 8. UI
# =============================================================================

st.set_page_config(page_title="SGS/CTI 報告聚合工具 v63.35", layout="wide")
st.title("📄 萬用型檢測報告聚合工具 (v63.35 雙核心．數值修正終極版)")
st.info("💡 v63.35：修正 CTI 多樣品加總邏輯為「取最大值」、排除 CTI 誤抓 TBBP-A、排除 SGS 誤抓 0.003 MDL，並優化輸出格式（去除 .0）。馬來西亞引擎保持獨立運作。")

uploaded_files = st.file_uploader("請一次選取所有 PDF 檔案", type="pdf", accept_multiple_files=True)

if uploaded_files:
    if st.button("🔄 重新執行"): st.rerun()

    try:
        result_data = process_files(uploaded_files)
        df = pd.DataFrame(result_data)
        df = df.reindex(columns=OUTPUT_COLUMNS)

        st.success("✅ 處理完成！")
        st.dataframe(df)

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Summary')
        
        st.download_button(
            label="📥 下載 Excel",
            data=output.getvalue(),
            file_name="SGS_CTI_Summary_v63.35.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        
    except Exception as e:
        st.error(f"系統錯誤: {e}")
