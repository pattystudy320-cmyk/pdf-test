import streamlit as st
import pdfplumber
import pandas as pd
import re
import math

# --- 設定頁面 ---
st.set_page_config(page_title="通用檢測報告擷取工具 (V12 最終融合版)", layout="wide")
st.title("🧪 通用型第三方檢測報告數據擷取工具 (V12 最終融合版)")
st.markdown("""
**V12 核心策略：分而治之 (Divide and Conquer)**
1.  **⚓ 重金屬 (Pb)**：使用「黃金欄位鎖定」，跟隨 Cd/Hg，確保 Pb(3.53) 準確。
2.  **∑ 有機物 (PBBs)**：使用「全域子項目加總」，掃描表格內所有子項目累加，解決空值問題。
3.  **👁️ 單項掃描 (PFOS/Cl)**：使用「智慧行掃描」，自動避開 Limit/MDL，解決 PFOS 消失與 Cl 誤判。
4.  **📅 日期鎖定**：僅鎖定報告首頁簽發日。
""")

# --- 1. 定義目標欄位 ---
TARGET_FIELDS = {
    "Lead": {"name": "Pb", "keywords": [r"^Lead\b", r"^Pb\b", r"铅", r"Lead \(Pb\)", r"Pb"]},
    "Cadmium": {"name": "Cd", "keywords": [r"^Cadmium\b", r"^Cd\b", r"镉", r"Cadmium \(Cd\)", r"Cd"]},
    "Mercury": {"name": "Hg", "keywords": [r"^Mercury\b", r"^Hg\b", r"汞", r"Mercury \(Hg\)", r"Hg"]},
    "Hexavalent Chromium": {"name": "Cr(VI)", "keywords": [r"Hexavalent Chromium", r"Cr\(VI\)", r"Cr6\+", r"六价铬", r"六價鉻"]},
    "DEHP": {"name": "DEHP", "keywords": [r"Bis\(2-ethylhexyl\) phthalate", r"DEHP", r"邻苯二甲酸二\(2-乙基己基\)酯"]},
    "BBP": {"name": "BBP", "keywords": [r"Butyl benzyl phthalate", r"BBP", r"邻苯二甲酸丁基苄基酯"]},
    "DBP": {"name": "DBP", "keywords": [r"Dibutyl phthalate", r"DBP", r"邻苯二甲酸二丁酯"]},
    "DIBP": {"name": "DIBP", "keywords": [r"Diisobutyl phthalate", r"DIBP", r"邻苯二甲酸二异丁酯"]},
    "Fluorine": {"name": "F", "keywords": [r"Fluorine", r"氟", r"Fluorine \(F\)"]},
    "Chlorine": {"name": "Cl", "keywords": [r"Chlorine", r"氯", r"Chlorine \(Cl\)"]},
    "Bromine": {"name": "Br", "keywords": [r"Bromine", r"溴", r"Bromine \(Br\)"]},
    "Iodine": {"name": "I", "keywords": [r"Iodine", r"碘", r"Iodine \(I\)"]},
    "PFOS": {"name": "PFOS", "keywords": [r"Perfluorooctane Sulfonates", r"PFOS", r"全氟辛磺酸"]},
}

PBBS_KEYWORDS = [r"Monobromobiphenyl", r"Dibromobiphenyl", r"Tribromobiphenyl", r"Tetrabromobiphenyl", 
                 r"Pentabromobiphenyl", r"Hexabromobiphenyl", r"Heptabromobiphenyl", r"Octabromobiphenyl", 
                 r"Nonabromobiphenyl", r"Decabromobiphenyl", r"一溴联苯", r"十溴联苯", r"一溴聯苯"]
PBDES_KEYWORDS = [r"Monobromodiphenyl ether", r"Dibromodiphenyl ether", r"Tribromodiphenyl ether", 
                  r"Tetrabromodiphenyl ether", r"Pentabromodiphenyl ether", r"Hexabromodiphenyl ether", 
                  r"Heptabromodiphenyl ether", r"Octabromodiphenyl ether", r"Nonabromodiphenyl ether", 
                  r"Decabromodiphenyl ether", r"一溴二苯醚", r"十溴二苯醚"]

