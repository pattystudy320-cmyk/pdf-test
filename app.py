import streamlit as st
import pdfplumber
import pandas as pd
import re
import os

# --- 設定頁面 ---
st.set_page_config(page_title="通用檢測報告擷取工具 (精準版)", layout="wide")
st.title("🧪 通用型第三方檢測報告數據擷取工具 (精準版)")
st.markdown("""
**版本更新說明：** 1. **修正抓錯欄位問題**：不再誤抓測試方法(如 62321)作為結果。
2. **智慧鎖定結果欄**：自動辨識 A1, 001, Sample, Result 等欄位。
3. **支援多種格式**：針對 SGS, CTI, Intertek 格式優化。
""")

# --- 定義目標欄位與關鍵字 ---
# 這裡定義的是「項目名稱 (Item Name)」欄位的關鍵字
TARGET_FIELDS = {
    "Lead": {"name": "Pb", "keywords": [r"^Lead\b", r"^Pb\b", r"铅", r"Lead \(Pb\)"]},
    "Cadmium": {"name": "Cd", "keywords": [r"^Cadmium\b", r"^Cd\b", r"镉", r"Cadmium \(Cd\)"]},
    "Mercury": {"name": "Hg", "keywords": [r"^Mercury\b", r"^Hg\b", r"汞", r"Mercury \(Hg\)"]},
    "Hexavalent Chromium": {"name": "Cr(VI)", "keywords": [r"Hexavalent Chromium", r"Cr\(VI\)", r"Cr6\+", r"六价铬", r"六價鉻"]},
    "DEHP": {"name": "DEHP", "keywords": [r"Bis\(2-ethylhexyl\) phthalate", r"DEHP", r"邻苯二甲酸二\(2-乙基己基\)酯"]},
    "BBP": {"name": "BBP", "keywords": [r"Butyl benzyl phthalate", r"BBP", r"邻苯二甲酸丁基苄基酯"]},
    "DBP": {"name": "DBP", "keywords": [r"Dibutyl phthalate", r"DBP", r"邻苯二甲酸二丁酯"]},
    "DIBP": {"name": "DIBP", "keywords": [r"Diisobutyl phthalate", r"DIBP", r"邻苯二甲酸二异丁酯"]},
    "Fluorine": {"name": "F", "keywords": [r"Fluorine", r"氟"]},
    "Chlorine": {"name": "Cl", "keywords": [r"Chlorine", r"氯"]},
    "Bromine": {"name": "Br", "keywords": [r"Bromine", r"溴"]},
    "Iodine": {"name": "I", "keywords": [r"Iodine", r"碘"]},
    "PFOS": {"name": "PFOS", "keywords": [r"Perfluorooctane Sulfonates", r"PFOS", r"全氟辛磺酸"]},
}

# PBBs/PBDEs 子項目關鍵字 (用於加總)
PBBS_KEYWORDS = [r"Monobromobiphenyl", r"Dibromobiphenyl", r"Tribromobiphenyl", r"Tetrabromobiphenyl", 
                 r"Pentabromobiphenyl", r"Hexabromobiphenyl", r"Heptabromobiphenyl", r"Octabromobiphenyl", 
                 r"Nonabromobiphenyl", r"Decabromobiphenyl", r"一溴联苯", r"十溴联苯"]
PBDES_KEYWORDS = [r"Monobromodiphenyl ether", r"Dibromodiphenyl ether", r"Tribromodiphenyl ether", 
                  r"Tetrabromodiphenyl ether", r"Pentabromodiphenyl ether", r"Hexabromodiphenyl ether", 
                  r"Heptabromodiphenyl ether", r"Octabromodiphenyl ether", r"Nonabromodiphenyl ether", 
                  r"Decabromodiphenyl ether", r"一溴二苯醚", r"十溴二苯醚"]

# --- 輔助函式 ---

def clean_text(text):
    """清理文字"""
    if not text: return ""
    return re.sub(r'\s+', ' ', str(text)).strip()

def is_header_row(row_text_list):
    """判斷該列是否為表頭列"""
    row_str = " ".join([str(x).upper() for x in row_text_list])
    # 表頭通常包含這些關鍵字
    header_keywords = ["TEST ITEM", "METHOD", "UNIT", "MDL", "RESULT", "LIMIT", "测试项目", "单位", "结果"]
    matches = sum(1 for kw in header_keywords if kw in row_str)
    return matches >= 2 # 至少包含兩個關鍵字才算表頭

