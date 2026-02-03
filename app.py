import streamlit as st
import pdfplumber
import pandas as pd
import io
import re
from datetime import datetime

# =============================================================================
# 1. 定義欄位與關鍵字
# =============================================================================

OUTPUT_COLUMNS = [
    "Pb", "Cd", "Hg", "Cr6+", "PBB", "PBDE", 
    "DEHP", "BBP", "DBP", "DIBP", 
    "PFOS", "PFAS", "F", "CL", "BR", "I", 
    "日期", "檔案名稱"
]

SIMPLE_KEYWORDS = {
    "Pb": ["Lead", "鉛", "Pb"],
    "Cd": ["Cadmium", "鎘", "Cd"],
    "Hg": ["Mercury", "汞", "Hg"],
    "Cr6+": ["Hexavalent Chromium", "六價鉻", "Cr(VI)", "Chromium VI", "Hexavalent Chromium"],
    "DEHP": ["DEHP", "Di(2-ethylhexyl) phthalate", "Bis(2-ethylhexyl) phthalate"],
    "BBP": ["BBP", "Butyl benzyl phthalate"],
    "DBP": ["DBP", "Dibutyl phthalate"],
    "DIBP": ["DIBP", "Diisobutyl phthalate"],
    "PFOS": ["Perfluorooctane sulfonates", "Perfluorooctane sulfonate", "Perfluorooctane sulfonic acid", "全氟辛烷磺酸", "Perfluorooctane Sulfonamide", "PFOS and its salts", "PFOS 及其盐"],
    "F": ["Fluorine", "氟"],
    "CL": ["Chlorine", "氯"],
    "BR": ["Bromine", "溴"],
    "I": ["Iodine", "碘"]
}

GROUP_KEYWORDS = {
    "PBB": [
        "Polybrominated Biphenyls", "PBBs", "Sum of PBBs", "多溴聯苯總和", "多溴聯苯之和", "多溴联苯之和",
        "Polybromobiphenyl", "Polybromobiphenyls",
        "Monobromobiphenyl", "Dibromobiphenyl", "Tribromobiphenyl", 
        "Tetrabromobiphenyl", "Pentabromobiphenyl", "Hexabromobiphenyl", 
        "Heptabromobiphenyl", "Octabromobiphenyl", "Nonabromobiphenyl", 
        "Decabromobiphenyl", 
        "Monobrominated", "Dibrominated", "Tribrominated", 
        "Tetrabrominated", "Pentabrominated", "Hexabrominated", 
        "Heptabrominated", "Octabrominated", "Nonabrominated", 
        "Decabrominated", "bromobiphenyl"
    ],
    "PBDE": [
        "Polybrominated Diphenyl Ethers", "PBDEs", "Sum of PBDEs", "多溴聯苯醚總和", "多溴二苯醚之和", "多溴二苯醚之和",
        "Polybromodiphenyl ether", "Polybromodiphenyl ethers",
        "Monobromodiphenyl ether", "Dibromodiphenyl ether", "Tribromodiphenyl ether",
        "Tetrabromodiphenyl ether", "Pentabromodiphenyl ether", "Hexabromodiphenyl ether",
        "Heptabromodiphenyl ether", "Octabromodiphenyl ether", "Nonabromodiphenyl ether",
        "Decabromodiphenyl ether", 
        "Monobrominated Diphenyl", "Dibrominated Diphenyl", "Tribrominated Diphenyl",
        "Tetrabrominated Diphenyl", "Pentabrominated Diphenyl", "Hexabrominated Diphenyl",
        "Heptabrominated Diphenyl", "Octabrominated Diphenyl", "Nonabrominated Diphenyl",
        "Decabrominated Diphenyl", "bromodiphenyl ether"
    ]
}

PFAS_SUMMARY_KEYWORDS = [
    "Per- and Polyfluoroalkyl Substances", "PFAS", "全氟/多氟烷基物質", "全氟烷基物質"
]

MSDS_HEADER_KEYWORDS = [
    "content", "composition", "concentration", "含量", "成分"
]

# v63.11: 手動月份對照表 (解決 Locale 問題)
MONTH_MAP = {
    'jan': 1, 'january': 1,
    'feb': 2, 'february': 2,
    'mar': 3, 'march': 3,
    'apr': 4, 'april': 4,
    'may': 5,
    'jun': 6, 'june': 6,
    'jul': 7, 'july': 7,
    'aug': 8, 'august': 8,
    'sep': 9, 'september': 9, 'sept': 9,
    'oct': 10, 'october': 10,
    'nov': 11, 'november': 11,
    'dec': 12, 'december': 12
}