# --- 2. 輔助函式區 ---

def clean_text(text):
    if not text: return ""
    return re.sub(r'\s+', ' ', str(text)).strip()

def normalize_date(date_str):
    if not date_str: return ""
    clean_date = re.sub(r"Date:|Issue Date:|Report Date:|日期\s*\(?Date\)?[:：]?", "", date_str, flags=re.IGNORECASE).strip()
    try:
        match_num = re.search(r"(\d{4})[-/. ](\d{1,2})[-/. ](\d{1,2})", clean_date)
        if match_num:
            return f"{match_num.group(1)}/{int(match_num.group(2)):02d}/{int(match_num.group(3)):02d}"
        
        months = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6, 
                  "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
        
        match_dd_mon_yy = re.search(r"(\d{1,2})[-/\s]([A-Za-z]{3})[-/\s](\d{2,4})", clean_date, re.IGNORECASE)
        if match_dd_mon_yy:
            d, m_str, y = match_dd_mon_yy.groups()
            m = months.get(m_str.upper(), 0)
            if m > 0:
                if len(y) == 2: y = "20" + y
                return f"{y}/{m:02d}/{int(d):02d}"

        match_mon_dd_yyyy = re.search(r"([A-Za-z]{3})\.?\s+(\d{1,2}),?\s+(\d{4})", clean_date, re.IGNORECASE)
        if match_mon_dd_yyyy:
            m_str, d, y = match_mon_dd_yyyy.groups()
            m = months.get(m_str.upper(), 0)
            if m > 0:
                return f"{y}/{m:02d}/{int(d):02d}"
    except:
        pass
    return ""

def find_date_in_first_page(text):
    lines = text.split('\n')
    for line in lines:
        if "RECEIVED" in line.upper() or "PERIOD" in line.upper() or "STARTED" in line.upper(): continue
        if re.search(r"(Date|Issue Date|Report Date|日期)[:：\s\(]", line, re.IGNORECASE):
            m1 = re.search(r"(\d{4}[-/. ]\d{1,2}[-/. ]\d{1,2})", line)
            if m1: return normalize_date(m1.group(1))
            m2 = re.search(r"([A-Za-z]{3}\.?\s+\d{1,2},?\s+\d{4})", line)
            if m2: return normalize_date(m2.group(1))
            m3 = re.search(r"(\d{1,2}-[A-Za-z]{3}-\d{2,4})", line)
            if m3: return normalize_date(m3.group(1))
    return ""

def extract_value_logic(val_str, strict_numeric=False):
    if not val_str: return None, ""
    
    val_upper = str(val_str).upper().replace(" ", "")
    
    if re.search(r"\b\d{2,7}-\d{2}-\d\b", val_str): return None, ""

    if "N.D." in val_upper or "ND" in val_upper or "<" in val_upper: return 0, "N.D."
    
    if "NEGATIVE" in val_upper or "阴性" in val_upper: 
        if strict_numeric: return None, ""
        return 0.0001, "NEGATIVE"
        
    if "POSITIVE" in val_upper or "阳性" in val_upper: 
        if strict_numeric: return None, ""
        return 999999, "POSITIVE"
    
    val_clean = re.sub(r"(mg/kg|ppm|%|µg/cm²|ug/cm2)", "", val_str, flags=re.IGNORECASE)
    match = re.search(r"(\d+(\.\d+)?)", val_clean)
    
    if match:
        num = float(match.group(1))
        if 2010 <= num <= 2030: return None, ""
        # V11/V12: 智慧掃描輔助，排除 Limit/MDL
        if num in [100, 1000, 2, 5, 8, 10, 25, 50] and "ND" not in val_upper:
            pass 
        return num, match.group(1)
    
    return None, ""

