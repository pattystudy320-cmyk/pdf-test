import streamlit as st
import pdfplumber
import pandas as pd
import re
import math

# --- 設定頁面 ---
st.set_page_config(page_title="通用檢測報告擷取工具 (V7 終極版)", layout="wide")
st.title("🧪 通用型第三方檢測報告數據擷取工具 (V7 終極版)")
st.markdown("""
**V7 核心升級：**
1.  **📅 智能日期鎖定**：排除測試週期與接收日，只抓首頁簽發日期 (YYYY/MM/DD)。
2.  **🎯 絕對座標鎖定**：文字模式下只抓取 Result 標題正下方的數值，排除左側序號與右側限值。
3.  **🛡️ 強力防呆機制**：自動過濾 CAS No.、年份、與 MDL/Limit 雷同的數值。
""")

# --- 1. 定義目標欄位與關鍵字 ---
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
    """日期標準化為 YYYY/MM/DD"""
    if not date_str: return ""
    # 移除前綴與無關字元
    clean_date = re.sub(r"Date:|Issue Date:|Report Date:|日期[:：]?", "", date_str, flags=re.IGNORECASE).strip()
    
    try:
        # 1. 數字格式 (2025.06.16, 2025/06/16)
        match_num = re.search(r"(\d{4})[-/. ](\d{1,2})[-/. ](\d{1,2})", clean_date)
        if match_num:
            return f"{match_num.group(1)}/{int(match_num.group(2)):02d}/{int(match_num.group(3)):02d}"
        
        # 2. 英文格式
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
    return "" # 格式不符則回傳空

def find_date_in_first_page(text):
    """
    V7 日期鎖定邏輯：
    1. 只看第一頁。
    2. 排除 'Received', 'Period', 'Started' 等關鍵字所在的行。
    3. 優先尋找 'Date:', 'Issue Date' 等關鍵字。
    """
    lines = text.split('\n')
    
    # 關鍵字優先級搜尋
    # 1. 強力關鍵字 (Report Date, Issue Date)
    for line in lines:
        if "RECEIVED" in line.upper() or "PERIOD" in line.upper() or "STARTED" in line.upper(): continue
        if re.search(r"(Issue Date|Report Date|签发日期)[:：\s]", line, re.IGNORECASE):
             d = normalize_date(line)
             if d: return d

    # 2. 普通關鍵字 (Date)
    for line in lines:
        if "RECEIVED" in line.upper() or "PERIOD" in line.upper() or "STARTED" in line.upper(): continue
        if re.search(r"(Date|日期)[:：\s]", line, re.IGNORECASE):
             d = normalize_date(line)
             if d: return d
             
    # 3. 孤兒日期 (沒有標題，但格式像日期，通常在頁首或頁尾)
    for line in lines:
        if "RECEIVED" in line.upper() or "PERIOD" in line.upper() or "STARTED" in line.upper(): continue
        # 嚴格匹配完整日期格式
        if re.search(r"^\s*(\d{4}[-/. ]\d{1,2}[-/. ]\d{1,2})\s*$", line):
            return normalize_date(line)
        if re.search(r"^\s*([A-Za-z]{3}\.?\s+\d{1,2},?\s+\d{4})\s*$", line):
            return normalize_date(line)
            
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
        
        # 自身特徵
        if any(ex in txt for ex in exclude_kw): score -= 100
        if any(res in txt for res in result_kw): score += 50
        if "CAS" in txt: score -= 200 
        
        # 鄰居特徵 (右邊是 MDL?)
        if i + 1 < num_cols:
            right_txt = clean_text(str(header_cells[i+1])).upper()
            if any(k in right_txt for k in mdl_kw): score += 30
            
        # 鄰居特徵 (左邊是 Item?)
        if i - 1 >= 0:
            left_txt = clean_text(str(header_cells[i-1])).upper()
            if "ITEM" in left_txt or "项目" in left_txt: score += 20
            
        scores[i] = score

    # 數據指紋驗證
    if table_data and len(table_data) > 3:
        for i in range(num_cols):
            if i not in scores: continue
            sample_vals = []
            for row in table_data[1:5]:
                if i < len(row): sample_vals.append(clean_text(str(row[i])).upper())
            
            is_numeric_or_nd = 0
            is_cas = 0
            is_method = 0
            
            for val in sample_vals:
                if "N.D." in val or "NEGATIVE" in val or re.search(r"^\d+(\.\d+)?$", val):
                    is_numeric_or_nd += 1
                if re.search(r"\d{2,7}-\d{2}-\d", val): is_cas += 1
                if "IEC" in val or "EPA" in val: is_method += 1
            
            if is_cas > 0: scores[i] -= 200
            if is_method > 0: scores[i] -= 100
            if is_numeric_or_nd > 0: scores[i] += 20

    if not scores: return -1
    best_col = max(scores, key=scores.get)
    if scores[best_col] < -50: return -1
    return best_col

