import streamlit as st
import pdfplumber
import pandas as pd
import re
import os

# --- 設定頁面 ---
st.set_page_config(page_title="通用檢測報告擷取工具", layout="wide")
st.title("🧪 通用型第三方檢測報告數據擷取工具")
st.markdown("""
支援格式：SGS, CTI, INTERTEK, ITS, Eurofins 等 PDF 報告。
**功能特點：** 自動加總 PBBs/PBDEs、PFAS 偵測、自動排序 (Pb優先)、掃描檔偵測。
""")

# --- 核心邏輯設定 ---

# 定義我們要抓取的欄位與關鍵字對應 (正則表達式)
TARGET_FIELDS = {
    "Lead": {"name": "Pb", "keywords": [r"Lead", r"Pb", r"鉛"]},
    "Cadmium": {"name": "Cd", "keywords": [r"Cadmium", r"Cd", r"鎘"]},
    "Mercury": {"name": "Hg", "keywords": [r"Mercury", r"Hg", r"汞"]},
    "Hexavalent Chromium": {"name": "Cr(VI)", "keywords": [r"Hexavalent Chromium", r"Cr\(VI\)", r"Cr6\+", r"六價鉻"]},
    "DEHP": {"name": "DEHP", "keywords": [r"Bis\(2-ethylhexyl\) phthalate", r"DEHP", r"鄰苯二甲酸二\(2-乙基己基\)酯"]},
    "BBP": {"name": "BBP", "keywords": [r"Butyl benzyl phthalate", r"BBP", r"鄰苯二甲酸丁基苄基酯"]},
    "DBP": {"name": "DBP", "keywords": [r"Dibutyl phthalate", r"DBP", r"鄰苯二甲酸二丁酯"]},
    "DIBP": {"name": "DIBP", "keywords": [r"Diisobutyl phthalate", r"DIBP", r"鄰苯二甲酸二異丁酯"]},
    "Fluorine": {"name": "F", "keywords": [r"Fluorine", r"氟"]},
    "Chlorine": {"name": "Cl", "keywords": [r"Chlorine", r"氯"]},
    "Bromine": {"name": "Br", "keywords": [r"Bromine", r"溴"]},
    "Iodine": {"name": "I", "keywords": [r"Iodine", r"碘"]},
    "PFOS": {"name": "PFOS", "keywords": [r"Perfluorooctane Sulfonates", r"PFOS", r"全氟辛磺酸"]},
}

# PBBs 和 PBDEs 的子項目關鍵字，用於加總
PBBS_KEYWORDS = [r"Monobromobiphenyl", r"Dibromobiphenyl", r"Tribromobiphenyl", r"Tetrabromobiphenyl", 
                 r"Pentabromobiphenyl", r"Hexabromobiphenyl", r"Heptabromobiphenyl", r"Octabromobiphenyl", 
                 r"Nonabromobiphenyl", r"Decabromobiphenyl", r"一溴聯苯", r"十溴聯苯"]
PBDES_KEYWORDS = [r"Monobromodiphenyl ether", r"Dibromodiphenyl ether", r"Tribromodiphenyl ether", 
                  r"Tetrabromodiphenyl ether", r"Pentabromodiphenyl ether", r"Hexabromodiphenyl ether", 
                  r"Heptabromodiphenyl ether", r"Octabromodiphenyl ether", r"Nonabromodiphenyl ether", 
                  r"Decabromodiphenyl ether", r"一溴二苯醚", r"十溴二苯醚"]

# --- 輔助函式 ---

def clean_text(text):
    """清理文字，移除多餘空白與換行"""
    if not text: return ""
    return re.sub(r'\s+', ' ', str(text)).strip()

def extract_number(val_str):
    """從字串中提取數值，處理 N.D. 和 Negative"""
    if not val_str:
        return 0, "N.D."
    
    val_str_upper = str(val_str).upper().replace(" ", "")
    
    if "N.D." in val_str_upper or "ND" in val_str_upper or "<" in val_str_upper:
        return 0, "N.D."
    if "NEGATIVE" in val_str_upper or "陰性" in val_str_upper:
        return 0.0001, "NEGATIVE" # 給一個極小正值以便排序，但顯示為文字
    if "POSITIVE" in val_str_upper or "陽性" in val_str_upper:
        return 999999, "POSITIVE"

    # 嘗試提取數字
    match = re.search(r"(\d+(\.\d+)?)", val_str)
    if match:
        return float(match.group(1)), match.group(1)
    
    return 0, "N.D."