# =============================================================================
# 2. 共用輔助函式
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
        if n in [1000.0, 100.0, 50.0, 25.0, 10.0, 5.0, 2.0]: return True
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

def identify_company(text):
    txt = text.lower()
    if "sgs" in txt: return "SGS"
    if "intertek" in txt: return "INTERTEK"
    if "cti" in txt or "centre testing" in txt: return "CTI"
    if "ctic" in txt: return "CTIC"
    return "OTHERS"

# =============================================================================
# 3. 引擎 A: 標準引擎 (Standard Engine) - v60.5
# =============================================================================

def extract_dates_v60(text):
    """v60.5 標準日期提取 (用於 SGS/Intertek)"""
    lines = text.split('\n')
    candidates = []
    
    bonus_kw = ["report date", "issue date", "date:", "dated", "日期"]
    poison_kw = ["approve", "approved", "receive", "received", "receipt", "period", "expiry", "valid"]

    pat_ymd = r"(20\d{2})[\.\/-](0?[1-9]|1[0-2])[\.\/-](3[01]|[12][0-9]|0?[1-9])"
    pat_dmy = r"(3[01]|[12][0-9]|0?[1-9])\s+([a-zA-Z]{3,})\s+(20\d{2})"
    pat_mdy = r"([a-zA-Z]{3,})\s+(3[01]|[12][0-9]|0?[1-9])\s+(20\d{2})"
    pat_chinese = r"(20\d{2})\s*年\s*(0?[1-9]|1[0-2])\s*月\s*(3[01]|[12][0-9]|0?[1-9])\s*日"

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
    if any(k in full_header_text for k in MSDS_HEADER_KEYWORDS) and "result" not in full_header_text and "结果" not in full_header_text:
        is_msds_table = True

    for r_idx in range(max_scan_rows):
        row = table[r_idx]
        for c_idx, cell in enumerate(row):
            txt = clean_text(cell).lower()
            if not txt: continue
            
            if "test item" in txt or "tested item" in txt or "測試項目" in txt or "检测项目" in txt:
                if item_idx == -1: item_idx = c_idx
            if "mdl" in txt or "loq" in txt:
                if mdl_idx == -1: mdl_idx = c_idx
            
            if company == "SGS":
                 if ("result" in txt or "結果" in txt or "结果" in txt or re.search(r"00[1-9]", txt) or 
                    re.search(r"^[a-z]?\s*-?\s*\d+$", txt) or "no." in txt):
                    if "cas" not in txt and "method" not in txt and "limit" not in txt:
                        if result_idx == -1: result_idx = c_idx
            else:
                if ("result" in txt or "結果" in txt or "结果" in txt or re.search(r"00[1-9]", txt)):
                    if result_idx == -1: result_idx = c_idx
    
    if result_idx == -1 and company == "SGS":
        if mdl_idx != -1 and mdl_idx + 1 < len(table[0]):
            result_idx = mdl_idx + 1

    is_reference_table = False
    if is_msds_table: is_reference_table = True
    elif result_idx == -1:
        if "restricted substances" in full_header_text or "group name" in full_header_text or "substance name" in full_header_text:
            is_reference_table = True
        if company == "INTERTEK" and "limits" in full_header_text:
            is_reference_table = True
        if item_idx == -1:
            is_reference_table = True

    return item_idx, result_idx, is_reference_table

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
            
            if key == "Cd" and any(bad in line_lower for bad in ["hbcdd", "cyclododecane", "ecd"]): continue 
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
            item_idx, result_idx, is_skip = identify_columns_v60(table, company)
            if is_skip: continue
            
            for row in table:
                clean_row = [clean_text(cell) for cell in row]
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
                    if target_key == "Cd" and any(bad in item_name_lower for bad in ["hbcdd", "cyclododecane", "ecd"]): continue
                    if target_key == "F" and any(bad in item_name_lower for bad in ["perfluoro", "polyfluoro", "pfos", "pfoa", "全氟"]): continue
                    if target_key == "BR" and any(bad in item_name_lower for bad in ["polybromo", "hexabromo", "monobromo", "dibromo", "tribromo", "tetrabromo", "pentabromo", "heptabromo", "octabromo", "nonabromo", "decabromo", "multibromo", "pbb", "pbde", "多溴", "六溴", "一溴", "二溴", "三溴", "四溴", "五溴", "七溴", "八溴", "九溴", "十溴", "二苯醚"]): continue
                    if target_key == "Pb" and any(bad in item_name_lower for bad in ["pbb", "pbde", "polybrominated", "多溴"]): continue

                    for kw in keywords:
                        if kw.lower() in item_name_lower:
                            if target_key == "PFOS" and "related" in item_name_lower: continue 
                            data_pool[target_key].append({"priority": priority, "filename": filename})
                            break
                for group_key, keywords in GROUP_KEYWORDS.items():
                    for kw in keywords:
                        if kw.lower() in item_name_lower:
                            file_group_data[group_key].append(priority)
                            break

    # Text Rescue (SGS Only)
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
# 4. CTI 專用引擎 (v63.11: Manual Parsing + Max Date Priority)
# =============================================================================

