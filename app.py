import streamlit as st
import pdfplumber
import pandas as pd
import io
import re
from datetime import datetime

# =============================================================================
# 1. [Core 1] v63.43 繁簡韓通用關鍵字庫
# =============================================================================

# 內部處理用的欄位 (引擎產出)
INTERNAL_COLUMNS = [
    "Pb", "Cd", "Hg", "Cr6+", "PBB", "PBDE", 
    "DEHP", "BBP", "DBP", "DIBP", 
    "PFOS", "PFAS", "F", "CL", "BR", "I", 
    "日期", "檔案名稱"
]

# 最終顯示用的欄位 (UI 呈現)
DISPLAY_COLUMNS = [
    "ITEM", "Pb", "Cd", "Hg", "Cr+6", "PBBs", "PBDEs", 
    "DEHP", "BBP", "DBP", "DIBP", 
    "F", "Cl", "Br", "I", "PFOS", "PFAS", 
    "Date", "File Name"
]

# 欄位對應映射
COLUMN_MAPPING = {
    "Cr6+": "Cr+6",
    "PBB": "PBBs",
    "PBDE": "PBDEs",
    "CL": "Cl",
    "BR": "Br",
    "日期": "Date",
    "檔案名稱": "File Name"
}

# v63.43: 補齊簡體中文、韓文及縮寫關鍵字
SIMPLE_KEYWORDS = {
    "Pb": ["Lead", "鉛", "铅", "Pb", "납"],
    "Cd": ["Cadmium", "鎘", "镉", "Cd", "카드뮴"],
    "Hg": ["Mercury", "汞", "Hg", "수은"],
    "Cr6+": ["Hexavalent Chromium", "六價鉻", "六价铬", "Cr(VI)", "Chromium VI", "6가 크롬"],
    "DEHP": ["DEHP", "Di(2-ethylhexyl) phthalate", "Bis(2-ethylhexyl) phthalate", "邻苯二甲酸二(2-乙基己基)酯"],
    "BBP": ["BBP", "Butyl benzyl phthalate", "邻苯二甲酸丁苄酯"],
    "DBP": ["DBP", "Dibutyl phthalate", "邻苯二甲酸二丁酯"],
    "DIBP": ["DIBP", "Diisobutyl phthalate", "邻苯二甲酸二异丁酯"],
    "PFOS": ["Perfluorooctane sulfonates", "Perfluorooctane sulfonate", "Perfluorooctane sulfonic acid", "全氟辛烷磺酸", "Perfluorooctane Sulfonamide", "PFOS and its salts", "PFOS 及其盐", "PFOS"],
    "F": ["Fluorine", "氟", "불소"],
    "CL": ["Chlorine", "氯", "염소"],
    "BR": ["Bromine", "溴", "브롬"],
    "I": ["Iodine", "碘", "lodine", "요오드"]
}

GROUP_KEYWORDS = {
    "PBB": [
        "Polybrominated Biphenyls", "PBBs", "Sum of PBBs", 
        "多溴聯苯總和", "多溴聯苯之和", "多溴联苯总和", "多溴联苯之和", "多溴联苯", "폴리브롬화비페닐",
        "Polybromobiphenyl", "Monobromobiphenyl", "Dibromobiphenyl", "Tribromobiphenyl", 
        "Tetrabromobiphenyl", "Pentabromobiphenyl", "Hexabromobiphenyl", 
        "Heptabromobiphenyl", "Octabromobiphenyl", "Nonabromobiphenyl", 
        "Decabromobiphenyl", "Monobrominated", "Dibrominated", "Tribrominated", 
        "Tetrabrominated", "Pentabrominated", "Hexabrominated", "Heptabrominated", 
        "Octabrominated", "Nonabrominated", "Decabrominated",
        "MonoBB", "DiBB", "TriBB", "TetraBB", "PentaBB", "HexaBB", "HeptaBB", "OctaBB", "NonaBB", "DecaBB"
    ],
    "PBDE": [
        "Polybrominated Diphenyl Ethers", "PBDEs", "Sum of PBDEs", 
        "多溴聯苯醚總和", "多溴二苯醚之和", "多溴二苯醚總和", "多溴二苯醚", "폴리브롬화디페닐에테르",
        "Polybromodiphenyl ether", "Monobromodiphenyl ether", "Dibromodiphenyl ether", "Tribromodiphenyl ether",
        "Tetrabromodiphenyl ether", "Pentabromodiphenyl ether", "Hexabromodiphenyl ether",
        "Heptabromodiphenyl ether", "Octabromodiphenyl ether", "Nonabromodiphenyl ether",
        "Decabromodiphenyl ether", "Monobrominated Diphenyl", "Dibrominated Diphenyl", "Tribrominated Diphenyl",
        "Tetrabrominated Diphenyl", "Pentabrominated Diphenyl", "Hexabrominated Diphenyl",
        "Heptabrominated Diphenyl", "Octabrominated Diphenyl", "Nonabrominated Diphenyl",
        "Decabrominated Diphenyl",
        "MonoBDE", "DiBDE", "TriBDE", "TetraBDE", "PentaBDE", "HexaBDE", "HeptaBDE", "OctaBDE", "NonaBDE", "DecaBDE"
    ]
}