def check_pfas_in_section(full_text):
    start_keywords = ["TEST REQUESTED", "測試需求", "TEST REQUEST"]
    end_keywords = ["TEST METHOD", "TEST RESULTS", "CONCLUSION", "測試結果", "結論"]
    
    upper_text = full_text.upper()
    start_idx = -1
    end_idx = -1
    
    for kw in start_keywords:
        idx = upper_text.find(kw)
        if idx != -1:
            start_idx = idx
            break
    if start_idx == -1: return "" 
    
    for kw in end_keywords:
        idx = upper_text.find(kw, start_idx)
        if idx != -1:
            end_idx = idx
            break
    if end_idx == -1: end_idx = len(upper_text)
    
    target_section = upper_text[start_idx:end_idx]
    if "PFAS" in target_section or "PER- AND POLYFLUOROALKYL" in target_section:
        return "REPORT"
    return ""

def get_column_score(header_cells, table_data=None):
    scores = {} 
    num_cols = len(header_cells)
    exclude_kw = ["ITEM", "METHOD", "UNIT", "MDL", "LOQ", "LIMIT", "REQUIREMENT", "项目", "方法", "单位", "限值", "RL", "CAS", "NO.", "序"]
    result_kw = ["RESULT", "结果", "SAMPLE", "ID", "001", "002", "A1", "DATA", "含量"]
    mdl_kw = ["MDL", "LOQ", "RL", "LIMIT", "限值"]
    
    for i, cell in enumerate(header_cells):
        if not cell: continue
        txt = clean_text(str(cell)).upper()
        score = 0
        if any(ex in txt for ex in exclude_kw): score -= 100
        if any(res in txt for res in result_kw): score += 50
        if "CAS" in txt: score -= 200 
        if i + 1 < num_cols:
            right_txt = clean_text(str(header_cells[i+1])).upper()
            if any(k in right_txt for k in mdl_kw): score += 30
        if i - 1 >= 0:
            left_txt = clean_text(str(header_cells[i-1])).upper()
            if "ITEM" in left_txt or "项目" in left_txt: score += 20
        scores[i] = score

    if table_data and len(table_data) > 3:
        for i in range(num_cols):
            if i not in scores: continue
            sample_vals = []
            for row in table_data[1:5]:
                if i < len(row): sample_vals.append(clean_text(str(row[i])).upper())
            is_numeric_or_nd = 0
            is_cas = 0
            is_method = 0
            is_float = 0
            for val in sample_vals:
                if "N.D." in val or "NEGATIVE" in val or re.search(r"^\d+(\.\d+)?$", val): is_numeric_or_nd += 1
                if re.search(r"^\d+\.\d+$", val): is_float += 1
                if re.search(r"\d{2,7}-\d{2}-\d", val): is_cas += 1
                if "IEC" in val or "EPA" in val: is_method += 1
            if is_cas > 0: scores[i] -= 200
            if is_method > 0: scores[i] -= 100
            if is_numeric_or_nd > 0: scores[i] += 20
            if is_float > 0: scores[i] += 100 

    if not scores: return -1
    best_col = max(scores, key=scores.get)
    if scores[best_col] < -50: return -1
    return best_col

def find_golden_column(table, result_col_idx):
    if result_col_idx == -1: return False
    score = 0
    for row in table:
        if len(row) <= result_col_idx: continue
        row_text = " ".join([str(c).upper() for c in row if c])
        val_text = clean_text(row[result_col_idx])
        val_num, val_disp = extract_value_logic(val_text)
        if val_num is not None:
            if ("CADMIUM" in row_text or "镉" in row_text) and (val_disp == "N.D." or val_num > 0): score += 1
            if ("MERCURY" in row_text or "汞" in row_text) and (val_disp == "N.D." or val_num > 0): score += 1
    return score >= 1

def perform_smart_scan(row, strict_numeric=False):
    """
    V12 智慧行掃描：掃描整行，排除 Limit/MDL，找出最佳結果值
    """
    potential_vals = []
    for cell in row:
        txt = clean_text(str(cell))
        val_num, val_disp = extract_value_logic(txt, strict_numeric=strict_numeric)
        if val_num is not None:
            # 排除常見 Limit/MDL 數值 (除非它是 N.D.)
            if val_num in [2, 5, 8, 10, 25, 50, 100, 1000] and val_disp != "N.D.":
                continue
            potential_vals.append((val_num, val_disp))
    
    if potential_vals:
        # 取最後一個 (通常結果排在 Limit 左邊，或中間)
        # 如果有浮點數 (如 3.53)，優先級最高 (這裡簡化，取最後一個有效值通常正確)
        return potential_vals[-1]
    return None

