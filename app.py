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
    "Cr6+": ["Hexavalent Chromium", "六價鉻", "Cr(VI)", "Chromium VI"],
    "DEHP": ["DEHP", "Di(2-ethylhexyl) phthalate", "Bis(2-ethylhexyl) phthalate"],
    "BBP": ["BBP", "Butyl benzyl phthalate"],
    "DBP": ["DBP", "Dibutyl phthalate"],
    "DIBP": ["DIBP", "Diisobutyl phthalate"],
    "PFOS": ["Perfluorooctane sulfonates", "Perfluorooctane sulfonate", "Perfluorooctane sulfonic acid", "全氟辛烷磺酸"],
    "F": ["Fluorine", "氟"],
    "CL": ["Chlorine", "氯"],
    "BR": ["Bromine", "溴"],
    "I": ["Iodine", "碘"]
}

GROUP_KEYWORDS = {
    "PBB": [
        "Polybrominated Biphenyls", "PBBs", "Sum of PBBs", "多溴聯苯總和",
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
        "Polybrominated Diphenyl Ethers", "PBDEs", "Sum of PBDEs", "多溴聯苯醚總和",
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

def extract_date_from_text(text):
    # V34 原始日期邏輯：抓取像日期的格式，取最大值
    text = clean_text(text)
    patterns = [
        r"(20\d{2})[/\.-](0?[1-9]|1[0-2])[/\.-](0?[1-9]|[12][0-9]|3[01])", 
        r"(0?[1-9]|[12][0-9]|3[01])\s*[-/]\s*([a-zA-Z]{3})\s*[-/]\s*(20\d{2})", 
        r"([a-zA-Z]{3})\.?\s+(0?[1-9]|[12][0-9]|3[01])[,\s]+\s*(20\d{2})" 
    ]
    found_dates = []
    for pattern in patterns:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            try:
                dt = None
                full_match = match.group(0)
                clean_str = full_match.replace(".", " ").replace(",", " ").replace("-", " ").replace("/", " ")
                clean_str = " ".join(clean_str.split())
                for fmt in ["%Y %m %d", "%d %b %Y", "%b %d %Y"]:
                    try:
                        dt = datetime.strptime(clean_str, fmt)
                        break
                    except: continue
                if dt and 2000 <= dt.year <= 2030: 
                    found_dates.append(dt)
            except: continue
    if found_dates: return max(found_dates)
    return None

def is_suspicious_limit_value(val):
    try:
        n = float(val)
        if n in [1000.0, 100.0, 50.0]: return True
        return False
    except: return False

def parse_value_priority(value_str):
    raw_val = clean_text(value_str)
    
    # 移除括號內的單位
    if "(" in raw_val and ")" in raw_val:
        if re.search(r"\(\d+\)", raw_val):
            raw_val = raw_val.split("(")[0].strip()
    
    val = raw_val.replace("mg/kg", "").replace("ppm", "").replace("%", "").replace("µg/cm²", "").strip()
    
    if not val: return (0, 0, "")
    val_lower = val.lower()

    if val_lower in ["result", "limit", "mdl", "loq", "rl", "unit", "method", "004", "001", "no.1", "---", "-", "limits"]: 
        return (0, 0, "")

    if re.search(r"\d+-\d+-\d+", val): return (0, 0, "") 
    
    # 數值檢查
    num_only_match = re.search(r"([\d\.]+)", val)
    if num_only_match:
        if is_suspicious_limit_value(num_only_match.group(1)): return (0, 0, "")

    if "nd" in val_lower or "n.d." in val_lower or "<" in val_lower: return (1, 0, "N.D.")
    if "negative" in val_lower or "陰性" in val_lower: return (2, 0, "NEGATIVE")
    
    # ★ V34 關鍵：支援帶符號的數值 (如 186802 ▲)
    num_match = re.search(r"^([\d\.]+)(.*)$", val)
    if num_match:
        try:
            number = float(num_match.group(1))
            full_str = val 
            return (3, number, full_str)
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

    for r_idx in range(max_scan_rows):
        row = table[r_idx]
        for c_idx, cell in enumerate(row):
            txt = clean_text(cell).lower()
            if not txt: continue
            
            if "test item" in txt or "tested item" in txt or "測試項目" in txt:
                if item_idx == -1: item_idx = c_idx
            if "mdl" in txt or "loq" in txt:
                if mdl_idx == -1: mdl_idx = c_idx
            if "limit" in txt or "限值" in txt:
                if limit_idx == -1: limit_idx = c_idx

            if company == "SGS":
                if ("result" in txt or "結果" in txt or re.search(r"00[1-9]", txt) or 
                    re.search(r"^[a-z]?\s*-?\s*\d+$", txt) or "no." in txt):
                    if "cas" not in txt and "method" not in txt and "limit" not in txt:
                        if result_idx == -1: result_idx = c_idx
            elif company == "INTERTEK":
                if "result" in txt or "green" in txt or "submitted" in txt:
                    if result_idx == -1: result_idx = c_idx
            else: 
                if "result" in txt or "結果" in txt or re.search(r"00[1-9]", txt):
                    if result_idx == -1: result_idx = c_idx

    if result_idx == -1:
        if company == "SGS":
            if mdl_idx != -1 and mdl_idx + 1 < len(table[0]):
                result_idx = mdl_idx + 1
    
    is_reference_table = False
    if result_idx == -1:
        if "restricted substances" in full_header_text or "group name" in full_header_text or "substance name" in full_header_text:
            is_reference_table = True
        if company == "INTERTEK" and "limits" in full_header_text:
            is_reference_table = True
        if item_idx == -1:
            is_reference_table = True

    return item_idx, result_idx, is_reference_table

# --- 4. 核心：文字模式 ---

def parse_text_lines(text, data_pool, file_group_data, filename, company, targets=None):
    lines = text.split('\n')
    for line in lines:
        line_clean = clean_text(line)
        if not line_clean: continue
        
        matched_simple = None
        for key, keywords in SIMPLE_KEYWORDS.items():
            if targets and key not in targets: continue
            for kw in keywords:
                if kw in line_clean and "test item" not in line_clean.lower():
                    matched_simple = key
                    break
            if matched_simple: break
        
        matched_group = None
        if not matched_simple:
            for group_key, keywords in GROUP_KEYWORDS.items():
                if targets and group_key not in targets: continue
                for kw in keywords:
                    if kw in line_clean:
                        matched_group = group_key
                        break
                if matched_group: break
        
        if matched_simple or matched_group:
            parts = line_clean.split()
            if len(parts) < 2: continue
            
            found_val = ""
            for part in reversed(parts):
                p_lower = part.lower()
                if p_lower in ["mg/kg", "ppm", "2", "5", "10", "50", "100", "1000", "0.1", "-", "---"]: continue
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
    all_dates = []
    
    # ★ V34 特色: 全局 Pb 追蹤器，確保檔名顯示含有最大 Pb 值的檔案
    global_tracker = {
        "Pb": {"max_score": -1, "max_value": -1.0, "filename": ""}
    }
    
    progress_bar = st.progress(0)
    
    for i, file in enumerate(files):
        filename = file.name
        file_group_data = {key: [] for key in GROUP_KEYWORDS.keys()}
        
        try:
            with pdfplumber.open(file) as pdf:
                file_dates = []
                first_few_pages_text = ""
                full_text_content = "" 
                
                for p_idx in range(len(pdf.pages)):
                    page = pdf.pages[p_idx]
                    page_txt = page.extract_text() or ""
                    full_text_content += page_txt + "\n"
                    
                    if p_idx < 3:
                        first_few_pages_text += page_txt
                        d = extract_date_from_text(page_txt)
                        if d: file_dates.append(d)
                
                if file_dates:
                     all_dates.append((max(file_dates), filename))
                
                company = identify_company(first_few_pages_text)
                
                if check_pfas_in_summary(first_few_pages_text):
                    data_pool["PFAS"].append({"priority": (4, 0, "REPORT"), "filename": filename})

                # 2. 引擎 A: 表格模式
                for page in pdf.pages:
                    tables = page.extract_tables()
                    for table in tables:
                        if not table or len(table) < 2: continue
                        
                        item_idx, result_idx, is_skip_table = identify_columns_by_company(table, company)
                        if is_skip_table: continue 

                        for row_idx, row in enumerate(table):
                            clean_row = [clean_text(cell) for cell in row]
                            row_txt = "".join(clean_row).lower()
                            if "test item" in row_txt or "result" in row_txt or "restricted" in row_txt: continue
                            if not any(clean_row): continue
                            
                            target_item_col = item_idx if item_idx != -1 else 0
                            if target_item_col >= len(clean_row): continue
                            item_name = clean_row[target_item_col]
                            
                            if "pvc" in item_name.lower() or "polyvinyl" in item_name.lower(): continue

                            result = ""
                            if result_idx != -1 and result_idx < len(clean_row):
                                result = clean_row[result_idx]
                            
                            if not result:
                                for cell in reversed(clean_row):
                                    c_lower = cell.lower()
                                    if not cell: continue
                                    if "nd" in c_lower or "n.d." in c_lower or "negative" in c_lower:
                                        result = cell
                                        break
                                    if re.search(r"^\d+(\.\d+)?", cell):
                                        if "1000" in cell: continue 
                                        result = cell
                                        break
                            
                            priority = parse_value_priority(result)
                            if priority[0] == 0: continue 

                            for target_key, keywords in SIMPLE_KEYWORDS.items():
                                for kw in keywords:
                                    if kw.lower() in item_name.lower():
                                        if target_key == "PFOS" and "related" in item_name.lower(): continue 
                                        
                                        data_pool[target_key].append({"priority": priority, "filename": filename})
                                        
                                        # 更新 Pb 全局追蹤器
                                        if target_key == "Pb":
                                            score = priority[0]
                                            val = priority[1]
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
                                    if kw.lower() in item_name.lower():
                                        file_group_data[group_key].append(priority)
                                        break
                
                # 3. 引擎 B: 文字模式 (V34 邏輯: SGS 且缺資料時啟動)
                missing_targets = []
                pb_data = [d for d in data_pool["Pb"] if d['filename'] == filename]
                if not pb_data: missing_targets.append("Pb")
                
                if company == "SGS" and (missing_targets or not file_group_data["PBB"] or not file_group_data["PBDE"]):
                     parse_text_lines(full_text_content, data_pool, file_group_data, filename, company, targets=None)
                     
                     # 文字模式抓完後，再次更新 Pb 全局追蹤
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

            # 檔案結算
            for group_key, values in file_group_data.items():
                if values:
                    best_in_file = sorted(values, key=lambda x: (x[0], x[1]), reverse=True)[0]
                    data_pool[group_key].append({
                        "priority": best_in_file,
                        "filename": filename
                    })

        except Exception as e:
            st.warning(f"檔案 {filename} 解析異常: {e}")
        
        progress_bar.progress((i + 1) / len(files))

    # 聚合
    final_row = {}
    for key in OUTPUT_COLUMNS:
        if key in ["日期", "檔案名稱"]: continue
        candidates = data_pool.get(key, [])
        if not candidates:
            final_row[key] = "" 
            continue
        # 選出最好的結果
        best_record = sorted(candidates, key=lambda x: (x['priority'][0], x['priority'][1]), reverse=True)[0]
        final_row[key] = best_record['priority'][2]

    # 日期
    final_date_str = ""
    if all_dates:
        latest_date_record = sorted(all_dates, key=lambda x: x[0], reverse=True)[0]
        final_date_str = latest_date_record[0].strftime("%Y/%m/%d")
    
    final_row["日期"] = final_date_str
    
    # 檔名使用 Pb 的來源檔名 (V34 特色)
    if global_tracker["Pb"]["filename"]:
        final_row["檔案名稱"] = global_tracker["Pb"]["filename"]
    else:
        final_row["檔案名稱"] = latest_date_record[1] if all_dates else (files[0].name if files else "")

    return [final_row]

# --- 介面 ---
st.set_page_config(page_title="SGS 報告聚合工具 v34.0", layout="wide")
st.title("📄 萬用型檢測報告聚合工具 (v34.0 回歸穩定版)")
st.info("💡 v34.0：回復到最穩定的 SGS 版本，能抓取 ▲ 特殊符號數值，並確保 Pb 最大值對應正確的檔案名稱。")

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
        
        st.download_button("📥 下載 Excel", data=output.getvalue(), file_name="SGS_Summary_v34.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        
    except Exception as e:
        st.error(f"系統錯誤: {e}")