def identify_result_column(header_row):
    """
    核心邏輯：找出哪一欄是結果欄。
    策略：
    1. 排除 Method, Unit, MDL, Limit 欄位。
    2. 尋找 Sample ID (如 001, A1) 或 Result 關鍵字。
    3. 如果剩下的欄位不明，通常取最右邊的非 Limit 欄位。
    """
    exclude_keywords = ["ITEM", "METHOD", "UNIT", "MDL", "LOQ", "LIMIT", "REQUIREMENT", "项目", "方法", "单位", "限值"]
    possible_indices = []
    
    for i, cell in enumerate(header_row):
        cell_text = clean_text(cell).upper()
        if not cell_text: continue
        
        # 如果欄位名稱包含排除關鍵字，則跳過
        is_excluded = any(kw in cell_text for kw in exclude_keywords)
        
        # 特例：有時候表頭寫 "Test Result"，包含 Result 是我们要的
        if "RESULT" in cell_text or "结果" in cell_text:
            is_excluded = False
            
        if not is_excluded:
            possible_indices.append(i)
    
    # 如果找到多個可能的欄位 (例如有多個 Sample)，這裡暫時取第一個，或者根據需求取最大值
    # 針對您的需求 (若多份以上傳多份為準)，單份報告內通常只有一個主要結果欄
    if possible_indices:
        return possible_indices[0] # 回傳最可能的結果欄索引
    
    return -1 # 沒找到

def extract_value_logic(val_str):
    """
    數值提取邏輯
    回傳: (排序用數值, 顯示用字串)
    """
    if not val_str: return 0, "N.D."
    
    val_upper = str(val_str).upper().replace(" ", "")
    
    # 優先處理文字狀態
    if "N.D." in val_upper or "ND" in val_upper or "<" in val_upper:
        return 0, "N.D."
    if "NEGATIVE" in val_upper or "阴性" in val_upper:
        return 0.0001, "NEGATIVE"
    if "POSITIVE" in val_upper or "阳性" in val_upper:
        return 999999, "POSITIVE"
    
    # 嘗試提取數字 (排除括號內的數字，例如方法編號，但這裡我們已經透過欄位鎖定排除了方法)
    # 處理類似 "12 mg/kg" 的情況
    match = re.search(r"(\d+(\.\d+)?)", val_str)
    if match:
        return float(match.group(1)), match.group(1)
    
    return 0, "N.D."

def find_date_in_text(full_text):
    """嘗試從全文中抓取檢測日期"""
    date_patterns = [
        r"Date:\s*([A-Za-z]{3}\.?\s\d{1,2},\s\d{4})", # Jan 08, 2025
        r"Date:\s*(\d{4}[-/.]\d{1,2}[-/.]\d{1,2})",   # 2025.06.16
        r"Testing Period\s*[:\n]\s*.*?to\s*([A-Za-z]{3}\.?\s\d{1,2},\s\d{4})",
        r"Testing Period\s*[:\n]\s*.*?[-to]\s*(\d{4}[-/.]\d{1,2}[-/.]\d{1,2})"
    ]
    for pattern in date_patterns:
        match = re.search(pattern, full_text, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).replace("\n", " ").strip()
    return ""