def process_file(uploaded_file):
    filename = uploaded_file.name
    results = {k: {"val": None, "display": ""} for k in TARGET_FIELDS.keys()}
    results["PBBs"] = {"val": None, "display": "", "sum_val": 0}
    results["PBDEs"] = {"val": None, "display": "", "sum_val": 0}
    results["PFAS"] = ""
    results["Date"] = ""
    
    is_scanned = True
    full_text_content = ""
    
    with pdfplumber.open(uploaded_file) as pdf:
        # A. 全文掃描
        for i, page in enumerate(pdf.pages):
            text = page.extract_text()
            if text and len(text) > 50:
                is_scanned = False
                full_text_content += text + "\n"
                if i == 0: results["Date"] = find_date_in_first_page(text)

        if is_scanned: return None, filename
        results["PFAS"] = check_pfas_in_section(full_text_content)

        # B. 表格數據提取 (V12 分流策略)
        for page in pdf.pages:
            tables = page.extract_tables()
            if tables:
                for table in tables:
                    if not table or len(table) < 2: continue
                    
                    header_row_idx = -1
                    result_col_idx = -1
                    
                    # 1. 嘗試尋找表頭 (為了重金屬黃金欄位)
                    for r_idx, row in enumerate(table[:6]):
                        row_str = " ".join([str(c).upper() for c in row if c])
                        if ("ITEM" in row_str or "项目" in row_str) and ("UNIT" in row_str or "MDL" in row_str or "RESULT" in row_str or "结果" in row_str):
                            header_row_idx = r_idx
                            result_col_idx = get_column_score(row, table)
                            if result_col_idx == -1 and r_idx + 1 < len(table):
                                result_col_idx = get_column_score(table[r_idx+1], table)
                            break
                    
                    start_row = header_row_idx + 1 if header_row_idx != -1 else 0
                    is_golden_table = find_golden_column(table, result_col_idx) if result_col_idx != -1 else False

                    # 2. 遍歷所有行 (執行分流邏輯)
                    for r_idx in range(start_row, len(table)):
                        row = table[r_idx]
                        if not row: continue
                        
                        item_name = clean_text(row[0])
                        if len(row) > 1: item_name += " " + clean_text(row[1])
                        item_upper = item_name.upper()

                        # --- 策略 A: 有機物 (PBBs/PBDEs) 全域子項目加總 ---
                        # 不管這是哪個表，只要看到子項目就加總 (回歸 V10 暴力法)
                        for pbb_kw in PBBS_KEYWORDS:
                            if re.search(pbb_kw, item_upper, re.IGNORECASE):
                                res = perform_smart_scan(row) # 用智慧掃描抓取該行數值
                                if res:
                                    val_num, _ = res
                                    if val_num > 0:
                                        results["PBBs"]["sum_val"] += val_num
                                        results["PBBs"]["val"] = 1
                        
                        for pbde_kw in PBDES_KEYWORDS:
                            if re.search(pbde_kw, item_upper, re.IGNORECASE):
                                res = perform_smart_scan(row)
                                if res:
                                    val_num, _ = res
                                    if val_num > 0:
                                        results["PBDEs"]["sum_val"] += val_num
                                        results["PBDEs"]["val"] = 1

                        # --- 策略 B: 重金屬 (Pb/Cd/Hg/Cr6) 黃金欄位鎖定 ---
                        # 只有在確認是 Golden Table 時才使用鎖定，否則使用智慧掃描
                        is_heavy_metal = any(k in item_upper for k in ["LEAD", "CADMIUM", "MERCURY", "HEXAVALENT", "PB", "CD", "HG", "CR(VI)", "铅", "镉", "汞", "六价铬"])
                        
                        if is_heavy_metal and is_golden_table and result_col_idx != -1 and len(row) > result_col_idx:
                             val_text = clean_text(row[result_col_idx])
                             update_results(results, item_name, val_text, is_golden_col=True)
                             continue # 重金屬處理完畢

                        # --- 策略 C: 其他單項 (PFOS, Cl, F, etc.) 智慧行掃描 ---
                        # 包含非 Golden Table 的重金屬，以及 PFOS, Cl
                        # 掃描目標關鍵字
                        for field, config in TARGET_FIELDS.items():
                            for kw in config["keywords"]:
                                if re.search(kw, item_upper, re.IGNORECASE):
                                    # 語義防火牆 (Cl)
                                    if field == "Chlorine" and ("POLYVINYL" in item_upper or "PVC" in item_upper): continue
                                    
                                    # 重金屬如果在 Golden Table 已經被上面處理過了，這裡處理剩下的
                                    if results[field]["val"] is not None and field in ["Lead", "Cadmium", "Mercury", "Hexavalent Chromium"]: continue

                                    is_strict = (field in ["Chlorine", "Bromine"]) # Cl/Br 不接受 Negative
                                    res = perform_smart_scan(row, strict_numeric=is_strict)
                                    
                                    if res:
                                        val_num, val_disp = res
                                        # 寫入結果
                                        update_results_direct(results, field, val_num, val_disp)

            # C. 文字流模式 (Fallback, 僅針對尚未抓到的項目)
            words = page.extract_words(keep_blank_chars=True)
            target_x_center = -1
            for w in words:
                txt = w['text'].upper()
                if txt in ["RESULT", "结果", "SAMPLE", "001", "A1"] and "ITEM" not in txt: 
                    target_x_center = (w['x0'] + w['x1']) / 2
                    break
            
            if target_x_center != -1:
                rows = {}
                for w in words:
                    y = round(w['top'] / 5) * 5
                    if y not in rows: rows[y] = []
                    rows[y].append(w)
                
                for y, row_words in rows.items():
                    line_text = " ".join([w['text'] for w in row_words])
                    # 簡單補漏 PBBs (文字流模式)
                    for pbb_kw in PBBS_KEYWORDS + PBDES_KEYWORDS:
                        if re.search(pbb_kw, line_text, re.IGNORECASE):
                             for w in row_words:
                                w_center = (w['x0'] + w['x1']) / 2
                                if abs(w_center - target_x_center) < 150:
                                    val, disp = extract_value_logic(w['text'])
                                    if val is not None and val > 0 and val not in [1000, 5, 25]:
                                        cat = "PBBs" if any(k in pbb_kw for k in PBBS_KEYWORDS) else "PBDEs"
                                        results[cat]["sum_val"] += val
                                        results[cat]["val"] = 1 
                                        break

    finalize_results(results)
    
    # 填充
    for k, v in results.items():
        if isinstance(v, dict) and "val" in v and v["val"] is None:
            v["display"] = "" # 保持空白
            v["val"] = 0

    final_output = {
        "File Name": filename,
        "Pb": results["Lead"]["display"],
        "Cd": results["Cadmium"]["display"],
        "Hg": results["Mercury"]["display"],
        "Cr(VI)": results["Hexavalent Chromium"]["display"],
        "PBBs": results["PBBs"]["display"],
        "PBDEs": results["PBDEs"]["display"],
        "DEHP": results["DEHP"]["display"],
        "BBP": results["BBP"]["display"],
        "DBP": results["DBP"]["display"],
        "DIBP": results["DIBP"]["display"],
        "F": results["Fluorine"]["display"],
        "Cl": results["Chlorine"]["display"],
        "Br": results["Bromine"]["display"],
        "I": results["Iodine"]["display"],
        "PFOS": results["PFOS"]["display"],
        "PFAS": results["PFAS"],
        "Date": results["Date"],
        "_sort_pb": results["Lead"]["val"],
        "_sort_max": max([v["val"] for k, v in results.items() if isinstance(v, dict) and v["val"] is not None])
    }
    
    return final_output, None