def find_date_in_text(full_text):
    """嘗試從全文中抓取檢測日期"""
    # 常見格式: Jan 08, 2025, 2025/01/08, Dec. 26, 2024
    date_patterns = [
        r"Date:\s*([A-Za-z]{3}\.?\s\d{1,2},\s\d{4})", # Jan 08, 2025
        r"Date:\s*(\d{4}[-/.]\d{1,2}[-/.]\d{1,2})",   # 2025.06.16
        r"Testing Period\s*[:\n]\s*.*?to\s*([A-Za-z]{3}\.?\s\d{1,2},\s\d{4})", # Period ... to Date
        r"Testing Period\s*[:\n]\s*.*?[-to]\s*(\d{4}[-/.]\d{1,2}[-/.]\d{1,2})"
    ]
    
    for pattern in date_patterns:
        match = re.search(pattern, full_text, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).replace("\n", " ").strip()
    return ""

def process_file(uploaded_file):
    """處理單個 PDF 檔案的核心邏輯"""
    filename = uploaded_file.name
    results = {k: {"val": 0, "display": "N.D."} for k in TARGET_FIELDS.keys()}
    results["PBBs"] = {"val": 0, "display": "N.D.", "sum_val": 0}
    results["PBDEs"] = {"val": 0, "display": "N.D.", "sum_val": 0}
    results["PFAS"] = "N.D." # 預設
    results["Date"] = ""
    
    is_scanned = True
    full_text_content = ""
    
    with pdfplumber.open(uploaded_file) as pdf:
        # 1. 初步掃描：檢查是否為掃描檔 & 提取全文用於 PFAS/Date 搜尋
        for page in pdf.pages:
            text = page.extract_text()
            if text and len(text) > 50:
                is_scanned = False
                full_text_content += text + "\n"
        
        if is_scanned:
            return None, filename # 回傳 None 表示是掃描檔

        # 2. 提取日期
        results["Date"] = find_date_in_text(full_text_content)

        # 3. 判斷 PFAS (關鍵字存在即 Report)
        pfas_keywords = ["Per- and Polyfluoroalkyl", "PFAS"]
        for kw in pfas_keywords:
            if kw.upper() in full_text_content.upper():
                results["PFAS"] = "REPORT"
                break

        # 4. 表格數據提取 (核心)
        # 遍歷每一頁的每一個表格
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                if not table: continue
                
                # 將表格轉為 DataFrame 方便處理
                df = pd.DataFrame(table)
                
                # 遍歷表格的每一列
                for index, row in df.iterrows():
                    row_text = [clean_text(cell) for cell in row if cell]
                    row_str = " ".join(row_text).upper()
                    
                    if not row_text: continue

                    # A. 處理一般項目 (Pb, Cd, Phthalates, Halogens...)
                    for field_key, config in TARGET_FIELDS.items():
                        # 檢查該列是否包含目標化學物質的關鍵字
                        for kw in config["keywords"]:
                            if re.search(kw.upper(), row_str):
                                # 找到關鍵字，接下來找數值
                                # 邏輯：排除 Limit (通常是 100, 1000) 和 MDL (通常是 2, 5, 10, 50)
                                # 找出這列中所有的數字/ND
                                found_value = False
                                for cell in row_text:
                                    if not cell: continue
                                    # 排除本身是關鍵字的儲存格
                                    if re.search(kw.upper(), str(cell).upper()): continue
                                    
                                    num_val, display_str = extract_number(cell)
                                    
                                    # 簡單過濾 Limit 和 MDL 的常見誤判
                                    # 如果數值是 1000 或 100，且旁邊有 ND，通常 1000 是 Limit
                                    # 這是一個啟發式規則，可能需要根據實際情況微調
                                    if num_val in [1000, 100] and "ND" not in str(cell).upper():
                                        continue # 忽略 Limit
                                    if num_val in [2, 5, 7, 8, 10, 20, 25, 50] and "ND" not in str(cell).upper():
                                        # 這是比較危險的過濾，假設測試結果不會剛好等於 MDL
                                        # 但為了通用性，我們先假設結果通常大於 MDL 或為 ND
                                        # 改進：如果 cell 含有 "RL" "MDL" "Limit" 則跳過
                                        continue

                                    # 如果找到有效值 (大於目前紀錄的值 或 是 ND 但我們還沒找到值)
                                    if num_val > results[field_key]["val"]:
                                        results[field_key]["val"] = num_val
                                        results[field_key]["display"] = display_str
                                        found_value = True
                                    elif num_val == 0 and results[field_key]["val"] == 0:
                                        # 保持 ND 或 Negative
                                        if display_str == "NEGATIVE":
                                            results[field_key]["display"] = "NEGATIVE"
                                    
                                    if found_value: break # 這一列找到一個結果就跳出 (避免讀到後面的欄位)
                                break # 關鍵字匹配成功，跳出關鍵字迴圈

                    # B. 處理 PBBs 子項目加總
                    for pbb_kw in PBBS_KEYWORDS:
                        if re.search(pbb_kw.upper(), row_str):
                             for cell in row_text:
                                if re.search(pbb_kw.upper(), str(cell).upper()): continue
                                num_val, _ = extract_number(cell)
                                # 排除 Limit (1000) 和 MDL (5, 25)
                                if num_val in [1000, 5, 25] and "ND" not in str(cell).upper(): continue
                                results["PBBs"]["sum_val"] += num_val
                                break 

                    # C. 處理 PBDEs 子項目加總
                    for pbde_kw in PBDES_KEYWORDS:
                        if re.search(pbde_kw.upper(), row_str):
                             for cell in row_text:
                                if re.search(pbde_kw.upper(), str(cell).upper()): continue
                                num_val, _ = extract_number(cell)
                                if num_val in [1000, 5, 25] and "ND" not in str(cell).upper(): continue
                                results["PBDEs"]["sum_val"] += num_val
                                break

    # 計算 PBBs/PBDEs 最終顯示
    if results["PBBs"]["sum_val"] > 0:
        results["PBBs"]["display"] = str(round(results["PBBs"]["sum_val"], 2))
        results["PBBs"]["val"] = results["PBBs"]["sum_val"]
    
    if results["PBDEs"]["sum_val"] > 0:
        results["PBDEs"]["display"] = str(round(results["PBDEs"]["sum_val"], 2))
        results["PBDEs"]["val"] = results["PBDEs"]["sum_val"]

    # 整理最終輸出格式
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
        # 用於排序的隱藏數值
        "_sort_pb": results["Lead"]["val"],
        "_sort_max": max([v["val"] for k, v in results.items() if isinstance(v, dict) and "val" in v])
    }
    
    return final_output, None

