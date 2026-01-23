import streamlit as st
import pdfplumber
import pandas as pd
import re
from datetime import datetime

# --- 設定頁面 ---
st.set_page_config(page_title="通用檢測報告擷取工具 (V5 最終版)", layout="wide")
st.title("🧪 通用型第三方檢測報告數據擷取工具 (V5 最終版)")
st.markdown("""
**V5 版本更新重點：**
1. **🛡️ CAS 防火牆**：自動識別並過濾 CAS No.，解決誤抓化學編號 (如 1763, 85) 的問題。
2. **📅 日期標準化**：鎖定首頁報告日期，統一轉為 `YYYY/MM/DD`，排除測試週期干擾。
3. **🎯 垂直表頭修正**：優化對巢狀表頭 (Result 下方有 Sample ID) 的定位能力。
""")

# --- 1. 定義目標欄位與關鍵字聯集 ---
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
    """將各種日期格式統一轉為 YYYY/MM/DD"""
    if not date_str: return ""
    
    # 移除多餘雜訊
    clean_date = re.sub(r"Date:|Issue Date:|Report Date:|日期[:：]?", "", date_str, flags=re.IGNORECASE).strip()
    
    try:
        # 嘗試解析常見格式
        # 1. 2025.05.26 or 2025/05/26 or 2025-05-26
        match_num = re.search(r"(\d{4})[-/. ](\d{1,2})[-/. ](\d{1,2})", clean_date)
        if match_num:
            return f"{match_num.group(1)}/{int(match_num.group(2)):02d}/{int(match_num.group(3)):02d}"
            
        # 2. Jan 08, 2025 or 16-Jun-25
        # 這裡簡單處理英文月份轉換
        months = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6, 
                  "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
        
        # 格式: 16-Jun-25 (DD-Mon-YY)
        match_dd_mon_yy = re.search(r"(\d{1,2})[-/\s]([A-Za-z]{3})[-/\s](\d{2,4})", clean_date, re.IGNORECASE)
        if match_dd_mon_yy:
            d, m_str, y = match_dd_mon_yy.groups()
            m = months.get(m_str.upper(), 0)
            if m > 0:
                if len(y) == 2: y = "20" + y
                return f"{y}/{m:02d}/{int(d):02d}"

        # 格式: Jan 08, 2025 (Mon DD, YYYY)
        match_mon_dd_yyyy = re.search(r"([A-Za-z]{3})\.?\s+(\d{1,2}),?\s+(\d{4})", clean_date, re.IGNORECASE)
        if match_mon_dd_yyyy:
            m_str, d, y = match_mon_dd_yyyy.groups()
            m = months.get(m_str.upper(), 0)
            if m > 0:
                return f"{y}/{m:02d}/{int(d):02d}"
                
    except:
        pass
    
    return clean_date # 如果真的轉不了，回傳原字串

def extract_value_logic(val_str):
    """
    數值提取邏輯 (V5 增強版：CAS 過濾)
    """
    if not val_str: return 0, "N.D."
    
    val_upper = str(val_str).upper().replace(" ", "")
    
    # 1. 優先處理 CAS 編號 (V5 新增防火牆)
    # 格式如 1763-23-1, 85-68-7
    if re.search(r"\b\d{2,7}-\d{2}-\d\b", val_str):
        return 0, "N.D." # 這是 CAS 編號，不是結果，強制回傳 N.D.

    # 2. 處理文字狀態
    if "N.D." in val_upper or "ND" in val_upper or "<" in val_upper:
        return 0, "N.D."
    if "NEGATIVE" in val_upper or "阴性" in val_upper:
        return 0.0001, "NEGATIVE"
    if "POSITIVE" in val_upper or "阳性" in val_upper:
        return 999999, "POSITIVE"
    
    # 3. 處理數字
    val_clean = re.sub(r"(mg/kg|ppm|%)", "", val_str, flags=re.IGNORECASE)
    match = re.search(r"(\d+(\.\d+)?)", val_clean)
    
    if match:
        num = float(match.group(1))
        # 簡單過濾 Limit/MDL 常見值 (輔助)
        if num in [100, 1000, 2, 5, 8, 10, 25, 50] and "ND" not in val_upper:
             # 這是一個風險判斷，但在表格鎖定失效時很有用
             pass 
        return num, match.group(1)
    
    return 0, "N.D."