def update_results(results, item_name, val_text, is_golden_col=False):
    """ V12 通用更新邏輯 (主要用於表單模式) """
    item_upper = str(item_name).upper()
    if "CHLORINE" in item_upper and ("POLYVINYL" in item_upper or "PVC" in item_upper): return
    
    is_halogen = any(x in item_upper for x in ["CHLORINE", "BROMINE", "FLUORINE", "IODINE"])
    val_num, val_disp = extract_value_logic(val_text, strict_numeric=is_halogen)
    if val_num is None: return

    for field_key, config in TARGET_FIELDS.items():
        for kw in config["keywords"]:
            if re.search(kw, item_upper, re.IGNORECASE):
                if is_golden_col and field_key in ["Lead", "Cadmium", "Mercury", "Hexavalent Chromium"]:
                    results[field_key]["val"] = val_num
                    results[field_key]["display"] = val_disp
                    return
                
                # 比大小更新
                current_val = results[field_key]["val"]
                if current_val is None or val_num > current_val:
                    results[field_key]["val"] = val_num
                    results[field_key]["display"] = val_disp
                elif val_num == 0 and (current_val == 0 or current_val is None):
                    if val_disp == "NEGATIVE": results[field_key]["display"] = "NEGATIVE"
                    elif not results[field_key]["display"]: results[field_key]["display"] = "N.D."
                    results[field_key]["val"] = 0
                return