PFAS_SUMMARY_KEYWORDS = ["Per- and Polyfluoroalkyl Substances", "PFAS", "全氟/多氟烷基物質"]
MSDS_HEADER_KEYWORDS = ["content", "composition", "concentration", "含量", "成分"]

# =============================================================================
# 2. [Core 2] 馬來西亞設定
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
# 4. 引擎區域 (保持 v63.43 原樣)
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
    data_pool = {key: [] for key in INTERNAL_COLUMNS if key not in ["日期", "檔案名稱"]}
    full_text = ""
    for p in pdf.pages: full_text += (p.extract_text() or "") + "\n"
    report_date = extract_date_malaysia_v7(pdf.pages[0].extract_text() or "")
    for col_key in INTERNAL_COLUMNS:
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

def extract_dates_v63_13_global(text):
    candidates = []
    poison_kw = ["received", "receive", "expiry", "valid", "process", "testing period", "检测日期", "接收日期"]
    backup_kw = ["testing", "period", "test"]
    bonus_kw = ["report date", "date:", "日期:", "report no"]
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
                if any(p in context_window for p in poison_kw): score -= 1000 
                elif any(b in context_window for b in bonus_kw): score += 500
                elif any(b in context_window for b in backup_kw): score += 10 
                candidates.append((score, dt))
        except: pass
    return candidates

def process_cti_engine(pdf, filename):
    data_pool = {key: [] for key in INTERNAL_COLUMNS if key not in ["日期", "檔案名稱"]}
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
            for c in range(cols):
                header = str(table[0][c]).lower()
                if "item" in header or "項目" in header or "项目" in header:
                    item_col_idx = c
                    break
            if item_col_idx == -1: item_col_idx = 0
            data_col_indices = []
            for c in range(item_col_idx + 1, mdl_col_idx):
                data_col_indices.append(c)
            if not data_col_indices:
                data_col_indices = [mdl_col_idx - 1]
            for row in table:
                if len(row) <= mdl_col_idx: continue
                item_text = clean_text(row[item_col_idx]).lower()
                if "tbbp" in item_text or "tetrabromo" in item_text: continue
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
                    max_val = max(valid_numbers) 
                    final_prio = (3, max_val, str(max_val))
                elif has_negative:
                    final_prio = (2, 0, "NEGATIVE")
                elif has_nd:
                    final_prio = (1, 0, "N.D.")
                if final_prio[0] == 0: continue
                for key, kws in SIMPLE_KEYWORDS.items():
                    if key == "BR" and ("halogen" in item_text or "bromine" in item_text): pass 
                    else:
                        if key == "Cd" and any(bad in item_text for bad in ["hbcdd", "cyclododecane", "ecd"]): continue 
                        if key == "F" and any(bad in item_text for bad in ["perfluoro", "polyfluoro", "pfos", "pfoa", "全氟"]): continue
                        if key == "BR" and any(bad in item_text for bad in ["polybromo", "hexabromo", "monobromo", "dibromo", "tribromo", "tetrabromo", "pentabromo", "heptabromo", "octabromo", "nonabromo", "decabromo", "multibromo", "pbb", "pbde", "多溴", "六溴", "一溴", "二溴", "三溴", "四溴", "五溴", "七溴", "八溴", "九溴", "十溴", "二苯醚"]): continue
                        if key == "Pb" and any(bad in item_text for bad in ["pbb", "pbde", "polybrominated", "多溴"]): continue
                    if any(kw.lower() in item_text for kw in kws):
                        data_pool[key].append({"priority": final_prio, "filename": filename})
                        break
                for key, kws in GROUP_KEYWORDS.items():
                    if any(kw.lower() in item_text for kw in kws):
                        data_pool[key].append({"priority": final_prio, "filename": filename})
                        break
    return data_pool, date_candidates