def find_date_in_first_page(text):
    """
    只在第一頁抓日期 (V5 增強版：排除 Testing Period)
    """
    # 關鍵字：必須是 Date, Issue Date, Report Date, 日期
    # 且後面不能接 "Received" (接收日) 或 "Started" (開始日)
    
    lines = text.split('\n')
    for line in lines:
        # 排除 Sample Received Date, Testing Period
        if "RECEIVED" in line.upper() or "PERIOD" in line.upper() or "STARTED" in line.upper():
            continue
            
        if re.search(r"(Date:|Issue Date|Report Date|日期[:：])", line, re.IGNORECASE):
            # 找到日期行，嘗試提取日期
            # 格式 1: YYYY/MM/DD
            m1 = re.search(r"(\d{4}[-/. ]\d{1,2}[-/. ]\d{1,2})", line)
            if m1: return normalize_date(m1.group(1))
            
            # 格式 2: Jan 08, 2025
            m2 = re.search(r"([A-Za-z]{3}\.?\s+\d{1,2},?\s+\d{4})", line)
            if m2: return normalize_date(m2.group(1))
            
            # 格式 3: DD-Mon-YY
            m3 = re.search(r"(\d{1,2}-[A-Za-z]{3}-\d{2,4})", line)
            if m3: return normalize_date(m3.group(1))

    return ""

def get_column_strategy(header_cells):
    """
    十字座標鎖定 (V5 增強版：CAS 排除)
    """
    # 排除清單加入 CAS, CAS No.
    exclude_keywords = ["ITEM", "METHOD", "UNIT", "MDL", "LOQ", "LIMIT", "REQUIREMENT", 
                        "项目", "方法", "单位", "限值", "RL", "CAS", "NO."]
    
    target_keywords = ["RESULT", "结果", "SAMPLE", "ID", "001", "002", "A1", "DATA"]
    
    best_col_idx = -1
    max_score = -1
    
    for i, cell in enumerate(header_cells):
        if not cell: continue
        txt = clean_text(cell).upper()
        
        # 強制排除 CAS
        if "CAS" in txt: continue 
        
        if any(ex in txt for ex in exclude_keywords): continue
            
        score = 0
        if any(tg in txt for tg in target_keywords): score += 10
        
        # 優先選中間或靠後的欄位 (通常 Result 不會是第一欄)
        if i > 0: score += 1 

        if score > max_score:
            max_score = score
            best_col_idx = i
            
    # Fallback: 如果沒找到明確 Result，取最後一個非排除欄位
    if best_col_idx == -1:
        valid_indices = [i for i, c in enumerate(header_cells) if c and "CAS" not in str(c).upper() and not any(ex in clean_text(c).upper() for ex in exclude_keywords)]
        if valid_indices:
            best_col_idx = valid_indices[0] 
            
    return best_col_idx

