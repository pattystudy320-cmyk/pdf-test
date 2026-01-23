import streamlit as st
import pdfplumber
import pandas as pd
import re
import math

# --- 設定頁面 ---
st.set_page_config(page_title="通用檢測報告擷取工具 (V6 旗艦版)", layout="wide")
st.title("🧪 通用型第三方檢測報告數據擷取工具 (V6 旗艦版)")
st.markdown("""
**V6 核心引擎技術：**
1.  **🧠 權重評分系統**：不只看標題，還分析欄位內容與「左右鄰居」(如右邊是 MDL 則加分)，精準鎖定結果欄。
2.  **🔍 資料指紋分析**：自動識別並排除 CAS 編號、法規年份 (2015)、限值 (1000) 等雜訊。
3.  **🛡️ 語義防禦機制**：解決 Chlorine 誤抓 Polyvinyl Chloride (Negative) 的問題。
4.  **📅 智能日期鎖定**：僅鎖定報告首頁簽發日期，標準化為 YYYY/MM/DD。
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
    clean_date = re.sub(r"Date:|Issue Date:|Report Date:|日期[:：]?", "", date_str, flags=re.IGNORECASE).strip()
    try:
        # 2025.05.26 / 2025-05-26
        match_num = re.search(r"(\d{4})[-/. ](\d{1,2})[-/. ](\d{1,2})", clean_date)
        if match_num:
            return f"{match_num.group(1)}/{int(match_num.group(2)):02d}/{int(match_num.group(3)):02d}"
        
        # 英文月份處理
        months = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6, 
                  "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
        
        # 16-Jun-25
        match_dd_mon_yy = re.search(r"(\d{1,2})[-/\s]([A-Za-z]{3})[-/\s](\d{2,4})", clean_date, re.IGNORECASE)
        if match_dd_mon_yy:
            d, m_str, y = match_dd_mon_yy.groups()
            m = months.get(m_str.upper(), 0)
            if m > 0:
                if len(y) == 2: y = "20" + y
                return f"{y}/{m:02d}/{int(d):02d}"

        # Jan 08, 2025
        match_mon_dd_yyyy = re.search(r"([A-Za-z]{3})\.?\s+(\d{1,2}),?\s+(\d{4})", clean_date, re.IGNORECASE)
        if match_mon_dd_yyyy:
            m_str, d, y = match_mon_dd_yyyy.groups()
            m = months.get(m_str.upper(), 0)
            if m > 0:
                return f"{y}/{m:02d}/{int(d):02d}"
    except:
        pass
    return clean_date

def find_date_in_first_page(text):
    """只在第一頁抓取日期"""
    lines = text.split('\n')
    for line in lines:
        if "RECEIVED" in line.upper() or "PERIOD" in line.upper() or "STARTED" in line.upper(): continue
        
        if re.search(r"(Date:|Issue Date|Report Date|日期[:：])", line, re.IGNORECASE):
            # 優先匹配完整日期格式
            m1 = re.search(r"(\d{4}[-/. ]\d{1,2}[-/. ]\d{1,2})", line)
            if m1: return normalize_date(m1.group(1))
            
            m2 = re.search(r"([A-Za-z]{3}\.?\s+\d{1,2},?\s+\d{4})", line)
            if m2: return normalize_date(m2.group(1))
            
            m3 = re.search(r"(\d{1,2}-[A-Za-z]{3}-\d{2,4})", line)
            if m3: return normalize_date(m3.group(1))
    return ""

# --- 3. 核心邏輯：權重評分系統 ---

def get_column_score(header_cells, table_data=None):
    """
    對每一欄進行評分，找出最可能是 Result 的欄位索引。
    考量：標題關鍵字、左右鄰居、欄位內容指紋。
    """
    scores = {} # col_idx -> score
    num_cols = len(header_cells)
    
    # 關鍵字定義
    exclude_kw = ["ITEM", "METHOD", "UNIT", "MDL", "LOQ", "LIMIT", "REQUIREMENT", "项目", "方法", "单位", "限值", "RL", "CAS", "NO."]
    result_kw = ["RESULT", "结果", "SAMPLE", "ID", "001", "002", "A1", "DATA", "含量"]
    mdl_kw = ["MDL", "LOQ", "RL", "LIMIT", "限值"]
    
    for i, cell in enumerate(header_cells):
        if not cell: continue
        txt = clean_text(str(cell)).upper()
        
        score = 0
        
        # 1. 自身特徵
        if any(ex in txt for ex in exclude_kw): score -= 100
        if any(res in txt for res in result_kw): score += 50
        if "CAS" in txt: score -= 200 # CAS 絕對排除
        
        # 2. 鄰居特徵 (拓樸關係)
        # 檢查右邊 (i+1) 是否為 MDL/Limit (SGS 常見)
        if i + 1 < num_cols:
            right_txt = clean_text(str(header_cells[i+1])).upper()
            if any(k in right_txt for k in mdl_kw): score += 30
            
        # 檢查左邊 (i-1) 是否為 Item (CTI 常見)
        if i - 1 >= 0:
            left_txt = clean_text(str(header_cells[i-1])).upper()
            if "ITEM" in left_txt or "项目" in left_txt: score += 20
            
        scores[i] = score

    # 3. 數據指紋驗證 (Data Fingerprinting) - 偷看前幾行內容
    if table_data and len(table_data) > 3:
        for i in range(num_cols):
            if i not in scores: continue
            
            # 檢查該欄位在前幾行的內容
            sample_vals = []
            for row in table_data[1:5]: # 取前5行數據
                if i < len(row): sample_vals.append(clean_text(str(row[i])).upper())
            
            # 判斷特徵
            is_numeric_or_nd = 0
            is_cas = 0
            is_method = 0
            
            for val in sample_vals:
                if "N.D." in val or "NEGATIVE" in val or re.search(r"^\d+(\.\d+)?$", val):
                    is_numeric_or_nd += 1
                if re.search(r"\d{2,7}-\d{2}-\d", val): # CAS 格式
                    is_cas += 1
                if "IEC" in val or "EPA" in val:
                    is_method += 1
            
            if is_cas > 0: scores[i] -= 200
            if is_method > 0: scores[i] -= 100
            if is_numeric_or_nd > 0: scores[i] += 20 # 內容像數據，加分

    # 找出最高分
    if not scores: return -1
    best_col = max(scores, key=scores.get)
    
    # 門檻值：如果最高分仍很低 (例如都是 Method)，則不回傳
    if scores[best_col] < -50: return -1
    
    return best_col

def extract_value_logic(val_str, mdl_val=None, limit_val=None):
    """
    數值提取與防呆機制
    """
    if not val_str: return 0, "N.D."
    
    val_upper = str(val_str).upper().replace(" ", "")
    
    # 1. CAS 防火牆
    if re.search(r"\b\d{2,7}-\d{2}-\d\b", val_str): return 0, "N.D."

    # 2. 文字狀態
    if "N.D." in val_upper or "ND" in val_upper or "<" in val_upper: return 0, "N.D."
    if "NEGATIVE" in val_upper or "阴性" in val_upper: return 0.0001, "NEGATIVE"
    if "POSITIVE" in val_upper or "阳性" in val_upper: return 999999, "POSITIVE"
    
    # 3. 數字提取
    val_clean = re.sub(r"(mg/kg|ppm|%|µg/cm²|ug/cm2)", "", val_str, flags=re.IGNORECASE)
    match = re.search(r"(\d+(\.\d+)?)", val_clean)
    
    if match:
        num = float(match.group(1))
        
        # 4. 防呆機制 (Sanity Check)
        # 排除年份 (2011, 2015, 2025)
        if 2010 <= num <= 2030: return 0, "N.D." # 假設檢測值極少剛好落在這區間且為整數
        
        # 排除 Limit / MDL (如果剛好抓到 1000 或 100)
        if num in [100, 1000] and "ND" not in val_upper:
             # 如果這個數字跟 MDL 或 Limit 一樣，可能是抓錯欄位
             pass 
             
        return num, match.group(1)
    
    return 0, "N.D."

def process_file(uploaded_file):
    filename = uploaded_file.name
    results = {k: {"val": 0, "display": ""} for k in TARGET_FIELDS.keys()}
    results["PBBs"] = {"val": 0, "display": "", "sum_val": 0}
    results["PBDEs"] = {"val": 0, "display": "", "sum_val": 0}
    results["PFAS"] = ""
    results["Date"] = ""
    
    is_scanned = True
    full_text_content = ""
    
    with pdfplumber.open(uploaded_file) as pdf:
        # A. 全文掃描 & 日期
        for i, page in enumerate(pdf.pages):
            text = page.extract_text()
            if text and len(text) > 50:
                is_scanned = False
                full_text_content += text + "\n"
                if i == 0: results["Date"] = find_date_in_first_page(text)

        if is_scanned: return None, filename

        if "PFAS" in full_text_content.upper() or "PER- AND POLYFLUOROALKYL" in full_text_content.upper():
            results["PFAS"] = "REPORT"

        # B. 表格數據提取 (優先)
        for page in pdf.pages:
            tables = page.extract_tables()
            if tables:
                for table in tables:
                    if not table or len(table) < 2: continue
                    
                    df = pd.DataFrame(table)
                    header_row_idx = -1
                    result_col_idx = -1
                    
                    # 尋找表頭 (包含垂直合併處理)
                    for r_idx, row in enumerate(table[:6]):
                        row_str = " ".join([str(c).upper() for c in row if c])
                        if ("ITEM" in row_str or "项目" in row_str):
                            header_row_idx = r_idx
                            result_col_idx = get_column_score(row, table) # 評分系統
                            
                            # CTI 巢狀表頭處理: 如果評分失敗，嘗試下一行
                            if result_col_idx == -1 and r_idx + 1 < len(table):
                                result_col_idx = get_column_score(table[r_idx+1], table)
                            break
                    
                    if result_col_idx != -1:
                        for r_idx in range(header_row_idx + 1, len(table)):
                            row = table[r_idx]
                            if len(row) <= result_col_idx: continue
                            
                            item_name = clean_text(row[0])
                            if len(row) > 1: item_name += " " + clean_text(row[1])
                            val_text = clean_text(row[result_col_idx])
                            
                            update_results(results, item_name, val_text)

            # C. 文字流模式 (Fallback for invisible tables)
            # 使用 extract_words 做簡易行對齊
            words = page.extract_words(keep_blank_chars=True)
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
                            # 語義防禦: 排除 Polyvinyl Chloride 誤判為 Chlorine
                            if field == "Chlorine" and "POLYVINYL" in line_text.upper():
                                continue
                                
                            parts = line_text.split()
                            valid_parts = [p for p in parts if not re.search(r"\d{2,7}-\d{2}-\d", p)]
                            for part in reversed(valid_parts): # 從右邊找
                                val, disp = extract_value_logic(part)
                                # 排除年份與Limit (文字模式較寬鬆，需嚴格檢查)
                                if val not in [100, 1000, 2011, 2015] and (val > 0 or disp in ["N.D.", "NEGATIVE"]):
                                    update_results(results, field, disp, is_text_mode=True)
                                    break
                
                # PBBs/PBDEs 加總
                for pbb_kw in PBBS_KEYWORDS + PBDES_KEYWORDS:
                    if re.search(pbb_kw, line_text, re.IGNORECASE):
                        parts = line_text.split()
                        for part in reversed(parts):
                            val, disp = extract_value_logic(part)
                            if val > 0 and val not in [1000, 5, 25]:
                                cat = "PBBs" if any(k in pbb_kw for k in PBBS_KEYWORDS) else "PBDEs"
                                results[cat]["sum_val"] += val
                                break

    finalize_results(results)
    
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
        "_sort_max": max([v["val"] for k, v in results.items() if isinstance(v, dict) and "val" in v])
    }
    
    return final_output, None

def update_results(results, item_name, val_text, is_text_mode=False):
    item_upper = str(item_name).upper()
    
    # 語義防禦: 防止 Chlorine 抓到 Polyvinyl Chloride
    if "CHLORINE" in item_upper and "POLYVINYL" in item_upper: return

    val_num, val_disp = extract_value_logic(val_text)
    
    for field_key, config in TARGET_FIELDS.items():
        for kw in config["keywords"]:
            if re.search(kw, item_upper, re.IGNORECASE):
                if is_text_mode and results[field_key]["val"] > 0: return
                
                if val_num > results[field_key]["val"]:
                    results[field_key]["val"] = val_num
                    results[field_key]["display"] = val_disp
                elif val_num == 0 and results[field_key]["val"] == 0:
                    if val_disp == "NEGATIVE": results[field_key]["display"] = "NEGATIVE"
                    elif not results[field_key]["display"]: results[field_key]["display"] = "N.D."
                return

    for pbb_kw in PBBS_KEYWORDS:
        if re.search(pbb_kw, item_upper, re.IGNORECASE):
            if is_text_mode: return
            results["PBBs"]["sum_val"] += val_num
            return

    for pbde_kw in PBDES_KEYWORDS:
        if re.search(pbde_kw, item_upper, re.IGNORECASE):
            if is_text_mode: return
            results["PBDEs"]["sum_val"] += val_num
            return

def finalize_results(results):
    if results["PBBs"]["sum_val"] > 0:
        results["PBBs"]["display"] = str(round(results["PBBs"]["sum_val"], 2))
        results["PBBs"]["val"] = results["PBBs"]["sum_val"]
    elif not results["PBBs"]["display"]: results["PBBs"]["display"] = "N.D."

    if results["PBDEs"]["sum_val"] > 0:
        results["PBDEs"]["display"] = str(round(results["PBDEs"]["sum_val"], 2))
        results["PBDEs"]["val"] = results["PBDEs"]["sum_val"]
    elif not results["PBDEs"]["display"]: results["PBDEs"]["display"] = "N.D."

# --- 主介面 ---

uploaded_files = st.file_uploader("請上傳 PDF 檢測報告 (支援 SGS, CTI, Intertek 等)", type="pdf", accept_multiple_files=True)

if uploaded_files:
    all_data = []
    scanned_files = []

    with st.spinner('正在使用 V6 引擎分析 (權重評分 + 語義防禦 + 資料指紋)...'):
        for pdf_file in uploaded_files:
            data, scanned_name = process_file(pdf_file)
            if scanned_name:
                scanned_files.append(scanned_name)
            else:
                all_data.append(data)

    if all_data:
        df = pd.DataFrame(all_data)
        df = df.sort_values(by=["_sort_pb", "_sort_max"], ascending=[False, False])
        display_df = df.drop(columns=["_sort_pb", "_sort_max"])
        
        st.success(f"✅ 成功擷取 {len(all_data)} 份報告！(V6 核心)")
        st.dataframe(display_df, use_container_width=True)
        
        csv = display_df.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            label="📥 下載 Excel/CSV 報表",
            data=csv,
            file_name="rohs_report_v6.csv",
            mime="text/csv",
        )

    if scanned_files:
        st.error("⚠️ 以下檔案為掃描圖片 (無法擷取文字)：")
        for f in scanned_files:
            st.write(f"- {f}")

else:
    st.info("請上傳 PDF 檔案以開始分析。")