def extract_value_logic(val_str):
    """
    V7 嚴格數值提取：
    1. 擋掉 CAS。
    2. 擋掉 年份。
    3. 擋掉 序號 (通常是個位數整數，但如果結果真的只有 1 ppm 怎麼辦？ 
       -> 我們依賴欄位鎖定，如果欄位鎖定正確，這裡就不會抓到序號)
    """
    if not val_str: return 0, "N.D."
    
    val_upper = str(val_str).upper().replace(" ", "")
    
    # CAS 防火牆
    if re.search(r"\b\d{2,7}-\d{2}-\d\b", val_str): return 0, "N.D."

    if "N.D." in val_upper or "ND" in val_upper or "<" in val_upper: return 0, "N.D."
    if "NEGATIVE" in val_upper or "阴性" in val_upper: return 0.0001, "NEGATIVE"
    if "POSITIVE" in val_upper or "阳性" in val_upper: return 999999, "POSITIVE"
    
    val_clean = re.sub(r"(mg/kg|ppm|%|µg/cm²|ug/cm2)", "", val_str, flags=re.IGNORECASE)
    match = re.search(r"(\d+(\.\d+)?)", val_clean)
    
    if match:
        num = float(match.group(1))
        # 年份過濾
        if 2010 <= num <= 2030: return 0, "N.D."
        return num, match.group(1)
    
    return 0, "N.D."

# --- 3. 核心處理邏輯 ---

def process_file(uploaded_file):
    filename = uploaded_file.name
    # 這裡的 val 初始值設為 -1，代表「尚未抓取」
    results = {k: {"val": -1, "display": ""} for k in TARGET_FIELDS.keys()}
    results["PBBs"] = {"val": 0, "display": "", "sum_val": 0}
    results["PBDEs"] = {"val": 0, "display": "", "sum_val": 0}
    results["PFAS"] = ""
    results["Date"] = ""
    
    is_scanned = True
    full_text_content = ""
    
    with pdfplumber.open(uploaded_file) as pdf:
        # A. 全文掃描 & 首頁日期
        for i, page in enumerate(pdf.pages):
            text = page.extract_text()
            if text and len(text) > 50:
                is_scanned = False
                full_text_content += text + "\n"
                if i == 0: results["Date"] = find_date_in_first_page(text)

        if is_scanned: return None, filename

        if "PFAS" in full_text_content.upper() or "PER- AND POLYFLUOROALKYL" in full_text_content.upper():
            results["PFAS"] = "REPORT"

        # B. 表格數據提取 (絕對忠誠模式)
        for page in pdf.pages:
            tables = page.extract_tables()
            
            # --- 模式 1: 結構化表格 ---
            if tables:
                for table in tables:
                    if not table or len(table) < 2: continue
                    
                    header_row_idx = -1
                    result_col_idx = -1
                    
                    # 尋找表頭
                    for r_idx, row in enumerate(table[:6]):
                        row_str = " ".join([str(c).upper() for c in row if c])
                        if ("ITEM" in row_str or "项目" in row_str) and ("UNIT" in row_str or "MDL" in row_str or "RESULT" in row_str or "结果" in row_str):
                            header_row_idx = r_idx
                            result_col_idx = get_column_score(row, table)
                            
                            # 巢狀表頭修正
                            if result_col_idx == -1 and r_idx + 1 < len(table):
                                next_row = table[r_idx+1]
                                result_col_idx = get_column_score(next_row, table)
                            break
                    
                    if result_col_idx != -1:
                        # 鎖定欄位抓取
                        for r_idx in range(header_row_idx + 1, len(table)):
                            row = table[r_idx]
                            if len(row) <= result_col_idx: continue
                            
                            item_name = clean_text(row[0])
                            if len(row) > 1: item_name += " " + clean_text(row[1])
                            
                            val_text = clean_text(row[result_col_idx])
                            # 這裡傳入 is_absolute=True，表示信任此欄位
                            update_results(results, item_name, val_text, is_absolute=True)

            # --- 模式 2: 文字流 (X 軸重心鎖定) ---
            words = page.extract_words(keep_blank_chars=True)
            
            # 1. 尋找 Result 標題的 X 座標
            target_x_center = -1
            for w in words:
                txt = w['text'].upper()
                if txt in ["RESULT", "结果", "SAMPLE", "001", "A1"] and "ITEM" not in txt: 
                    # 簡單過濾一下避免抓到 Test Item
                    target_x_center = (w['x0'] + w['x1']) / 2
                    break
            
            # 沒找到 Result 標題就跳過文字流模式 (避免亂抓)
            if target_x_center == -1: continue 
            
            rows = {}
            for w in words:
                y = round(w['top'] / 5) * 5
                if y not in rows: rows[y] = []
                rows[y].append(w)
            
            for y, row_words in rows.items():
                line_text = " ".join([w['text'] for w in row_words])
                
                # 掃描目標項目
                for field, config in TARGET_FIELDS.items():
                    for kw in config["keywords"]:
                        if re.search(kw, line_text, re.IGNORECASE):
                            if field == "Chlorine" and "POLYVINYL" in line_text.upper(): continue
                            
                            # 在此行尋找數值，但只接受 X 座標在 target_x 附近的
                            valid_vals = []
                            for w in row_words:
                                w_center = (w['x0'] + w['x1']) / 2
                                # 允許誤差範圍 +/- 100 (視排版寬度而定，可調整)
                                if abs(w_center - target_x_center) < 150: 
                                    val, disp = extract_value_logic(w['text'])
                                    if val > 0 or disp in ["N.D.", "NEGATIVE"]:
                                        valid_vals.append((val, disp))
                            
                            # 如果有找到位於 Result 區域的值，更新
                            if valid_vals:
                                best_val, best_disp = valid_vals[0] # 取第一個符合位置的
                                update_results(results, field, best_disp)

                # PBBs/PBDEs 加總
                for pbb_kw in PBBS_KEYWORDS + PBDES_KEYWORDS:
                    if re.search(pbb_kw, line_text, re.IGNORECASE):
                        for w in row_words:
                            w_center = (w['x0'] + w['x1']) / 2
                            if abs(w_center - target_x_center) < 150:
                                val, disp = extract_value_logic(w['text'])
                                if val > 0 and val != 1000:
                                    cat = "PBBs" if any(k in pbb_kw for k in PBBS_KEYWORDS) else "PBDEs"
                                    results[cat]["sum_val"] += val
                                    break

    finalize_results(results)
    
    # 填充未抓取的值為 N.D. (如果還在初始狀態)
    for k, v in results.items():
        if isinstance(v, dict) and "val" in v and v["val"] == -1:
            v["val"] = 0
            v["display"] = "N.D."

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

