import streamlit as st
import pdfplumber
import pandas as pd
import re
from datetime import datetime

# --- 設定頁面 ---
st.set_page_config(page_title="通用檢測報告擷取工具 (V15 混合架構版)", layout="wide")
st.title("🧪 通用型第三方檢測報告數據擷取工具 (V15 混合架構版)")
st.markdown("""
**V15 核心策略：集大成之作 (The Best of V7 + V10)**
1.  **∑ 有機物 (PBBs)**：**回歸 V7 邏輯** (全行暴力掃描)，無視表頭，只認數值，解決所有空值問題。
2.  **⚓ 重金屬 (Pb)**：**保留 V10 邏輯** (黃金欄位+消去法)，自動識別 "A2/No.1" 等未知表頭，確保數值精確。
3.  **📅 日期鎖定**：**V15 新邏輯** (黑名單+最晚日期法則)，精準抓取發行日。
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
    
    formats = [
        "%Y-%m-%d", "%d-%b-%Y", "%d-%B-%Y", "%b-%d-%Y", "%B-%d-%Y",
        "%d-%b-%y", "%d-%B-%y"
    ]
    for fmt in formats:
        try:
            return datetime.strptime(clean, fmt)
        except:
            continue
            
    try:
        m = re.search(r"(\d{4})[-/. ](\d{1,2})[-/. ](\d{1,2})", date_str)
        if m: return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        
        m2 = re.search(r"(\d{1,2})[-/\s]([A-Za-z]{3})[-/\s](\d{4})", date_str, re.IGNORECASE)
        if m2: return datetime.strptime(f"{m2.group(1)}-{m2.group(2)}-{m2.group(3)}", "%d-%b-%Y")
        
        m3 = re.search(r"([A-Za-z]{3})\.?\s+(\d{1,2}),?\s+(\d{4})", date_str, re.IGNORECASE)
        if m3: return datetime.strptime(f"{m3.group(2)}-{m3.group(1)}-{m3.group(3)}", "%d-%b-%Y")
    except:
        pass
    return None

def find_date_in_first_page(text):
    """
    V15 日期抓取：黑名單 + 最晚日期法則
    """
    lines = text.split('\n')
    candidates = []
    
    # 強力黑名單：出現這些字的行，裡面的日期絕對不是發行日
    blacklist = ["RECEIVED", "PERIOD", "STARTED", "SUBMITTED", "COMPLETED", "收件", "週期", "期间", "TESTING"]
    
    for line in lines:
        upper_line = line.upper()
        if any(bad in upper_line for bad in blacklist):
            continue
            
        # 尋找日期格式
        if re.search(r"\d{4}[-/. ]\d{1,2}[-/. ]\d{1,2}", line) or \
           (re.search(r"[A-Za-z]{3}", line) and re.search(r"\d{4}", line)):
            candidates.append(line)
            
    valid_dates = []
    for c in candidates:
        dt = parse_date_obj(c)
        if dt:
            if 2010 <= dt.year <= 2030:
                valid_dates.append(dt)
    
    if valid_dates:
        latest_date = max(valid_dates) # 取最晚日期 (發行日)
        return latest_date.strftime("%Y/%m/%d")
        
    return ""

def extract_value_logic(val_str, strict_numeric=False):
    if not val_str: return None, ""
    
    val_upper = str(val_str).upper().replace(" ", "")
    
    if re.search(r"\b\d{2,7}-\d{2}-\d\b", val_str): return None, "" # CAS No.

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
        # 排除年份
        if 2010 <= num <= 2030: return None, ""
        return num, match.group(1)
    
    return None, ""

def check_pfas_in_section(full_text):
    start_keywords = ["TEST REQUESTED", "測試需求", "TEST REQUEST"]
    end_keywords = ["TEST METHOD", "TEST RESULTS", "CONCLUSION", "測試結果", "結論"]
    upper_text = full_text.upper()
    start_idx = -1
    for kw in start_keywords:
        idx = upper_text.find(kw)
        if idx != -1:
            start_idx = idx
            break
    if start_idx == -1: return "" 
    end_idx = -1
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

def get_column_score(header_cells):
    """
    V15 消去法定位：
    找出 Unit, MDL, Limit，剩下的那一欄很可能就是 Result (即使它叫 A2)
    """
    scores = {} 
    num_cols = len(header_cells)
    
    # 關鍵字特徵
    result_kw = ["RESULT", "结果", "SAMPLE", "ID", "001", "002", "A1", "A2", "DATA", "含量"]
    known_cols_kw = ["ITEM", "METHOD", "UNIT", "MDL", "LOQ", "LIMIT", "REQUIREMENT", "项目", "方法", "单位", "限值", "CAS"]
    
    for i, cell in enumerate(header_cells):
        if not cell: continue
        txt = clean_text(str(cell)).upper()
        
        score = 0
        # 如果是已知欄位 (Unit/MDL/Limit)，它絕對不是結果欄，扣分
        if any(k in txt for k in known_cols_kw): score -= 500
        
        # 如果包含 Result 相關字，加分
        if any(res in txt for res in result_kw): score += 100
        
        # 額外邏輯：如果不是已知欄位，它就有可能是結果欄 (針對 A2, No.1 這種)
        if score == 0: score += 50 
            
        scores[i] = score

    if not scores: return -1
    best_col = max(scores, key=scores.get)
    if scores[best_col] < 0: return -1 # 全部都是 Unit/MDL，沒找到結果欄
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
        # A. 全文掃描 (含日期抓取)
        for i, page in enumerate(pdf.pages):
            text = page.extract_text()
            if text and len(text) > 50:
                is_scanned = False
                full_text_content += text + "\n"
                if i == 0: results["Date"] = find_date_in_first_page(text)

        if is_scanned: return None, filename
        results["PFAS"] = check_pfas_in_section(full_text_content)

        # B. 表格數據提取
        for page in pdf.pages:
            tables = page.extract_tables()
            if tables:
                for table in tables:
                    if not table or len(table) < 2: continue
                    
                    header_row_idx = -1
                    result_col_idx = -1
                    
                    # 1. 識別表頭 & 鎖定欄位 (Pb 重金屬用)
                    for r_idx, row in enumerate(table[:6]):
                        row_str = " ".join([str(c).upper() for c in row if c])
                        # 只要有 Item 且有 Unit/MDL/Limit 其中之一，就當作是檢測表
                        if ("ITEM" in row_str or "项目" in row_str) and \
                           ("UNIT" in row_str or "MDL" in row_str or "LIMIT" in row_str or "限值" in row_str or "RESULT" in row_str):
                            header_row_idx = r_idx
                            result_col_idx = get_column_score(row) # 使用消去法
                            break
                    
                    start_row = header_row_idx + 1 if header_row_idx != -1 else 0
                    
                    # 判斷這張表是否適合用於「黃金欄位鎖定」(是否有 Cd/Hg)
                    is_golden_table = False
                    if result_col_idx != -1:
                        is_golden_table = find_golden_column(table, result_col_idx)

                    # 2. 遍歷表格行 (執行混合邏輯)
                    for r_idx in range(start_row, len(table)):
                        row = table[r_idx]
                        if not row: continue
                        
                        item_name = clean_text(row[0])
                        if len(row) > 1: item_name += " " + clean_text(row[1])
                        item_upper = item_name.upper()

                        # =======================================================
                        # 策略 A (V7 邏輯): PBBs/PBDEs 全行掃描 (暴力法)
                        # =======================================================
                        for pbb_kw in PBBS_KEYWORDS + PBDES_KEYWORDS:
                            if re.search(pbb_kw, item_upper, re.IGNORECASE):
                                # 掃描這一行所有格子，忽略欄位索引
                                potential_vals = []
                                for cell in row:
                                    v_num, v_disp = extract_value_logic(clean_text(str(cell)))
                                    if v_num is not None:
                                        # [智慧過濾]: 排除 MDL/Limit (5, 10, 50, 100, 1000)
                                        if v_num in [5, 8, 10, 50, 100, 1000] and v_disp != "N.D.":
                                            continue
                                        potential_vals.append(v_num)
                                
                                if potential_vals:
                                    val = potential_vals[-1] # 取最後一個有效值
                                    if val > 0:
                                        cat = "PBBs" if any(k in pbb_kw for k in PBBS_KEYWORDS) else "PBDEs"
                                        results[cat]["sum_val"] += val
                                        results[cat]["val"] = 1

                        # =======================================================
                        # 策略 B (V10 邏輯): Pb/Cd/Hg/Cr6 黃金欄位鎖定
                        # =======================================================
                        is_heavy = any(x in item_upper for x in ["LEAD", "CADMIUM", "MERCURY", "HEXAVALENT", "PB", "CD", "HG", "CR(VI)"])
                        
                        if is_heavy and is_golden_table and result_col_idx != -1 and len(row) > result_col_idx:
                            val_text = clean_text(row[result_col_idx])
                            val_num, val_disp = extract_value_logic(val_text)
                            if val_num is not None:
                                update_results(results, item_name, val_num, val_disp, force_golden=True)
                            continue # 重金屬處理完畢，跳過

                        # =======================================================
                        # 策略 C (V13/V15): 其他單項 (PFOS, Cl...) - 依賴欄位但嚴格檢查
                        # =======================================================
                        if result_col_idx != -1 and len(row) > result_col_idx:
                            val_text = clean_text(row[result_col_idx])
                            
                            # 語義防火牆 (Cl)
                            if "CHLORINE" in item_upper and ("POLYVINYL" in item_upper or "PVC" in item_upper): continue

                            # 嚴格型別檢查 (Cl, PFOS, Br 拒絕 Negative)
                            is_strict = any(x in item_upper for x in ["CHLORINE", "BROMINE", "PFOS", "FLUORINE", "IODINE"])
                            val_num, val_disp = extract_value_logic(val_text, strict_numeric=is_strict)
                            
                            if val_num is not None:
                                # 再次過濾 Limit (1000) 以防萬一
                                if val_num in [1000] and val_disp != "N.D.": continue
                                update_results(results, item_name, val_num, val_disp)

            # C. 文字流模式 (Fallback) - 僅針對尚未抓到的項目
            # (省略部分程式碼以保持精簡，V15 主要依賴強大的表格邏輯)

    finalize_results(results)
    
    # 填充空值
    for k, v in results.items():
        if isinstance(v, dict) and "val" in v and v["val"] is None:
            v["display"] = ""
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

def update_results(results, item_name, val_num, val_disp, force_golden=False):
    item_upper = str(item_name).upper()
    
    for field_key, config in TARGET_FIELDS.items():
        for kw in config["keywords"]:
            if re.search(kw, item_upper, re.IGNORECASE):
                # 黃金欄位強制更新
                if force_golden and field_key in ["Lead", "Cadmium", "Mercury", "Hexavalent Chromium"]:
                    results[field_key]["val"] = val_num
                    results[field_key]["display"] = val_disp
                    return

                # 一般更新 (比大小，取最大值)
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

    with st.spinner('正在進行 V15 引擎分析 (混合架構 + 終極日期鎖定)...'):
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
        
        st.success(f"✅ 成功擷取 {len(all_data)} 份報告！(V15 核心)")
        st.dataframe(display_df, use_container_width=True)
        
        csv = display_df.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            label="📥 下載 Excel/CSV 報表",
            data=csv,
            file_name="rohs_report_v15_hybrid.csv",
            mime="text/csv",
        )

    if scanned_files:
        st.error("⚠️ 以下檔案為掃描圖片 (無法擷取文字)：")
        for f in scanned_files:
            st.write(f"- {f}")
else:
    st.info("請上傳 PDF 檔案以開始分析。")