def extract_dates_v60(text):
    lines = text.split('\n')
    candidates = []
    bonus_kw = ["report date", "issue date", "date:", "dated", "日期"]
    poison_kw = ["approve", "approved", "receive", "received", "receipt", "period", "expiry", "valid", "testing period", "检测日期"]
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
            if key == "BBP" and ("tbbp" in line_lower or "tetrabromo" in line_lower): continue
            if key == "BR" and ("halogen" in line_lower or "bromine" in line_lower): pass
            else:
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
    data_pool = {key: [] for key in INTERNAL_COLUMNS if key not in ["日期", "檔案名稱"]}
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
                    is_sum_row = "sum of" in item_name_lower or "之和" in item_name_lower or "总和" in item_name_lower
                    if is_sum_row:
                         result = "" 
                         for cell in reversed(clean_row):
                            c_lower = cell.lower()
                            if c_lower in ["1000", "100", "50", "10", "mg/kg", "ppm", "-"]: continue
                            if "nd" in c_lower or "n.d." in c_lower:
                                result = cell
                                break
                            if re.search(r"^\d+(\.\d+)?", cell):
                                if is_suspicious_limit_value(cell): continue
                                result = cell
                                break
                    if not is_sum_row:
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
                        if target_key == "Cd" and any(bad in item_name_lower for bad in ["hbcdd", "cyclododecane", "ecd", "indeno"]): continue
                        if target_key == "F" and any(bad in item_name_lower for bad in ["perfluoro", "polyfluoro", "pfos", "pfoa", "全氟"]): continue
                        if target_key == "BR" and ("halogen" in item_name_lower or "bromine" in item_name_lower): pass
                        else:
                            if target_key == "BR" and any(bad in item_name_lower for bad in ["polybromo", "hexabromo", "monobromo", "dibromo", "tribromo", "tetrabromo", "pentabromo", "heptabromo", "octabromo", "nonabromo", "decabromo", "multibromo", "pbb", "pbde", "多溴", "六溴", "一溴", "二溴", "三溴", "四溴", "五溴", "七溴", "八溴", "九溴", "十溴", "二苯醚"]): continue
                        if target_key == "Pb" and any(bad in item_name_lower for bad in ["pbb", "pbde", "polybrominated", "多溴"]): continue
                        if target_key == "BBP" and ("tbbp" in item_name_lower or "tetrabromo" in item_name_lower): continue
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

def clean_intertek_value(val):
    if not val: return ""
    cleaned = re.sub(r'\s*\(.*?\)', '', val)
    return cleaned.strip()

def extract_intertek_dates(text):
    candidates = []
    poison_kw = ["received", "receive", "expiry", "valid", "process", "testing period", "检测日期", "接收日期", "date test started", "date job applied"]
    bonus_kw = ["issue date"] 
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
                if any(p in context_window for p in poison_kw): score -= 1000 
                elif any(b in context_window for b in bonus_kw): score += 200
                candidates.append((score, dt))
        except: pass
    return candidates

