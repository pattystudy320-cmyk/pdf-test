import streamlit as st
import pdfplumber
import pandas as pd
import re
import math

# --- 設定頁面 ---
st.set_page_config(page_title="通用檢測報告擷取工具 (V8 最終完美版)", layout="wide")
st.title("🧪 通用型第三方檢測報告數據擷取工具 (V8 最終完美版)")
st.markdown("""
**V8 核心升級：**
1.  **⚓ 黃金欄位鎖定**：利用 Cd/Hg 穩定性鎖定 Pb 欄位，解決 SGS Pb(3.53) 誤判問題。
2.  **🛡️ 語義防火牆**：搜尋 Chlorine 時強制排除 PVC，解決 CTI Cl 誤判為 Negative。
3.  **📑 PFAS 區塊限定**：僅在「測試需求」段落搜尋 PFAS，避免誤判。
4.  **📅 日期解析增強**：支援 `日期(Date):` 等混合格式，且僅抓取首頁簽發日。
5.  **⭕ 空值保留**：未檢測項目維持空白，不預設 N.D.。
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

# --- 2. 輔助函式 ---

def clean_text(text):
    if not text: return ""
    return re.sub(r'\s+', ' ', str(text)).strip()

def normalize_date(date_str):
    """日期格式標準化"""
    if not date_str: return ""
    # 移除前綴與無關字元，支援 "日期(Date):" 這種格式
    clean_date = re.sub(r"Date:|Issue Date:|Report Date:|日期\s*\(?Date\)?[:：]?", "", date_str, flags=re.IGNORECASE).strip()
    try:
        # 1. 數字格式 (2025.06.16, 2025/06/16)
        match_num = re.search(r"(\d{4})[-/. ](\d{1,2})[-/. ](\d{1,2})", clean_date)
        if match_num:
            return f"{match_num.group(1)}/{int(match_num.group(2)):02d}/{int(match_num.group(3)):02d}"
        
        # 英文月份處理
        months = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6, 
                  "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
        
        # 2. 16-Jun-25
        match_dd_mon_yy = re.search(r"(\d{1,2})[-/\s]([A-Za-z]{3})[-/\s](\d{2,4})", clean_date, re.IGNORECASE)
        if match_dd_mon_yy:
            d, m_str, y = match_dd_mon_yy.groups()
            m = months.get(m_str.upper(), 0)
            if m > 0:
                if len(y) == 2: y = "20" + y
                return f"{y}/{m:02d}/{int(d):02d}"

        # 3. Jan 08, 2025
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
    """只在第一頁抓取日期"""
    lines = text.split('\n')
    for line in lines:
        if "RECEIVED" in line.upper() or "PERIOD" in line.upper() or "STARTED" in line.upper(): continue
        
        # 擴充 Regex 支援 "日期(Date)"
        if re.search(r"(Date|Issue Date|Report Date|日期)[:：\s\(]", line, re.IGNORECASE):
            # 優先匹配完整日期格式
            m1 = re.search(r"(\d{4}[-/. ]\d{1,2}[-/. ]\d{1,2})", line)
            if m1: return normalize_date(m1.group(1))
            
            m2 = re.search(r"([A-Za-z]{3}\.?\s+\d{1,2},?\s+\d{4})", line)
            if m2: return normalize_date(m2.group(1))
            
            m3 = re.search(r"(\d{1,2}-[A-Za-z]{3}-\d{2,4})", line)
            if m3: return normalize_date(m3.group(1))
    return ""

def extract_value_logic(val_str):
    """
    V8 數值提取：CAS 過濾、年份過濾、內容優先級
    """
    if not val_str: return None, "" # 改為回傳 None 表示沒抓到
    
    val_upper = str(val_str).upper().replace(" ", "")
    
    # 1. CAS 防火牆
    if re.search(r"\b\d{2,7}-\d{2}-\d\b", val_str): return None, ""

    # 2. 文字狀態
    if "N.D." in val_upper or "ND" in val_upper or "<" in val_upper: return 0, "N.D."
    if "NEGATIVE" in val_upper or "阴性" in val_upper: return 0.0001, "NEGATIVE"
    if "POSITIVE" in val_upper or "阳性" in val_upper: return 999999, "POSITIVE"
    
    # 3. 數字提取
    val_clean = re.sub(r"(mg/kg|ppm|%|µg/cm²|ug/cm2)", "", val_str, flags=re.IGNORECASE)
    match = re.search(r"(\d+(\.\d+)?)", val_clean)
    
    if match:
        num = float(match.group(1))
        # 年份過濾
        if 2010 <= num <= 2030: return None, ""
        return num, match.group(1)
    
    return None, ""

# --- 3. 核心處理邏輯 ---

def check_pfas_in_section(full_text):
    """
    PFAS 區塊限定：只在 Test Requested 到 Conclusion 之間搜尋
    """
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
            
    if start_idx == -1: return "" # 找不到開始點，保守起見不回傳 REPORT
    
    # 從開始點之後找結束點
    for kw in end_keywords:
        idx = upper_text.find(kw, start_idx)
        if idx != -1:
            end_idx = idx
            break
            
    # 如果找不到結束點，就搜到最後
    if end_idx == -1: end_idx = len(upper_text)
    
    target_section = upper_text[start_idx:end_idx]
    
    if "PFAS" in target_section or "PER- AND POLYFLUOROALKYL" in target_section:
        return "REPORT"
        
    return ""

def get_column_score(header_cells, table_data=None):
    """權重評分：找出最像 Result 的欄位索引"""
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
                if "N.D." in val or "NEGATIVE" in val or re.search(r"^\d+(\.\d+)?$", val):
                    is_numeric_or_nd += 1
                # V8 新增：如果是浮點數 (如 3.53)，大大加分 (因為 MDL/Limit 通常是整數)
                if re.search(r"^\d+\.\d+$", val):
                    is_float += 1
                if re.search(r"\d{2,7}-\d{2}-\d", val): is_cas += 1
                if "IEC" in val or "EPA" in val: is_method += 1
            
            if is_cas > 0: scores[i] -= 200
            if is_method > 0: scores[i] -= 100
            if is_numeric_or_nd > 0: scores[i] += 20
            if is_float > 0: scores[i] += 100 # 強力加分項

    if not scores: return -1
    best_col = max(scores, key=scores.get)
    if scores[best_col] < -50: return -1
    return best_col

def find_golden_column(table, result_col_idx):
    """
    V8 黃金欄位鎖定：
    掃描表格，看 Cd, Hg 在 result_col_idx 是否有值。
    如果是，回傳 True，表示該欄位非常可靠。
    """
    if result_col_idx == -1: return False
    
    score = 0
    for row in table:
        if len(row) <= result_col_idx: continue
        row_text = " ".join([str(c).upper() for c in row if c])
        val_text = clean_text(row[result_col_idx])
        val_num, val_disp = extract_value_logic(val_text)
        
        # 檢查錨點關鍵字
        if ("CADMIUM" in row_text or "镉" in row_text) and (val_disp == "N.D." or val_num > 0):
            score += 1
        if ("MERCURY" in row_text or "汞" in row_text) and (val_disp == "N.D." or val_num > 0):
            score += 1
            
    return score >= 1 # 只要抓到其中一個錨點，就鎖定

def process_file(uploaded_file):
    filename = uploaded_file.name
    # 初始值為 None，表示未抓取 (區分 "未抓到" 和 "N.D.")
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

        # PFAS 區塊限定判斷
        results["PFAS"] = check_pfas_in_section(full_text_content)

        # B. 表格數據提取
        for page in pdf.pages:
            tables = page.extract_tables()
            
            # 模式 1: 結構化表格
            if tables:
                for table in tables:
                    if not table or len(table) < 2: continue
                    
                    header_row_idx = -1
                    result_col_idx = -1
                    
                    for r_idx, row in enumerate(table[:6]):
                        row_str = " ".join([str(c).upper() for c in row if c])
                        if ("ITEM" in row_str or "项目" in row_str) and ("UNIT" in row_str or "MDL" in row_str or "RESULT" in row_str or "结果" in row_str):
                            header_row_idx = r_idx
                            result_col_idx = get_column_score(row, table)
                            if result_col_idx == -1 and r_idx + 1 < len(table):
                                result_col_idx = get_column_score(table[r_idx+1], table)
                            break
                    
                    if result_col_idx != -1:
                        # 啟動黃金欄位檢查
                        is_golden = find_golden_column(table, result_col_idx)
                        
                        for r_idx in range(header_row_idx + 1, len(table)):
                            row = table[r_idx]
                            if len(row) <= result_col_idx: continue
                            
                            item_name = clean_text(row[0])
                            if len(row) > 1: item_name += " " + clean_text(row[1])
                            
                            val_text = clean_text(row[result_col_idx])
                            # 如果是黃金欄位，強制更新 (force_update=True)
                            update_results(results, item_name, val_text, force_update=is_golden)

            # 模式 2: 文字流 (Fallback)
            # 只有當該頁表格提取不理想時才依賴，或者針對沒抓到的項目補強
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
                    
                    for field, config in TARGET_FIELDS.items():
                        for kw in config["keywords"]:
                            if re.search(kw, line_text, re.IGNORECASE):
                                # 語義防火牆: 防止 Chlorine 抓到 Polyvinyl Chloride
                                if field == "Chlorine" and ("POLYVINYL" in line_text.upper() or "PVC" in line_text.upper()):
                                    continue
                                    
                                # 如果已經有值(來自表格)，且不是 None，就不覆蓋
                                if results[field]["val"] is not None: continue

                                for w in row_words:
                                    w_center = (w['x0'] + w['x1']) / 2
                                    if abs(w_center - target_x_center) < 150: 
                                        val, disp = extract_value_logic(w['text'])
                                        if val is not None:
                                            update_results(results, field, disp)
                                            break

                    # PBBs/PBDEs 加總
                    for pbb_kw in PBBS_KEYWORDS + PBDES_KEYWORDS:
                        if re.search(pbb_kw, line_text, re.IGNORECASE):
                             for w in row_words:
                                w_center = (w['x0'] + w['x1']) / 2
                                if abs(w_center - target_x_center) < 150:
                                    val, disp = extract_value_logic(w['text'])
                                    if val is not None and val > 0 and val != 1000:
                                        cat = "PBBs" if any(k in pbb_kw for k in PBBS_KEYWORDS) else "PBDEs"
                                        results[cat]["sum_val"] += val
                                        # 標記有抓到值
                                        results[cat]["val"] = 1 
                                        break

    finalize_results(results)
    
    # 填充：未抓到的保持空白 (不填 N.D.)
    for k, v in results.items():
        if isinstance(v, dict) and "val" in v and v["val"] is None:
            v["display"] = "" # 保持空白
            v["val"] = 0 # 排序用預設值

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
        "_sort_max": max([v["val"] for k, v in results.items() if isinstance(v, dict) and "val" is not None])
    }
    
    return final_output, None

def update_results(results, item_name, val_text, force_update=False):
    item_upper = str(item_name).upper()
    
    # 語義防火牆
    if "CHLORINE" in item_upper and ("POLYVINYL" in item_upper or "PVC" in item_upper): return

    val_num, val_disp = extract_value_logic(val_text)
    if val_num is None: return # 無效數值

    for field_key, config in TARGET_FIELDS.items():
        for kw in config["keywords"]:
            if re.search(kw, item_upper, re.IGNORECASE):
                # 黃金欄位強制更新
                if force_update:
                    results[field_key]["val"] = val_num
                    results[field_key]["display"] = val_disp
                    return

                # 正常比較邏輯
                current_val = results[field_key]["val"]
                if current_val is None or val_num > current_val:
                    results[field_key]["val"] = val_num
                    results[field_key]["display"] = val_disp
                elif val_num == 0 and (current_val == 0 or current_val is None):
                    # N.D. vs Negative
                    if val_disp == "NEGATIVE": results[field_key]["display"] = "NEGATIVE"
                    elif not results[field_key]["display"]: results[field_key]["display"] = "N.D."
                    results[field_key]["val"] = 0
                return

    for pbb_kw in PBBS_KEYWORDS:
        if re.search(pbb_kw, item_upper, re.IGNORECASE):
            if val_num > 0:
                results["PBBs"]["sum_val"] += val_num
                results["PBBs"]["val"] = 1 # 標記有值
            return

    for pbde_kw in PBDES_KEYWORDS:
        if re.search(pbde_kw, item_upper, re.IGNORECASE):
            if val_num > 0:
                results["PBDEs"]["sum_val"] += val_num
                results["PBDEs"]["val"] = 1
            return

def finalize_results(results):
    if results["PBBs"]["sum_val"] > 0:
        results["PBBs"]["display"] = str(round(results["PBBs"]["sum_val"], 2))
    elif results["PBBs"]["val"] is None: # 如果完全沒抓到子項目
        results["PBBs"]["display"] = "" # 保持空白
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

    with st.spinner('正在進行 V8 引擎分析 (黃金鎖定 + 語義防火牆)...'):
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
        
        st.success(f"✅ 成功擷取 {len(all_data)} 份報告！(V8 核心)")
        st.dataframe(display_df, use_container_width=True)
        
        csv = display_df.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            label="📥 下載 Excel/CSV 報表",
            data=csv,
            file_name="rohs_report_v8_final.csv",
            mime="text/csv",
        )

    if scanned_files:
        st.error("⚠️ 以下檔案為掃描圖片 (無法擷取文字)：")
        for f in scanned_files:
            st.write(f"- {f}")

else:
    st.info("請上傳 PDF 檔案以開始分析。")