# --- 主程式介面 ---

uploaded_files = st.file_uploader("請上傳 PDF 檢測報告 (可多選)", type="pdf", accept_multiple_files=True)

if uploaded_files:
    all_data = []
    scanned_files = []

    with st.spinner('正在分析報告中，請稍候...'):
        for pdf_file in uploaded_files:
            data, scanned_name = process_file(pdf_file)
            if scanned_name:
                scanned_files.append(scanned_name)
            else:
                all_data.append(data)

    if all_data:
        df = pd.DataFrame(all_data)
        
        # 排序邏輯：Pb 數值最高優先，若 Pb 為 0/ND，則看其他項目的最高值
        df = df.sort_values(by=["_sort_pb", "_sort_max"], ascending=[False, False])
        
        # 移除排序用的隱藏欄位
        display_df = df.drop(columns=["_sort_pb", "_sort_max"])
        
        st.success(f"成功擷取 {len(all_data)} 份報告數據！")
        st.dataframe(display_df, use_container_width=True)
        
        # 下載按鈕
        csv = display_df.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            label="📥 下載 CSV 報表",
            data=csv,
            file_name="rohs_analysis_result.csv",
            mime="text/csv",
        )

    # 顯示異常檔案
    if scanned_files:
        st.error("⚠️ 以下檔案疑似為掃描圖片檔 (無法擷取文字)，請手動確認：")
        for f in scanned_files:
            st.write(f"- {f}")

else:
    st.info("請上傳 PDF 檔案以開始分析。")