def update_results_direct(results, field_key, val_num, val_disp):
    """ 直接更新指定欄位 (用於智慧掃描) """
    current_val = results[field_key]["val"]
    if current_val is None or val_num > current_val:
        results[field_key]["val"] = val_num
        results[field_key]["display"] = val_disp
    elif val_num == 0 and (current_val == 0 or current_val is None):
        if val_disp == "NEGATIVE": results[field_key]["display"] = "NEGATIVE"
        elif not results[field_key]["display"]: results[field_key]["display"] = "N.D."
        results[field_key]["val"] = 0

def finalize_results(results):
    if results["PBBs"]["sum_val"] > 0:
        results["PBBs"]["display"] = str(round(results["PBBs"]["sum_val"], 2))
    elif results["PBBs"]["val"] is None:
        results["PBBs"]["display"] = ""
    else:
        results["PBBs"]["display"] = "N.D."

    if results["PBDEs"]["sum_val"] > 0:
        results["PBDEs"]["display"] = str(round(results["PBDEs"]["sum_val"], 2))
    elif results["PBDEs"]["val"] is None:
        results["PBDEs"]["display"] = ""
    else:
        results["PBDEs"]["display"] = "N.D."

# --- 主介面 ---
uploaded_files = st.file_uploader("請上傳 PDF 檢測報告 (支援 SGS, CTI, Intertek 等)", type="pdf", accept_multiple_files=True)

if uploaded_files:
    all_data = []
    scanned_files = []

    with st.spinner('正在進行 V12 引擎分析 (分流 + 智慧行掃描 + 全域加總)...'):
        for pdf_file in uploaded_files:
            data, scanned_name = process_file(pdf_file)
            if scanned_name:
                scanned_files.append(scanned_name)
            else:
                all_data.append(data)

    if all_data:
        df = pd.DataFrame(all_data)
        if "_sort_pb" in df.columns:
            df = df.sort_values(by=["_sort_pb", "_sort_max"], ascending=[False, False])
            display_df = df.drop(columns=["_sort_pb", "_sort_max"])
        else:
            display_df = df
        
        st.success(f"✅ 成功擷取 {len(all_data)} 份報告！(V12 核心)")
        st.dataframe(display_df, use_container_width=True)
        
        csv = display_df.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            label="📥 下載 Excel/CSV 報表",
            data=csv,
            file_name="rohs_report_v12_final.csv",
            mime="text/csv",
        )

    if scanned_files:
        st.error("⚠️ 以下檔案為掃描圖片 (無法擷取文字)：")
        for f in scanned_files:
            st.write(f"- {f}")
else:
    st.info("請上傳 PDF 檔案以開始分析。")
