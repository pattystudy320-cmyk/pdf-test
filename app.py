import streamlit as st
import pdfplumber
import pandas as pd
import io
import re
from datetime import datetime

# --- 1. 定義欄位與關鍵字 ---

SIMPLE_KEYWORDS = {
    "Pb": ["Lead", "鉛", "Pb"],
    "Cd": ["Cadmium", "鎘", "Cd"],
    "Hg": ["Mercury", "汞", "Hg"],
    "Cr6+": ["Hexavalent Chromium", "六價鉻", "Cr(VI)", "Chromium VI", "Hexavalent Chromium"],
    "DEHP": ["DEHP", "Di(2-ethylhexyl) phthalate", "Bis(2-ethylhexyl) phthalate"],
    "BBP": ["BBP", "Butyl benzyl phthalate"],
    "DBP": ["DBP", "Dibutyl phthalate"],
    "DIBP": ["DIBP", "Diisobutyl phthalate"],
    "PFOS": ["Perfluorooctane sulfonates", "Perfluorooctane sulfonate", "Perfluorooctane sulfonic acid", "全氟辛烷磺酸", "Perfluorooctane Sulfonamide"],
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

OUTPUT_COLUMNS = [
    "Pb", "Cd", "Hg", "Cr6+", "PBB", "PBDE", 
    "DEHP", "BBP", "DBP", "DIBP", 
    "PFOS", "PFAS", "F", "CL", "BR", "I", 
    "日期", "檔案名稱"
]

# --- 2. 輔助功能 ---

def clean_text(text):
    if not text: return ""
    return str(text).replace('\n', ' ').strip()

def find_report_start_page(pdf):
    for i in range(min(10, len(pdf.pages))):
        text = (pdf.pages[i].extract_text() or "").lower()
        if "test report" in text or "測試報告" in text:
            return i
    return 0

def extract_dates_v60_8(text):
    """
    v60.8: 支援 Reported Date, 排除 Job Ref/Ref No/Date of expiry
    """
    lines = text.split('\n')
    candidates = [] # (score, date_object)
    
    bonus_kw = ["report date", "issue date", "date:", "dated", "日期", "reported date"]
    poison_kw = [
        "approve", "approved", "approval", "approver", 
        "check", "checked", "review", "reviewed",      
        "receive", "received", "receipt",
        "period", "testing period", "started", "from", "to ",
        "承認", "核准", "檢驗", "收件", "接收", "有效", "expiry", "valid", "期間", "周期", "時間",
        "effective date", "job ref", "ref no", "date of expiry"
    ]

    pat_ymd = r"(20\d{2})\s+(0?[1-9]|1[0-2])\s+(0?[1-9]|[12][0-9]|3[01])"
    pat_dmy = r"(0?[1-9]|[12][0-9]|3[01])\s+([a-zA-Z]{3,})\s+(20\d{2})"
    pat_mdy = r"([a-zA-Z]{3,})\s+(0?[1-9]|[12][0-9]|3[01])\s+(20\d{2})"

    for line in lines:
        line_lower = line.lower()
        score = 1
        if any(bad in line_lower for bad in poison_kw):
            score = -100 
        elif any(good in line_lower for good in bonus_kw):
            score = 100 

        clean_line = line.replace(".", " ").replace(",", " ").replace("-", " ").replace("/", " ")
        clean_line = clean_line.replace("年", " ").replace("月", " ").replace("日", " ")
        clean_line = " ".join(clean_line.split())

        found_dt = None
        for pat in [pat_ymd, pat_dmy, pat_mdy]:
            matches = re.finditer(pat, clean_line, re.IGNORECASE)
            for match in matches:
                try:
                    groups = match.groups()
                    date_str = " ".join(groups)
                    for fmt in ["%Y %m %d", "%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y"]:
                        try:
                            dt = datetime.strptime(date_str, fmt)
                            if 2000 <= dt.year <= 2030:
                                found_dt = dt
                                break
                        except: continue
                    if found_dt: break
                except: continue
            if found_dt: break
        
        if found_dt:
            candidates.append((score, found_dt))
            
    return candidates

def is_suspicious_limit_value(val):
    try:
        n = float(val)
        # v60.8: 保留常見 MDL/Limit 數字
        if n in [1000.0, 100.0, 50.0, 25.0, 10.0, 8.0, 5.0, 2.0]: return True
        return False
    except: return False

def is_unit_or_method(val):
    val_lower = val.lower()
    if any(u in val_lower for u in ["mg/kg", "ppm", "%", "unit"]): return True
    if any(m in val_lower for m in ["iec", "iso", "epa", "reference", "with", "analysis", "performed", "method"]): return True
    return False

def parse_value_priority(value_str):
    if not value_str: return (0, 0, "")
    # v60.8: 內容清洗，只取第一行 (針對 N.D.\n(Not Detected))
    raw_val = str(value_str).split('\n')[0].strip()
    
    if "(" in raw_val and ")" in raw_val:
        if re.search(r"\(\d+\)", raw_val):
            raw_val = raw_val.split("(")[0].strip()
    val = raw_val.replace("mg/kg", "").replace("ppm", "").replace("%", "").replace("µg/cm²", "").strip()
    
    if not val: return (0, 0, "")
    val_lower = val.lower()
    
    if val_lower in ["result", "limit", "mdl", "loq", "rl", "unit", "method", "004", "001", "no.1", "---", "-", "limits", "n.a.", "/"]: 
        return (0, 0, "")
    if re.search(r"\d+-\d+-\d+", val): return (0, 0, "") 
    
    if is_unit_or_method(val): return (0, 0, "")

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

def check_pfas_in_summary(text):
    txt_lower = text.lower()
    for kw in PFAS_SUMMARY_KEYWORDS:
        if kw.lower() in txt_lower: return True
    return False

def identify_company(text):
    txt = text.lower()
    if "sgs" in txt: return "SGS"
    if "intertek" in txt: return "INTERTEK"
    if "cti" in txt or "centre testing" in txt: return "CTI"
    if "ctic" in txt: return "CTIC"
    return "OTHERS"

# --- 3. 核心：表格識別 ---

def identify_columns_by_company(table, company):
    item_idx = -1
    result_idx = -1
    mdl_idx = -1
    limit_idx = -1
    
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
            
            # v60.8: 模糊匹配 Test Parameter，只要同時有 test 和 parameter 就算
            if "test item" in txt or "tested item" in txt or "測試項目" in txt or "检测项目" in txt:
                if item_idx == -1: item_idx = c_idx
            elif "test" in txt and "parameter" in txt: # v60.8: 針對馬來西亞版
                if item_idx == -1: item_idx = c_idx
                
            if "mdl" in txt or "loq" in txt:
                if mdl_idx == -1: mdl_idx = c_idx
            if "limit" in txt or "限值" in txt:
                if limit_idx == -1: limit_idx = c_idx
                
            is_bad_header = any(bad in txt for bad in MSDS_HEADER_KEYWORDS)
            if not is_bad_header:
                if company == "SGS":
                     if ("result" in txt or "結果" in txt or "结果" in txt or re.search(r"00[1-9]", txt) or 
                        re.search(r"^[a-z]?\s*-?\s*\d+$", txt) or "no." in txt):
                        # v60.8: 更嚴格的 result 判定，防止 method 被誤判
                        if "cas" not in txt and "method" not in txt and "limit" not in txt:
                            if result_idx == -1: result_idx = c_idx
                else:
                    if ("result" in txt or "結果" in txt or "结果" in txt or re.search(r"00[1-9]", txt)):
                        if result_idx == -1: result_idx = c_idx
    
    is_reference_table = False
    if is_msds_table: is_reference_table = True
    elif result_idx == -1 and mdl_idx == -1:
        if "restricted substances" in full_header_text or "group name" in full_header_text or "substance name" in full_header_text:
            is_reference_table = True
        if company == "INTERTEK" and "limits" in full_header_text:
            is_reference_table = True
        if item_idx == -1:
            is_reference_table = True

    return item_idx, result_idx, mdl_idx, is_reference_table

# --- 4. 核心：文字模式 ---

def parse_text_lines(text, data_pool, file_group_data, filename, company, targets=None):
    lines = text.split('\n')
    for line in lines:
        line_clean = clean_text(line)
        line_lower = line_clean.lower()
        if not line_clean: continue
        if any(bad in line_lower for bad in MSDS_HEADER_KEYWORDS): continue

        matched_simple = None
        for key, keywords in SIMPLE_KEYWORDS.items():
            if targets and key not in targets: continue
            
            # --- v60.8: 事前掃毒 ---
            if key == "Cd" and any(bad in line_lower for bad in ["hbcdd", "cyclododecane", "ecd", "indeno", "pyrene"]): 
                continue 
            if key == "F" and any(bad in line_lower for bad in ["perfluoro", "polyfluoro", "pfos", "pfoa", "全氟"]): 
                continue
            if key == "BR" and any(bad in line_lower for bad in ["polybromo", "hexabromo", "monobromo", "dibromo", "tribromo", "tetrabromo", "pentabromo", "heptabromo", "octabromo", "nonabromo", "decabromo", "multibromo", "pbb", "pbde", "多溴", "六溴", "一溴", "二溴", "三溴", "四溴", "五溴", "七溴", "八溴", "九溴", "十溴", "二苯醚"]): 
                continue
            if key == "Pb" and any(bad in line_lower for bad in ["pbb", "pbde", "polybrominated", "多溴"]):
                continue

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
                if p_lower in ["mg/kg", "ppm", "2", "5", "10", "50", "100", "1000", "0.1", "-", "---", "unit", "mdl", "iec", "method"]: continue
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

# --- 主程式 ---

def process_files(files):
    data_pool = {key: [] for key in OUTPUT_COLUMNS if key not in ["日期", "檔案名稱"]}
    all_file_valid_dates = [] 
    global_tracker = {"Pb": {"max_score": -1, "max_value": -1.0, "filename": ""}}
    
    progress_bar = st.progress(0)
    
    for i, file in enumerate(files):
        filename = file.name
        file_group_data = {key: [] for key in GROUP_KEYWORDS.keys()}
        
        try:
            with pdfplumber.open(file) as pdf:
                start_page_idx = find_report_start_page(pdf)
                company = "OTHERS"
                first_page_text = ""
                full_text_content = ""
                
                if len(pdf.pages) > start_page_idx:
                    first_page_text = pdf.pages[start_page_idx].extract_text() or ""
                    company = identify_company(first_page_text)
                    if check_pfas_in_summary(first_page_text):
                        data_pool["PFAS"].append({"priority": (4, 0, "REPORT"), "filename": filename})

                # 1. 日期提取
                file_dates_candidates = []
                for p_idx in range(start_page_idx, len(pdf.pages)):
                    page = pdf.pages[p_idx]
                    page_txt = page.extract_text() or ""
                    full_text_content += page_txt + "\n"
                    if p_idx < start_page_idx + 5:
                        dates = extract_dates_v60_8(page_txt)
                        file_dates_candidates.extend(dates)
                
                if file_dates_candidates:
                    valid_candidates = [d for d in file_dates_candidates if d[0] > -50]
                    if valid_candidates:
                        best_date = sorted(valid_candidates, key=lambda x: (x[0], x[1]), reverse=True)[0]
                        all_file_valid_dates.append(best_date[1])

                # 2. 引擎 A: 表格模式
                for p_idx in range(start_page_idx, len(pdf.pages)):
                    page = pdf.pages[p_idx]
                    tables = page.extract_tables()
                    for table in tables:
                        if not table or len(table) < 2: continue
                        item_idx, result_idx, mdl_idx, is_skip_table = identify_columns_by_company(table, company)
                        if is_skip_table: continue 
                        
                        for row_idx, row in enumerate(table):
                            clean_row = [clean_text(cell) for cell in row]
                            row_txt = "".join(clean_row).lower()
                            if "test item" in row_txt or "result" in row_txt or "restricted" in row_txt: continue
                            if not any(clean_row): continue
                            
                            target_item_col = item_idx if item_idx != -1 else 0
                            if target_item_col >= len(clean_row): continue
                            item_name = clean_row[target_item_col]
                            item_name_lower = item_name.lower()
                            
                            if "pvc" in item_name_lower or "polyvinyl" in item_name_lower: continue

                            result = ""
                            
                            # v60.8: 優先使用標題座標鎖定 (最穩定)
                            if result_idx != -1 and result_idx < len(clean_row):
                                result = clean_row[result_idx]
                            
                            # v60.8: 智慧 MDL 左右偵測 (備案：當標題找不到時)
                            if (not result or parse_value_priority(result)[0] == 0) and mdl_idx != -1:
                                # 往左找
                                for step in [1, 2]:
                                    if mdl_idx - step >= 0:
                                        val = clean_row[mdl_idx - step]
                                        if is_unit_or_method(val): continue
                                        if parse_value_priority(val)[0] > 0:
                                            result = val
                                            break
                                # 往右找
                                if not result or parse_value_priority(result)[0] == 0:
                                    for step in [1, 2]:
                                        if mdl_idx + step < len(clean_row):
                                            val = clean_row[mdl_idx + step]
                                            if is_unit_or_method(val): continue
                                            if parse_value_priority(val)[0] > 0:
                                                result = val
                                                break

                            # v60.8: 最後防線 - 智慧行掃描 (清洗內容後判定)
                            temp_priority = parse_value_priority(result)
                            if temp_priority[0] == 0: 
                                found_better = False
                                for cell in reversed(clean_row):
                                    c_lower = cell.lower()
                                    if not cell: continue
                                    if "nd" in c_lower or "n.d." in c_lower or "negative" in c_lower:
                                        result = cell
                                        found_better = True
                                        break
                                    if re.search(r"^\d+(\.\d+)?", cell):
                                        if is_suspicious_limit_value(cell): continue 
                                        result = cell
                                        found_better = True
                                        break
                                if not found_better and result_idx == -1: 
                                    pass 

                            priority = parse_value_priority(result)
                            if priority[0] == 0: continue 

                            # 匹配邏輯
                            for target_key, keywords in SIMPLE_KEYWORDS.items():
                                if target_key == "Cd" and any(bad in item_name_lower for bad in ["hbcdd", "cyclododecane", "ecd", "indeno", "pyrene"]): 
                                    continue
                                if target_key == "F" and any(bad in item_name_lower for bad in ["perfluoro", "polyfluoro", "pfos", "pfoa", "全氟"]): 
                                    continue
                                if target_key == "BR" and any(bad in item_name_lower for bad in ["polybromo", "hexabromo", "monobromo", "dibromo", "tribromo", "tetrabromo", "pentabromo", "heptabromo", "octabromo", "nonabromo", "decabromo", "multibromo", "pbb", "pbde", "多溴", "六溴", "一溴", "二溴", "三溴", "四溴", "五溴", "七溴", "八溴", "九溴", "十溴", "二苯醚"]): 
                                    continue
                                if target_key == "Pb" and any(bad in item_name_lower for bad in ["pbb", "pbde", "polybrominated", "多溴"]):
                                    continue

                                for kw in keywords:
                                    if kw.lower() in item_name_lower:
                                        if target_key == "PFOS" and "related" in item_name_lower: continue 
                                        data_pool[target_key].append({"priority": priority, "filename": filename})
                                        if target_key == "Pb":
                                            score, val = priority[0], priority[1]
                                            if score > global_tracker["Pb"]["max_score"]:
                                                global_tracker["Pb"]["max_score"] = score
                                                global_tracker["Pb"]["max_value"] = val
                                                global_tracker["Pb"]["filename"] = filename
                                            elif score == global_tracker["Pb"]["max_score"] and val > global_tracker["Pb"]["max_value"]:
                                                global_tracker["Pb"]["max_value"] = val
                                                global_tracker["Pb"]["filename"] = filename
                                        break

                            for group_key, keywords in GROUP_KEYWORDS.items():
                                for kw in keywords:
                                    if kw.lower() in item_name_lower:
                                        file_group_data[group_key].append(priority)
                                        break
                
                # 3. 引擎 B: 文字模式 (v60.8 擴大救援)
                missing_targets = []
                pb_data = [d for d in data_pool["Pb"] if d['filename'] == filename]
                if not pb_data: missing_targets.append("Pb")
                
                halogen_data = []
                for h in ["F", "CL", "BR", "I"]:
                    halogen_data.extend([d for d in data_pool[h] if d['filename'] == filename])
                
                pfos_data = [d for d in data_pool["PFOS"] if d['filename'] == filename]
                
                trigger_rescue = False
                if company == "SGS":
                    if not pb_data: trigger_rescue = True
                    # v60.8: Combustion + Chromatography 救援 (忽略 Ion)
                    ft_lower = full_text_content.lower()
                    if ("halogen" in ft_lower or "卤素" in ft_lower or ("combustion" in ft_lower and "chromatography" in ft_lower)) and not halogen_data:
                        trigger_rescue = True
                        missing_targets.extend(["F", "CL", "BR", "I"])
                    if "pfos" in ft_lower and not pfos_data:
                        trigger_rescue = True
                        missing_targets.append("PFOS")

                if trigger_rescue:
                     parse_text_lines(full_text_content, data_pool, file_group_data, filename, company, targets=None)
                     
                     for d in data_pool["Pb"]:
                         if d['filename'] == filename:
                             p = d['priority']
                             if p[0] > global_tracker["Pb"]["max_score"]:
                                 global_tracker["Pb"]["max_score"] = p[0]
                                 global_tracker["Pb"]["max_value"] = p[1]
                                 global_tracker["Pb"]["filename"] = filename
                             elif p[0] == global_tracker["Pb"]["max_score"] and p[1] > global_tracker["Pb"]["max_value"]:
                                 global_tracker["Pb"]["max_value"] = p[1]
                                 global_tracker["Pb"]["filename"] = filename

            # 4. 結算
            for group_key, values in file_group_data.items():
                if values:
                    best_in_file = sorted(values, key=lambda x: (x[0], x[1]), reverse=True)[0]
                    data_pool[group_key].append({"priority": best_in_file, "filename": filename})

        except Exception as e:
            st.warning(f"檔案 {filename} 解析異常: {e}")
        progress_bar.progress((i + 1) / len(files))

    # 5. 聚合
    final_row = {}
    for key in OUTPUT_COLUMNS:
        if key in ["日期", "檔案名稱"]: continue
        candidates = data_pool.get(key, [])
        if not candidates:
            final_row[key] = "" 
            continue
        best_record = sorted(candidates, key=lambda x: (x['priority'][0], x['priority'][1]), reverse=True)[0]
        final_row[key] = best_record['priority'][2]

    final_date_str = ""
    if all_file_valid_dates:
        latest_date = max(all_file_valid_dates)
        final_date_str = latest_date.strftime("%Y/%m/%d")
    
    final_row["日期"] = final_date_str
    
    if global_tracker["Pb"]["filename"]:
        final_row["檔案名稱"] = global_tracker["Pb"]["filename"]
    else:
        final_row["檔案名稱"] = files[0].name if files else ""

    return [final_row]

# --- 介面 ---
st.set_page_config(page_title="SGS 報告聚合工具 v60.8", layout="wide")
st.title("📄 萬用型檢測報告聚合工具 (v60.8 座標鎖定/強效清洗版)")
st.info("💡 v60.8：採用「標題座標鎖定」與「強制內容清洗」技術，徹底解決 SGS 馬來西亞版欄位偏移與 OCR 換行問題。")

uploaded_files = st.file_uploader("請一次選取所有 PDF 檔案", type="pdf", accept_multiple_files=True)

if uploaded_files:
    if st.button("🔄 重新執行"): st.rerun()

    try:
        result_data = process_files(uploaded_files)
        df = pd.DataFrame(result_data)
        for col in OUTPUT_COLUMNS:
            if col not in df.columns: df[col] = ""
        df = df[OUTPUT_COLUMNS]

        st.success("✅ 處理完成！")
        st.dataframe(df)

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Summary')
        
        st.download_button("📥 下載 Excel", data=output.getvalue(), file_name="SGS_Summary_v60.8.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        
    except Exception as e:
        st.error(f"系統錯誤: {e}")