def process_intertek_engine(pdf, filename):
    data_pool = {key: [] for key in INTERNAL_COLUMNS if key not in ["日期", "檔案名稱"]}
    full_text_content = ""
    for p in pdf.pages:
        full_text_content += (p.extract_text() or "") + "\n"
    if "per- and polyfluoroalkyl substances" in full_text_content.lower() or "pfas" in full_text_content.lower():
        data_pool["PFAS"].append({"priority": (4, 0, "REPORT"), "filename": filename})
    date_candidates = extract_intertek_dates(full_text_content[:2000])
    has_pbde_sub_nd = False
    has_pbb_sub_nd = False 
    for page in pdf.pages:
        tables = page.extract_tables()
        for table in tables:
            if not table or len(table) < 2: continue
            rl_col_idx = -1
            item_col_idx = -1
            cols = len(table[0])
            for c in range(cols):
                header = clean_text(table[0][c]).lower()
                if "rl" in header or "reporting limit" in header or "mdl" in header or "loq" in header:
                    rl_col_idx = c
                    break
            for c in range(cols):
                header = str(table[0][c]).lower()
                if "test item" in header or "測試項目" in header or "시험항목" in header:
                    item_col_idx = c
                    break
            if item_col_idx == -1: item_col_idx = 0
            result_col_idx = -1
            for c in range(cols):
                header = str(table[0][c]).lower()
                if "result" in header or "結果" in header or "submitted samples" in header or "시험결과" in header:
                    result_col_idx = c
                    break
            if result_col_idx == -1 and rl_col_idx != -1:
                result_col_idx = rl_col_idx - 1 
            for r_idx, row in enumerate(table):
                if len(row) <= item_col_idx: continue
                item_text_raw = clean_text(row[item_col_idx])
                item_text_lower = item_text_raw.lower()
                is_pb_sum = "polybrominated" in item_text_lower and ("biphenyls" in item_text_lower or "ether" in item_text_lower)
                result_text = ""
                if result_col_idx != -1 and result_col_idx < len(row):
                    result_text = clean_text(row[result_col_idx])
                if is_pb_sum and not result_text:
                    if r_idx + 1 < len(table):
                        next_row = table[r_idx + 1]
                        if result_col_idx != -1 and result_col_idx < len(next_row):
                            next_val = clean_text(next_row[result_col_idx])
                            if "nd" in next_val.lower():
                                result_text = "N.D."
                    if not result_text:
                         for cell in reversed(row):
                            c_lower = clean_text(cell).lower()
                            if "nd" in c_lower or "n.d." in c_lower:
                                result_text = "N.D."
                                break
                if not result_text and not is_pb_sum: 
                    if ("brominated" in item_text_lower and "ether" in item_text_lower) or "monobde" in item_text_lower or "decabde" in item_text_lower or "모노브로모디페닐에테르" in item_text_raw:
                         sub_res = ""
                         if result_col_idx != -1 and result_col_idx < len(row):
                             sub_res = clean_text(row[result_col_idx])
                         if "nd" in sub_res.lower():
                             has_pbde_sub_nd = True
                    elif ("brominated" in item_text_lower and "biphenyl" in item_text_lower) or "monobb" in item_text_lower or "decabb" in item_text_lower or "모노브로모비페닐" in item_text_raw:
                         sub_res = ""
                         if result_col_idx != -1 and result_col_idx < len(row):
                             sub_res = clean_text(row[result_col_idx])
                         if "nd" in sub_res.lower():
                             has_pbb_sub_nd = True
                    continue
                result_text = clean_intertek_value(result_text)
                prio = parse_value_priority(result_text)
                if prio[0] == 0: continue
                for key, kws in SIMPLE_KEYWORDS.items():
                    if key == "CL" and ("pvc" in item_text_lower or "polyvinyl" in item_text_lower): continue
                    if any(kw.lower() in item_text_lower for kw in kws):
                        data_pool[key].append({"priority": prio, "filename": filename})
                        break
                for key, kws in GROUP_KEYWORDS.items():
                    if any(kw.lower() in item_text_lower for kw in kws):
                        data_pool[key].append({"priority": prio, "filename": filename})
                        break
    if not data_pool["PBDE"] and has_pbde_sub_nd:
        data_pool["PBDE"].append({"priority": (1, 0, "N.D."), "filename": filename})
    if not data_pool["PBB"] and has_pbb_sub_nd:
        data_pool["PBB"].append({"priority": (1, 0, "N.D."), "filename": filename})
    return data_pool, date_candidates

