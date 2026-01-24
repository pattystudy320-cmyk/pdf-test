import streamlit as st
import pdfplumber
import pandas as pd
import re
from datetime import datetime

# --- 設定頁面 ---
st.set_page_config(page_title="通用檢測報告擷取工具 (V14 最終全能版)", layout="wide")
st.title("🧪 通用型第三方檢測報告數據擷取工具 (V14 最終全能版)")
st.markdown("""
**V14 版本更新摘要：**
1.  **📅 日期鎖定 V2**：引入「黑名單過濾」與「最晚日期法則」，精準鎖定發行日，排除接收/測試日。
2.  **∑ 有機物優化**：PBBs/PBDEs 採用「全行掃描+智慧過濾」，不依賴欄位，解決 Intertek 空值問題。
3.  **🎯 核心保留**：Pb 黃金欄位、Cl PVC 防火牆、PFOS 單項直取。
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

def parse_date_obj(date_str):
    """將字串解析為 datetime 物件，用於比較日期先後"""
    clean = re.sub(r"Date:|Issue Date:|Report Date:|日期\s*\(?Date\)?[:：]?", "", date_str, flags=re.IGNORECASE).strip()
    clean = clean.replace("/", "-").replace(".", "-").replace(" ", "-")
    
    # 嘗試常見格式
    formats = [
        "%Y-%m-%d", "%d-%b-%Y", "%d-%B-%Y", "%b-%d-%Y", "%B-%d-%Y",
        "%d-%b-%y", "%d-%B-%y"
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(clean, fmt)
        except:
            continue
            
    # 嘗試 Regex 提取
    try:
        # 2025-06-16
        m = re.search(r"(\d{4})[-/. ](\d{1,2})[-/. ](\d{1,2})", date_str)
        if m: return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        
        # 16-Jun-2025
        m2 = re.search(r"(\d{1,2})[-/\s]([A-Za-z]{3})[-/\s](\d{4})", date_str, re.IGNORECASE)
        if m2: return datetime.strptime(f"{m2.group(1)}-{m2.group(2)}-{m2.group(3)}", "%d-%b-%Y")
        
        # Jun 16, 2025
        m3 = re.search(r"([A-Za-z]{3})\.?\s+(\d{1,2}),?\s+(\d{4})", date_str, re.IGNORECASE)
        if m3: return datetime.strptime(f"{m3.group(2)}-{m3.group(1)}-{m3.group(3)}", "%d-%b-%Y")
    except:
        pass
    return None

def normalize_date_str(dt_obj):
    """將 datetime 物件轉為 YYYY/MM/DD 字串"""
    if dt_obj:
        return dt_obj.strftime("%Y/%m/%d")
    return ""

def find_date_in_first_page(text):
    """
    V14 日期抓取邏輯：
    1. 黑名單過濾 (Received, Period, Started...)
    2. 收集所有候選日期
    3. 取「最晚」的一個日期 (Issue Date 通常是最後發生的)
    """
    lines = text.split('\n')
    candidates = []
    
    blacklist = ["RECEIVED", "PERIOD", "STARTED", "SUBMITTED", "COMPLETED", "收件", "週期", "期间"]
    
    for line in lines:
        upper_line = line.upper()
        # 1. 黑名單過濾：如果該行包含黑名單關鍵字，直接跳過
        if any(bad in upper_line for bad in blacklist):
            continue
            
        # 2. 尋找日期格式 (YYYY/MM/DD, DD-Mon-YYYY)
        # 格式 A: 2025.06.16 或 2025/06/16
        if re.search(r"\d{4}[-/. ]\d{1,2}[-/. ]\d{1,2}", line):
            candidates.append(line)
        # 格式 B: 16-Jun-2025 或 Jun 16, 2025
        elif re.search(r"[A-Za-z]{3}", line) and re.search(r"\d{4}", line):
            candidates.append(line)
            
    if not candidates:
        return ""
        
    # 3. 解析候選日期並找出最晚的一天
    valid_dates = []
    for c in candidates:
        dt = parse_date_obj(c)
        if dt:
            # 簡單過濾：年份必須合理 (例如 2010~2030)
            if 2010 <= dt.year <= 2030:
                valid_dates.append(dt)
    
    if valid_dates:
        latest_date = max(valid_dates) # 取最晚日期
        return normalize_date_str(latest_date)
        
    return ""

def extract_value_logic(val_str, strict_numeric=False):
    """
    數值提取邏輯
    strict_numeric: 用於 Cl/PFOS，拒絕 Negative
    """
    if not val_str: return None, ""
    
    val_upper = str(val_str).upper().replace(" ", "")
    
    # CAS 防火牆
    if re.search(r"\b\d{2,7}-\d{2}-\d\b", val_str): return None, ""

    if "N.D." in val_upper or "ND" in val_upper or "<" in val_upper: return 0, "N.D."
    
    if "NEGATIVE" in val_upper or "阴性" in val_upper: 
        if strict_numeric: return None, "" # Cl/PFOS 不接受 Negative
        return 0.0001, "NEGATIVE"
        
    if "POSITIVE" in val_upper or "阳性" in val_upper: 
        if strict_numeric: return None, ""
        return 999999, "POSITIVE"
    
    val_clean = re.sub(r"(mg/kg|ppm|%|µg/cm²|ug/cm2)", "", val_str, flags=re.IGNORECASE)
    match = re.search(r"(\d+(\.\d+)?)", val_clean)
    
    if match:
        num = float(match.group(1))
        # 年份過濾
        if 2010 <= num <= 2030: return None, ""
        return num, match.group(1)
    
    return None, ""

def check_pfas_in_section(full_text):
    """PFAS 區塊限定"""
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
    """找出最像 Result 的欄位索引"""
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
            for row in table_data[1:6]:
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
    """利用 Cd/Hg 鎖定 Result 欄位"""
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

        # B. 表格數據提取 (V14 分流與優化)
        for page in pdf.pages:
            tables = page.extract_tables()
            if tables:
                for table in tables:
                    if not table or len(table) < 2: continue
                    
                    header_row_idx = -1
                    result_col_idx = -1
                    
                    # 1. 定位 Result 欄位 (為了重金屬和單項)
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

                    # 2. 遍歷表格行
                    for r_idx in range(start_row, len(table)):
                        row = table[r_idx]
                        if not row: continue
                        
                        item_name = clean_text(row[0])
                        if len(row) > 1: item_name += " " + clean_text(row[1])
                        item_upper = item_name.upper()

                        # =======================================================
                        # [V14] 策略 A: PBBs/PBDEs 全域加總 + 智慧過濾
                        # =======================================================
                        def process_organic_sum(keywords_list, category_key):
                            if any(re.search(kw, item_upper, re.IGNORECASE) for kw in keywords_list):
                                # 掃描整行，尋找合適數值
                                potential_vals = []
                                for cell in row:
                                    v_num, v_disp = extract_value_logic(clean_text(str(cell)))
                                    if v_num is not None:
                                        # [關鍵] 排除 Limit (1000) 和 MDL (5, 10) 干擾
                                        if v_num in [5, 10, 50, 100, 1000] and v_disp != "N.D.": 
                                            continue 
                                        potential_vals.append(v_num)
                                
                                if potential_vals:
                                    val = potential_vals[-1] # 取最後一個有效值
                                    if val > 0:
                                        results[category_key]["sum_val"] += val
                                        results[category_key]["val"] = 1

                        process_organic_sum(PBBS_KEYWORDS, "PBBs")
                        process_organic_sum(PBDES_KEYWORDS, "PBDEs")

                        # =======================================================
                        # [V14] 策略 B: 單項數值 (Pb, Cl, PFOS) - 依賴欄位定位
                        # =======================================================
                        if result_col_idx != -1 and len(row) > result_col_idx:
                            val_text = clean_text(row[result_col_idx])
                            
                            # 語義防火牆 (Cl)
                            if "CHLORINE" in item_upper and ("POLYVINYL" in item_upper or "PVC" in item_upper):
                                continue

                            # 嚴格型別 (Cl, Br, PFOS)
                            is_strict = any(x in item_upper for x in ["CHLORINE", "BROMINE", "PFOS", "FLUORINE", "IODINE"])
                            
                            val_num, val_disp = extract_value_logic(val_text, strict_numeric=is_strict)
                            
                            if val_num is not None:
                                update_results(results, item_name, val_num, val_disp, is_golden_col=is_golden_table)

            # C. 文字流模式 (Fallback)
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
                    
                    # 補漏 PBBs (文字流模式)
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

def update_results(results, item_name, val_num, val_disp, is_golden_col=False):
    item_upper = str(item_name).upper()
    
    for field_key, config in TARGET_FIELDS.items():
        for kw in config["keywords"]:
            if re.search(kw, item_upper, re.IGNORECASE):
                # 黃金欄位強制更新 (只針對重金屬)
                if is_golden_col and field_key in ["Lead", "Cadmium", "Mercury", "Hexavalent Chromium"]:
                    results[field_key]["val"] = val_num
                    results[field_key]["display"] = val_disp
                    return

                # 一般更新 (比大小)
                current_val = results[field_key]["val"]
                if current_val is None or val_num > current_val:
                    results[field_key]["val"] = val_num
                    results[field_key]["display"] = val_disp
                elif val_num == 0 and (current_val == 0 or current_val is None):
                    if val_disp == "NEGATIVE": results[field_key]["display"] = "NEGATIVE"
                    elif not results[field_key]["display"]: results[field_key]["display"] = "N.D."
                    results[field_key]["val"] = 0
                return

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

    with st.spinner('正在進行 V14 引擎分析 (全能日期鎖定 + 有機物全域掃描)...'):
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
        
        st.success(f"✅ 成功擷取 {len(all_data)} 份報告！(V14 核心)")
        st.dataframe(display_df, use_container_width=True)
        
        csv = display_df.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            label="📥 下載 Excel/CSV 報表",
            data=csv,
            file_name="rohs_report_v14_final.csv",
            mime="text/csv",
        )

    if scanned_files:
        st.error("⚠️ 以下檔案為掃描圖片 (無法擷取文字)：")
        for f in scanned_files:
            st.write(f"- {f}")
else:
    st.info("請上傳 PDF 檔案以開始分析。")