def update_results(results, item_name, val_text, is_absolute=False):
    """
    更新邏輯：
    1. is_absolute (表格模式): 強制更新，覆蓋舊值 (因為表格定位最準)。
    2. 一般模式: 只更新比舊值「更好」的值 (數字 > N.D.)。
    """
    item_upper = str(item_name).upper()
    if "CHLORINE" in item_upper and "POLYVINYL" in item_upper: return

    val_num, val_disp = extract_value_logic(val_text)
    
    # 一般項目
    for field_key, config in TARGET_FIELDS.items():
        for kw in config["keywords"]:
            if re.search(kw, item_upper, re.IGNORECASE):
                # 如果是絕對模式，直接寫入 (除非是空的)
                if is_absolute:
                    if val_text: # 確保不是空字串
                        results[field_key]["val"] = val_num
                        results[field_key]["display"] = val_disp
                    return

                # 一般模式：比較大小 (舊值為 -1 表示尚未有值)
                if val_num > results[field_key]["val"]:
                    results[field_key]["val"] = val_num
                    results[field_key]["display"] = val_disp
                elif val_num == 0 and results[field_key]["val"] <= 0: # 如果新舊都是 0
                    if val_disp == "NEGATIVE": results[field_key]["display"] = "NEGATIVE"
                    elif not results[field_key]["display"] or results[field_key]["val"] == -1: 
                        results[field_key]["display"] = "N.D."
                        results[field_key]["val"] = 0
                return

    # PBBs/PBDEs 加總
    for pbb_kw in PBBS_KEYWORDS:
        if re.search(pbb_kw, item_upper, re.IGNORECASE):
            results["PBBs"]["sum_val"] += val_num
            return

    for pbde_kw in PBDES_KEYWORDS:
        if re.search(pbde_kw, item_upper, re.IGNORECASE):
            results["PBDEs"]["sum_val"] += val_num
            return

def finalize_results(results):
    if results["PBBs"]["sum_val"] > 0:
        results["PBBs"]["display"] = str(round(results["PBBs"]["sum_val"], 2))
        results["PBBs"]["val"] = results["PBBs"]["sum_val"]
    elif not results["PBBs"]["display"]: 
        results["PBBs"]["display"] = "N.D."
        results["PBBs"]["val"] = 0

    if results["PBDEs"]["sum_val"] > 0:
        results["PBDEs"]["display"] = str(round(results["PBDEs"]["sum_val"], 2))
        results["PBDEs"]["val"] = results["PBDEs"]["sum_val"]
    elif not results["PBDEs"]["display"]: 
        results["PBDEs"]["display"] = "N.D."
        results["PBDEs"]["val"] = 0

# --- 主介面 ---

uploaded_files = st.file_uploader("請上傳 PDF 檢測報告 (支援 SGS, CTI, Intertek 等)", type="pdf", accept_multiple_files=True)

if uploaded_files:
    all_data = []
    scanned_files = []

    with st.spinner('正在進行 V7 引擎分析 (絕對座標鎖定 + 首頁日期模組)...'):
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
        
        st.success(f"✅ 成功擷取 {len(all_data)} 份報告！(V7 核心)")
        st.dataframe(display_df, use_container_width=True)
        
        csv = display_df.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            label="📥 下載 Excel/CSV 報表",
            data=csv,
            file_name="rohs_report_v7_final.csv",
            mime="text/csv",
        )

    if scanned_files:
        st.error("⚠️ 以下檔案為掃描圖片 (無法擷取文字)：")
        for f in scanned_files:
            st.write(f"- {f}")

else:
    st.info("請上傳 PDF 檔案以開始分析。")