# =============================================================================
# 9. 智慧整合邏輯 (v63.45 新增核心)
# =============================================================================

def get_value_score(val_str):
    """
    評估數值優先級:
    3: 數值 (Max Logic)
    2: NEGATIVE
    1: N.D.
    0: Empty / Invalid
    Returns: (type_score, float_value)
    """
    val_str = str(val_str).strip().upper()
    if not val_str: return (0, 0)
    
    # Check for N.D. variants
    if "N.D." in val_str or "ND" in val_str or "<" in val_str: return (1, 0)
    
    # Check for NEGATIVE
    if "NEGATIVE" in val_str or "陰性" in val_str: return (2, 0)
    
    # Check for Number
    try:
        # Remove any non-numeric chars except dot
        clean_num = re.sub(r"[^\d\.]", "", val_str)
        f = float(clean_num)
        return (3, f)
    except:
        return (0, 0)

def compare_chemical_values(v1, v2):
    """回傳較大/較高風險的值"""
    s1 = get_value_score(v1)
    s2 = get_value_score(v2)
    
    # 比較類型優先級 (數值 > NEGATIVE > ND > Empty)
    if s1[0] > s2[0]: return v1
    if s2[0] > s1[0]: return v2
    
    # 若類型相同且為數值，比大小
    if s1[0] == 3:
        return v1 if s1[1] >= s2[1] else v2
    
    # 若類型相同且非數值 (如都是 ND)，回傳 v1
    return v1

def process_batch(files, item_index):
    """處理單一批次檔案，回傳整合後的單列資料"""
    batch_raw_data = [] # 存儲每個檔案的原始抓取結果
    
    # 1. 分別解析每個檔案
    for file in files:
        try:
            with pdfplumber.open(file) as pdf:
                first_page_text = (pdf.pages[0].extract_text() or "").upper()
                company = identify_company(first_page_text)
                
                if "MALAYSIA" in first_page_text and "SGS" in first_page_text:
                    data_pool, date_candidates = process_malaysia_engine(pdf, file.name)
                elif company == "CTI":
                    data_pool, date_candidates = process_cti_engine(pdf, file.name)
                elif company == "INTERTEK":
                    data_pool, date_candidates = process_intertek_engine(pdf, file.name)
                else:
                    data_pool, date_candidates = process_standard_engine(pdf, file.name, company)
                
                # 整理單檔結果
                file_result = {}
                file_result["File Name"] = file.name
                
                # 日期
                valid_dates = [d for d in date_candidates if d[0] > -50]
                if valid_dates:
                    best_date = sorted(valid_dates, key=lambda x: (x[0], x[1]), reverse=True)[0][1]
                    file_result["Date"] = best_date.strftime("%Y/%m/%d")
                    file_result["DateObj"] = best_date
                else:
                    file_result["Date"] = ""
                    file_result["DateObj"] = datetime.min
                
                # 化學數值
                for k in INTERNAL_COLUMNS:
                    if k in ["日期", "檔案名稱"]: continue
                    candidates = data_pool.get(k, [])
                    if candidates:
                        best = sorted(candidates, key=lambda x: (x['priority'][0], x['priority'][1]), reverse=True)[0]
                        file_result[k] = format_output_value(best['priority'][2])
                        file_result[f"{k}_score"] = get_value_score(file_result[k])
                    else:
                        file_result[k] = ""
                        file_result[f"{k}_score"] = (0, 0)
                
                batch_raw_data.append(file_result)
        except Exception as e:
            st.error(f"檔案 {file.name} 解析失敗: {e}")

    if not batch_raw_data: return None

    # 2. 整合運算 (Aggregation)
    aggregated_row = {"ITEM": item_index}
    
    # (A) 數值整合: 取最大值
    for k in INTERNAL_COLUMNS:
        if k in ["日期", "檔案名稱"]: continue
        
        best_val = ""
        for d in batch_raw_data:
            current_val = d.get(k, "")
            best_val = compare_chemical_values(best_val, current_val)
        
        # 映射到顯示欄位名稱 (如 PBB -> PBBs)
        display_key = COLUMN_MAPPING.get(k, k)
        aggregated_row[display_key] = best_val

    # (B) 日期整合: 取最新日期 (獨立判斷)
    latest_date_obj = datetime.min
    latest_date_str = ""
    for d in batch_raw_data:
        if d["DateObj"] > latest_date_obj:
            latest_date_obj = d["DateObj"]
            latest_date_str = d["Date"]
    aggregated_row["Date"] = latest_date_str

    # (C) 檔名整合: Pb 優先決 > 日期決
    best_file_name = batch_raw_data[0]["File Name"]
    
    # 找 Pb 最高分
    max_pb_score = (-1, -1)
    for d in batch_raw_data:
        s = d.get("Pb_score", (0, 0))
        if s[0] > max_pb_score[0]:
            max_pb_score = s
        elif s[0] == max_pb_score[0] and s[1] > max_pb_score[1]:
            max_pb_score = s
            
    # 篩選出 Pb 最高的檔案們 (可能有多個)
    candidates = [d for d in batch_raw_data if d.get("Pb_score") == max_pb_score]
    
    # 從候選者中找日期最新的
    if candidates:
        best_candidate = sorted(candidates, key=lambda x: x["DateObj"], reverse=True)[0]
        best_file_name = best_candidate["File Name"]
        
    aggregated_row["File Name"] = best_file_name

    return aggregated_row