def extract_dates_v63_11_cti(text):
    """
    v63.11: 使用手動月份解析，避開 Locale 問題；保留 Max Date 邏輯。
    """
    lines = text.split('\n')
    candidates = []
    
    bonus_kw = ["report date", "issue date", "date:", "dated", "日期", "签发日期"]
    poison_kw = ["approve", "approved", "receive", "received", "receipt", "expiry", "valid", "收样"]
    backup_kw = ["testing period", "test period", "检测周期", "测试周期"]

    # 英文 MDY (暴力清洗後) -> 匹配小寫 mar 15 2022
    pat_mdy_clean = r"\b([a-z]{3,})\s+(\d{1,2})\s+(20\d{2})\b"
    pat_chinese = r"(20\d{2})\s*年\s*(0?[1-9]|1[0-2])\s*月\s*(3[01]|[12][0-9]|0?[1-9])\s*日"
    pat_dot = r"(20\d{2})\.(0?[1-9]|1[0-2])\.(3[01]|[12][0-9]|0?[1-9])"

    for i, line in enumerate(lines):
        line_lower = line.lower()
        # 強力清洗：非英數轉空格，轉小寫
        clean_line = re.sub(r'[^a-z0-9]', ' ', line_lower)
        
        # 1. 中文與點號格式 (標準匹配)
        for pat in [pat_chinese, pat_dot]:
            matches = re.finditer(pat, line)
            for m in matches:
                try:
                    dt = datetime.strptime(f"{m.group(1)}-{m.group(2)}-{m.group(3)}", "%Y-%m-%d")
                    if is_valid_date(dt): 
                        score = 1
                        if any(bad in line_lower for bad in poison_kw): score = -1000
                        elif any(good in line_lower for good in bonus_kw): score = 100
                        elif any(back in line_lower for back in backup_kw): score = 10
                        candidates.append((score, dt))
                except: pass

        # 2. 英文 MDY (手動解析)
        matches_mdy = re.finditer(pat_mdy_clean, clean_line)
        for m in matches_mdy:
            try:
                mon_str = m.group(1) # e.g. 'mar'
                day_str = m.group(2)
                year_str = m.group(3)
                
                # 查表轉換月份
                if mon_str in MONTH_MAP:
                    month_num = MONTH_MAP[mon_str]
                    dt = datetime(int(year_str), month_num, int(day_str))
                    
                    if is_valid_date(dt): 
                        score = 1
                        if any(bad in line_lower for bad in poison_kw): score = -1000
                        elif any(good in line_lower for good in bonus_kw): score = 100
                        elif any(back in line_lower for back in backup_kw): score = 10
                        candidates.append((score, dt))
            except: pass
        
        # 3. YMD 補漏
        # clean_line 已經全是空格分隔的數字和英文
        matches_ymd = re.finditer(r"\b(20\d{2})\s+(0?[1-9]|1[0-2])\s+(3[01]|[12][0-9]|0?[1-9])\b", clean_line)
        for m in matches_ymd:
            try:
                dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                if is_valid_date(dt): 
                    score = 1
                    if any(bad in line_lower for bad in poison_kw): score = -1000
                    elif any(good in line_lower for good in bonus_kw): score = 100
                    elif any(back in line_lower for back in backup_kw): score = 10
                    candidates.append((score, dt))
            except: pass

    return candidates