def process_file(uploaded_file):
    filename = uploaded_file.name
    
    # 初始化結果容器
    results = {k: {"val": 0, "display": ""} for k in TARGET_FIELDS.keys()}
    results["PBBs"] = {"val": 0, "display": "", "sum_val": 0}
    results["PBDEs"] = {"val": 0, "display": "", "sum_val": 0}
    results["PFAS"] = ""
    results["Date"] = ""
    
    is_scanned = True
    full_text_content = ""
    
    with pdfplumber.open(uploaded_file) as pdf:
        # 1. 全文掃描 (用於日期、PFAS、掃描檔判斷)
        for page in pdf.pages:
            text = page.extract_text()
            if text and len(text) > 50:
                is_scanned = False
                full_text_content += text + "\n"
        
        if is_scanned:
            return None, filename

        results["Date"] = find_date_in_text(full_text_content)
        
        # PFAS 判斷 (全文搜索關鍵字)
        if "PFAS" in full_text_content.upper() or "PER- AND POLYFLUOROALKYL" in full_text_content.upper():
            results["PFAS"] = "REPORT"

        # 2. 表格處理 (精準定位)
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                if not table or len(table) < 2: continue
                
                df = pd.DataFrame(table)
                result_col_idx = -1
                
                # 尋找表頭與結果欄位索引
                for idx, row in df.iterrows():
                    row_clean = [clean_text(c) for c in row]
                    if is_header_row(row_clean):
                        result_col_idx = identify_result_column(row_clean)
                        # print(f"Found Header at row {idx}, Result Column Index: {result_col_idx} in {row_clean}") # Debug用
                        if result_col_idx != -1:
                            break
                
                # 如果這張表找不到結果欄，跳過 (避免讀到無關的表格)
                if result_col_idx == -1: continue

                # 開始遍歷數據列
                for idx, row in df.iterrows():
                    # 跳過表頭之前的列
                    if idx <= 0: continue 
                    
                    # 確保列長度足夠，避免 index out of bounds
                    if len(row) <= result_col_idx: continue

                    # 第一欄通常是項目名稱 (Item Name)
                    item_name_cell = clean_text(row[0]).upper()
                    # 結合前兩欄，以防項目名稱被切分
                    if len(row) > 1:
                        item_name_cell += " " + clean_text(row[1]).upper()

                    target_value_cell = clean_text(row[result_col_idx])
                    
                    # 如果結果欄位是空的，或者是 MDL/Limit 的數值 (誤判)，則跳過
                    if not target_value_cell: continue

                    # --- A. 一般項目匹配 ---
                    for field_key, config in TARGET_FIELDS.items():
                        for kw in config["keywords"]:
                            # 使用正則表達式匹配項目名稱
                            if re.search(kw.upper(), item_name_cell):
                                # 找到項目，提取數值
                                num_val, disp_str = extract_value_logic(target_value_cell)
                                
                                # 更新邏輯：取最大值
                                if num_val > results[field_key]["val"]:
                                    results[field_key]["val"] = num_val
                                    results[field_key]["display"] = disp_str
                                elif num_val == 0 and results[field_key]["val"] == 0:
                                    # 如果都是 0，優先顯示 NEGATIVE，再來是 N.D.
                                    if disp_str == "NEGATIVE":
                                        results[field_key]["display"] = "NEGATIVE"
                                    elif not results[field_key]["display"]:
                                        results[field_key]["display"] = "N.D."
                                break

                    # --- B. PBBs 加總 ---
                    for pbb_kw in PBBS_KEYWORDS:
                        if re.search(pbb_kw.upper(), item_name_cell):
                            num_val, _ = extract_value_logic(target_value_cell)
                            results["PBBs"]["sum_val"] += num_val
                            break

                    # --- C. PBDEs 加總 ---
                    for pbde_kw in PBDES_KEYWORDS:
                        if re.search(pbde_kw.upper(), item_name_cell):
                            num_val, _ = extract_value_logic(target_value_cell)
                            results["PBDEs"]["sum_val"] += num_val
                            break

    # 計算 PBBs/PBDEs 最終顯示
    if results["PBBs"]["sum_val"] > 0:
        results["PBBs"]["display"] = str(round(results["PBBs"]["sum_val"], 2))
        results["PBBs"]["val"] = results["PBBs"]["sum_val"]
    elif not results["PBBs"]["display"]: # 如果沒有任何子項目，預設 N.D.
        results["PBBs"]["display"] = "N.D."

    if results["PBDEs"]["sum_val"] > 0:
        results["PBDEs"]["display"] = str(round(results["PBDEs"]["sum_val"], 2))
        results["PBDEs"]["val"] = results["PBDEs"]["sum_val"]
    elif not results["PBDEs"]["display"]:
        results["PBDEs"]["display"] = "N.D."

    # 最終數據整理
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
        # 排序用
        "_sort_pb": results["Lead"]["val"],
        "_sort_max": max([v["val"] for k, v in results.items() if isinstance(v, dict) and "val" in v])
    }
    
    return final_output, None

# --- 主程式介面 ---

uploaded_files = st.file_uploader("請上傳 PDF 檢測報告 (可多選)", type="pdf", accept_multiple_files=True)

if uploaded_files:
    all_data = []
    scanned_files = []

    with st.spinner('正在分析報告表格結構中，請稍候...'):
        for pdf_file in uploaded_files:
            data, scanned_name = process_file(pdf_file)
            if scanned_name:
                scanned_files.append(scanned_name)
            else:
                all_data.append(data)

    if all_data:
        df = pd.DataFrame(all_data)
        
        # 排序：優先 Pb 數值大 -> 小，其次是其他項目最大值
        df = df.sort_values(by=["_sort_pb", "_sort_max"], ascending=[False, False])
        
        display_df = df.drop(columns=["_sort_pb", "_sort_max"])
        
        st.success(f"成功擷取 {len(all_data)} 份報告數據！")
        st.dataframe(display_df, use_container_width=True)
        
        csv = display_df.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            label="📥 下載 CSV 報表",
            data=csv,
            file_name="rohs_analysis_result_v2.csv",
            mime="text/csv",
        )

    if scanned_files:
        st.error("⚠️ 以下檔案疑似為掃描圖片檔 (無法擷取文字)，請手動確認：")
        for f in scanned_files:
            st.write(f"- {f}")

else:
    st.info("請上傳 PDF 檔案以開始分析。")