# =============================================================================
# 10. UI (Streamlit)
# =============================================================================

st.set_page_config(page_title="SGS/CTI/Intertek 報告聚合工具 v63.45", layout="wide")
st.title("📄 萬用型檢測報告聚合工具 (v63.45 智慧整合版)")
st.info("💡 v63.45 更新：\n1. 支援「累加式上傳」：多次執行會自動新增 ITEM 列。\n2. 智慧整合：針對同一批檔案，自動抓取各元素的「最大風險值」。\n3. 邏輯優化：日期顯示「最新日期」，檔名顯示「Pb 最高者」。")

# 初始化 Session State
if 'results' not in st.session_state:
    st.session_state['results'] = []
if 'item_count' not in st.session_state:
    st.session_state['item_count'] = 0

# 上傳區
uploaded_files = st.file_uploader("請拖入一批 PDF 檔案 (視為同一 ITEM)", type="pdf", accept_multiple_files=True)

col1, col2 = st.columns([1, 1])

with col1:
    if st.button("▶️ 執行解析 (新增 ITEM)", type="primary"):
        if uploaded_files:
            st.session_state['item_count'] += 1
            current_item_id = st.session_state['item_count']
            
            with st.spinner(f"正在處理 ITEM {current_item_id}..."):
                row = process_batch(uploaded_files, current_item_id)
                if row:
                    st.session_state['results'].append(row)
                    st.success(f"ITEM {current_item_id} 處理完成！")
        else:
            st.warning("請先上傳檔案！")

with col2:
    if st.button("🗑️ 清除所有資料"):
        st.session_state['results'] = []
        st.session_state['item_count'] = 0
        st.rerun()

# 顯示結果
if st.session_state['results']:
    st.markdown("### 📊 解析結果總表")
    
    # 建立 DataFrame 並依照指定順序排列
    df = pd.DataFrame(st.session_state['results'])
    
    # 確保所有顯示欄位都存在
    for col in DISPLAY_COLUMNS:
        if col not in df.columns:
            df[col] = ""
            
    df = df[DISPLAY_COLUMNS] # 重排序
    
    st.dataframe(df)

    # 下載按鈕
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Summary')
    
    st.download_button(
        label="📥 下載 Excel",
        data=output.getvalue(),
        file_name=f"SGS_CTI_Intertek_Summary_v63.45.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