# --- 3. 核心處理邏輯 ---

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
        # --- A. 全文掃描 & 首頁日期 ---
        for i, page in enumerate(pdf.pages):
            text = page.extract_text()
            if text and len(text) > 50:
                is_scanned = False
                full_text_content += text + "\n"
                if i == 0:
                    results["Date"] = find_date_in_first_page(text)

        if is_scanned:
            return None, filename

        # PFAS
        if "PFAS" in full_text_content.upper() or "PER- AND POLYFLUOROALKYL" in full_text_content.upper():
            results["PFAS"] = "REPORT"

        # --- B. 表格數據提取 ---
        for page in pdf.pages:
            tables = page.extract_tables()
            
            # 模式 1: 結構化表格
            if tables:
                for table in tables:
                    if not table or len(table) < 2: continue
                    
                    df = pd.DataFrame(table)
                    header_row_idx = -1
                    result_col_idx = -1
                    
                    # 掃描表頭 (找 Result, 避開 CAS/Method)
                    for r_idx, row in enumerate(table[:5]):
                        row_text = " ".join([str(c).upper() for c in row if c])
                        # 只要有 Item/項目 且有 Unit/Result/MDL 就算表頭
                        if ("ITEM" in row_text or "项目" in row_text) and ("UNIT" in row_text or "MDL" in row_text or "RESULT" in row_text or "结果" in row_text):
                            header_row_idx = r_idx
                            result_col_idx = get_column_strategy(row)
                            
                            # V5 新增：垂直表頭修正 (如果 Result 這一格是空的，往下找 Sample ID)
                            if result_col_idx == -1 and r_idx + 1 < len(table):
                                next_row = table[r_idx+1]
                                result_col_idx = get_column_strategy(next_row)
                            break
                    
                    if result_col_idx != -1:
                        # 抓取數據
                        for r_idx in range(header_row_idx + 1, len(table)):
                            row = table[r_idx]
                            if len(row) <= result_col_idx: continue
                            
                            item_name = clean_text(row[0])
                            if len(row) > 1: item_name += " " + clean_text(row[1]) # 組合前兩欄名稱
                            
                            val_text = clean_text(row[result_col_idx])
                            update_results(results, item_name, val_text)

            # 模式 2: 文字流 (針對隱形表格)
            # 這裡簡化：如果該頁有找到表格就不跑文字流，除非表格很少
            # 為了保險，我們只針對特定關鍵字做文字流掃描
            
            words = page.extract_words(keep_blank_chars=True)
            rows = {}
            for w in words:
                y = round(w['top'] / 5) * 5 # 模糊 Y 軸 (加大 Tolerance)
                if y not in rows: rows[y] = []
                rows[y].append(w)
            
            for y, row_words in rows.items():
                line_text = " ".join([w['text'] for w in row_words])
                
                # 掃描目標項目
                for field, config in TARGET_FIELDS.items():
                    for kw in config["keywords"]:
                        if re.search(kw, line_text, re.IGNORECASE):
                            # 在此行尋找數值，由右向左找
                            parts = line_text.split()
                            # 過濾掉 CAS 格式 (xx-xx-x)
                            valid_parts = [p for p in parts if not re.search(r"\d{2,7}-\d{2}-\d", p)]
                            
                            # 找最後一個有效的
                            for part in reversed(valid_parts):
                                val, disp = extract_value_logic(part)
                                if val > 0 or disp == "N.D." or disp == "NEGATIVE":
                                    # 再次檢查是否為 Limit (1000)
                                    if val == 1000 and disp != "N.D.": continue
                                    update_results(results, field, disp, is_text_mode=True)
                                    break
                
                # PBBs/PBDEs 加總
                for pbb_kw in PBBS_KEYWORDS + PBDES_KEYWORDS:
                    if re.search(pbb_kw, line_text, re.IGNORECASE):
                        parts = line_text.split()
                        valid_parts = [p for p in parts if not re.search(r"\d{2,7}-\d{2}-\d", p)]
                        for part in reversed(valid_parts):
                            val, disp = extract_value_logic(part)
                            if val > 0 and val != 1000: # 排除 N.D. (0) 和 Limit
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
    val_num, val_disp = extract_value_logic(val_text)
    
    # 1. 一般項目
    for field_key, config in TARGET_FIELDS.items():
        for kw in config["keywords"]:
            if re.search(kw, item_upper, re.IGNORECASE):
                if is_text_mode and results[field_key]["val"] > 0: return # 表格優先
                
                if val_num > results[field_key]["val"]:
                    results[field_key]["val"] = val_num
                    results[field_key]["display"] = val_disp
                elif val_num == 0 and results[field_key]["val"] == 0:
                    if val_disp == "NEGATIVE": results[field_key]["display"] = "NEGATIVE"
                    elif not results[field_key]["display"]: results[field_key]["display"] = "N.D."
                return

    # 2. PBBs/PBDEs 加總
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

    with st.spinner('正在進行 V5 引擎分析 (CAS 過濾 + 智能鎖定)...'):
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
        
        st.success(f"✅ 成功擷取 {len(all_data)} 份報告！(V5 核心)")
        st.dataframe(display_df, use_container_width=True)
        
        csv = display_df.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            label="📥 下載 Excel/CSV 報表",
            data=csv,
            file_name="rohs_report_v5_final.csv",
            mime="text/csv",
        )

    if scanned_files:
        st.error("⚠️ 以下檔案為掃描圖片 (無法擷取文字)：")
        for f in scanned_files:
            st.write(f"- {f}")

else:
    st.info("請上傳 PDF 檔案以開始分析。")