def process_cti_engine(pdf, filename):
    data_pool = {key: [] for key in OUTPUT_COLUMNS if key not in ["日期", "檔案名稱"]}
    
    text_for_dates = ""
    for p in pdf.pages[:3]: text_for_dates += (p.extract_text() or "") + "\n"
    
    # 使用 v63.11 手動解析版
    date_candidates = extract_dates_v63_11_cti(text_for_dates)
    final_dates = []
    
    if date_candidates:
        # v63.10/11 核心: 篩選正分日期，然後取最大(最晚)的日期
        valid_entries = [entry for entry in date_candidates if entry[0] > 0]
        
        if valid_entries:
            # 排序規則: 日期越晚越大 (reverse=True)
            best_entry = sorted(valid_entries, key=lambda x: (x[1], x[0]), reverse=True)[0]
            final_dates.append(best_entry)

    # 2. 表格解析 (維持 v62.8 鹵素邏輯)
    for page in pdf.pages:
        tables = page.extract_tables()
        for table in tables:
            if not table or len(table) < 2: continue
            
            mdl_col_idx = -1
            cols = len(table[0])
            for c in range(cols):
                num_count = 0
                row_count = 0
                for r in range(1, len(table)):
                    val = clean_text(table[r][c]).replace("mg/kg", "").replace("~", "").replace("$", "").replace("%", "").strip()
                    if not val: continue
                    row_count += 1
                    if val in ["2", "5", "8", "10", "50", "100", "0.01", "0.010", "0.005", "20", "25"]: num_count += 1
                
                if row_count > 0 and (num_count / row_count) >= 0.5:
                    mdl_col_idx = c
                    break
            
            result_col_idx = -1
            if mdl_col_idx > 0:
                result_col_idx = mdl_col_idx - 1
            else:
                for c in range(cols):
                    if "result" in str(table[0][c]).lower() or "结果" in str(table[0][c]):
                        result_col_idx = c
                        break
            
            if result_col_idx == -1: continue

            for row in table:
                if len(row) <= result_col_idx: continue
                
                item_text = " ".join([str(x) for x in row[:result_col_idx] if x]).lower()
                
                raw_res = str(row[result_col_idx])
                final_val = None
                if re.search(r"(?i)(\bN\.?D\.?|\bNot Detected)", raw_res):
                    final_val = "N.D."
                else:
                    nums = re.findall(r"\d+(?:\.\d+)?", raw_res)
                    if nums: final_val = nums[0] 
                
                if not final_val: continue
                priority = parse_value_priority(final_val)
                if priority[0] == 0: continue

                for key, kws in SIMPLE_KEYWORDS.items():
                    if key == "Cd" and any(bad in item_text for bad in ["hbcdd", "cyclododecane", "ecd"]): continue 
                    if key == "F" and any(bad in item_text for bad in ["perfluoro", "polyfluoro", "pfos", "pfoa", "全氟"]): continue
                    if key == "BR" and any(bad in item_text for bad in ["polybromo", "hexabromo", "monobromo", "dibromo", "tribromo", "tetrabromo", "pentabromo", "heptabromo", "octabromo", "nonabromo", "decabromo", "multibromo", "pbb", "pbde", "多溴", "六溴", "一溴", "二溴", "三溴", "四溴", "五溴", "七溴", "八溴", "九溴", "十溴", "二苯醚"]): continue
                    if key == "Pb" and any(bad in item_text for bad in ["pbb", "pbde", "polybrominated", "多溴"]): continue

                    if any(kw.lower() in item_text for kw in kws):
                        data_pool[key].append({"priority": priority, "filename": filename})
                        break
                for key, kws in GROUP_KEYWORDS.items():
                    if any(kw.lower() in item_text for kw in kws):
                        data_pool[key].append({"priority": priority, "filename": filename})
                        break

    return data_pool, final_dates

# =============================================================================
# 5. 馬來西亞專用引擎 (v61.1 邏輯)
# =============================================================================

def extract_date_malaysia(text):
    lines = text.split('\n')
    for line in lines:
        if "REPORTED DATE" in line.upper():
            if "JOB REF" in line.upper(): continue
            pat = r"(0?[1-9]|[12][0-9]|3[01])[\s-]([a-zA-Z]{3,})[\s-](20\d{2})"
            match = re.search(pat, line)
            if match:
                dt_str = f"{match.group(1)} {match.group(2)} {match.group(3)}"
                for fmt in ["%d %B %Y", "%d %b %Y"]:
                    try:
                        return datetime.strptime(dt_str, fmt)
                    except: pass
    return None

def process_malaysia_engine(pdf, filename):
    data_pool = {key: [] for key in OUTPUT_COLUMNS if key not in ["日期", "檔案名稱"]}
    full_text = ""
    for p in pdf.pages: full_text += (p.extract_text() or "") + "\n"
    
    dt = extract_date_malaysia(full_text)
    date_candidates = []
    if dt: date_candidates.append((100, dt))

    # RoHS2 (Table Anchor)
    for page in pdf.pages:
        tables = page.extract_tables()
        for table in tables:
            if not table or len(table) < 2: continue
            mdl_col = -1
            cols = len(table[0])
            for c in range(cols):
                num_cnt = 0
                row_cnt = 0
                for r in range(1, len(table)):
                    val = clean_text(table[r][c])
                    if not val: continue
                    row_cnt += 1
                    if val in ["2", "5", "8", "10", "50"]: num_cnt += 1
                if row_cnt > 0 and (num_cnt / row_cnt) >= 0.5:
                    mdl_col = c
                    break
            
            if mdl_col > 0:
                result_col = mdl_col - 1
                for row in table:
                    if len(row) <= mdl_col: continue
                    item_text = " ".join([str(x) for x in row[:result_col] if x]).lower()
                    raw_res = str(row[result_col])
                    final_val = None
                    if re.search(r"(?i)(\bN\.?D\.?|\bNot Detected|\bNegative)", raw_res):
                        final_val = "N.D."
                    else:
                        nums = re.findall(r"\d+(?:\.\d+)?", raw_res)
                        for num in nums:
                            if num in ["62321", "2013", "2015", "2017", "2020"]: continue
                            final_val = num
                            break
                    
                    if not final_val: continue
                    priority = (10, 0, final_val) 

                    for key, kws in SIMPLE_KEYWORDS.items():
                        if any(kw.lower() in item_text for kw in kws):
                            if key == "Cd" and "hexabromocyclododecane" in item_text: continue
                            data_pool[key].append({"priority": priority, "filename": filename})
                            break
                    for key, kws in GROUP_KEYWORDS.items():
                        if any(kw.lower() in item_text for kw in kws):
                            data_pool[key].append({"priority": priority, "filename": filename})
                            break

    # HF (Block Search)
    ft_lower = full_text.lower()
    targets = {"F": "fluorine", "CL": "chlorine", "BR": "bromine", "I": "iodine"}
    for key, kw in targets.items():
        if not data_pool[key]:
            idx = ft_lower.find(kw)
            if idx != -1:
                window = ft_lower[idx:idx+300]
                if "n.d." in window:
                    data_pool[key].append({"priority": (10, 0, "N.D."), "filename": filename})
                else:
                    nums = re.findall(r"\b\d+\b", window)
                    found_num = ""
                    for n in nums:
                        if n == "50": continue
                        if len(n) == 1: continue
                        if n[:4] in ["2020", "2021", "2024", "2025"]: continue
                        if n == "62321": continue
                        found_num = n
                        break
                    if found_num:
                        data_pool[key].append({"priority": (5, float(found_num), found_num), "filename": filename})

    return data_pool, date_candidates

# =============================================================================
# 6. 主程式與分流器
# =============================================================================

def process_files(files):
    results = []
    progress_bar = st.progress(0)
    
    for i, file in enumerate(files):
        try:
            with pdfplumber.open(file) as pdf:
                first_page_text = (pdf.pages[0].extract_text() or "").upper()
                company = identify_company(first_page_text)
                
                if "MALAYSIA" in first_page_text and "SGS" in first_page_text:
                    data_pool, date_candidates = process_malaysia_engine(pdf, file.name)
                elif company == "CTI":
                    data_pool, date_candidates = process_cti_engine(pdf, file.name)
                else:
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
                        best = sorted(candidates, key=lambda x: (x['priority'][0], x['priority'][1]), reverse=True)[0]
                        final_row[k] = best['priority'][2]
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
        if "test report" in text or "測試報告" in text:
            return i
    return 0

# =============================================================================
# 7. UI
# =============================================================================

st.set_page_config(page_title="SGS/CTI 報告聚合工具 v63.11", layout="wide")
st.title("📄 萬用型檢測報告聚合工具 (v63.11 CTI 語系獨立終極版)")
st.info("💡 v63.11：徹底解決月份語系解析問題 (Locale Issue)，並維持日期最大化邏輯，確保精準抓取 CTI 報告日期。")

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
            file_name="SGS_CTI_Summary_v63.11.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        
    except Exception as e:
        st.error(f"系統錯誤: {e}")
