import streamlit as st
import pdfplumber
import pandas as pd
import re
from dateutil import parser
import io

st.set_page_config(page_title="SGS Report Parser", layout="wide")
st.title("📄 SGS Report 檢測結果彙總工具 (表格定位版)")

# =========================
# 1. 欄位關鍵字定義
# =========================
# 這裡的關鍵字用來匹配「表格第一欄」的內容
ITEM_KEYWORDS = {
    "Pb": ["Lead", "Pb"],
    "Cd": ["Cadmium", "Cd"],
    "Hg": ["Mercury", "Hg"],
    "CrVI": ["Hexavalent", "Chromium", "CrVI"],
    "PBBs": ["Sum of PBBs", "PBBs"],
    "PBDEs": ["Sum of PBDEs", "PBDEs"],
    "DEHP": ["DEHP", "Di(2-ethylhexyl) phthalate"],
    "BBP": ["BBP", "Benzyl butyl phthalate"],
    "DBP": ["DBP", "Dibutyl phthalate"],
    "DIBP": ["DIBP", "Diisobutyl phthalate"],
    "F": ["Fluorine", "F"],
    "CL": ["Chlorine", "Cl"],
    "BR": ["Bromine", "Br"],
    "I": ["Iodine", "I"],
    "PFOS": ["PFOS"]
}

FINAL_COLUMNS = [
    "Pb", "Cd", "Hg", "CrVI", "PBBs", "PBDEs",
    "DEHP", "BBP", "DBP", "DIBP",
    "F", "CL", "BR", "I",
    "PFOS", "PFAS", "DATE"
]

# =========================
# 2. 核心功能：表格讀取
# =========================

def normalize_result(value):
    """
    清洗抓到的結果：
    1. 統一 N.D. 格式
    2. 移除單位或雜訊
    """
    if not value:
        return ""
    
    val_str = str(value).strip()
    
    # 判斷 N.D. (包含 ND, N. D., Not Detected)
    if re.search(r"(\bN\s*\.?\s*D\s*\.?\b)|(Not\s*Detected)", val_str, re.IGNORECASE):
        return "N.D."
    
    if "NEGATIVE" in val_str.upper():
        return "NEGATIVE"

    # 嘗試抓取數字
    # 先移除單位
    val_str = re.sub(r"mg/kg|ppm|%|wt%", "", val_str, flags=re.IGNORECASE)
    match = re.search(r"\d+(\.\d+)?", val_str)
    if match:
        return match.group(0)
    
    return ""

def extract_data_from_pdf(pdf_file):
    """
    混合策略：
    1. 優先嘗試 extract_tables (表格模式) -> 準確度最高，不會抓到 MDL
    2. 若表格失敗，可回退到文字搜尋 (這裡簡化，專注於表格)
    """
    extracted_data = {key: [] for key in ITEM_KEYWORDS} # 儲存所有抓到的數據
    full_text = ""
    pages_text = []

    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            # 1. 收集全文 (用於抓日期和 PFAS)
            text = page.extract_text() or ""
            pages_text.append(text)
            full_text += text + "\n"

            # 2. 表格萃取 (Table Extraction)
            # 使用寬鬆設定，避免無框線表格讀不到
            tables = page.extract_tables(table_settings={"vertical_strategy": "text", "horizontal_strategy": "text"})

            for table in tables:
                for row in table:
                    # 過濾掉空行或欄位過少的行
                    # 馬來西亞報告通常有 4~6 欄 (Item, Unit, Method, Result, MDL, Limit)
                    # Result 通常在 Index 3 (第4欄)
                    if not row or len(row) < 4:
                        continue
                    
                    # 清理 row 中的 None
                    row_clean = [str(cell).strip() if cell else "" for cell in row]
                    
                    first_col = row_clean[0] # 測項名稱
                    target_col_idx = 3       # 結果欄位通常在第 4 欄 (Index 3)

                    # 檢查這一行是否是我們要的測項
                    for item, keywords in ITEM_KEYWORDS.items():
                        # 規則：關鍵字必須出現在第一欄
                        # 例如 keywords=["Lead", "Pb"]，只要第一欄包含 "Lead" 就算對應到 "Pb"
                        if all(k.lower() in first_col.lower() for k in keywords if len(k) > 1):
                            # 特別處理: 單一字母 F, I 容易誤判，需精確比對
                            if item in ["F", "I"] and len(first_col) < 20: 
                                if item not in first_col: 
                                    continue
                            
                            # 抓取結果欄位
                            raw_result = row_clean[target_col_idx]
                            
                            # 如果第 4 欄是空的，有可能是 N.D. 寫在第 3 欄 (很少見)，或是合併儲存格
                            # 但馬來西亞報告很標準，通常就在 Index 3
                            clean_res = normalize_result(raw_result)
                            if clean_res:
                                extracted_data[item].append(clean_res)

    return full_text, pages_text, extracted_data

def extract_pfas(text):
    return "REPORT" if re.search(r"\bPFAS\b", text, re.IGNORECASE) else ""

def extract_date(first_page_text):
    match = re.search(
        r"(REPORTED DATE|TEST REPORT REPORTED DATE)\s*[:\-]?\s*([^\n]+)",
        first_page_text,
        re.IGNORECASE
    )
    return match.group(2).strip() if match else ""

def normalize_date(date_text):
    if not date_text:
        return ""
    try:
        dt = parser.parse(date_text, dayfirst=True)
        return dt.strftime("%Y/%m/%d")
    except:
        return ""

def merge_results(values):
    """
    彙總邏輯
    """
    nums = []
    has_nd = False
    has_neg = False

    for v in values:
        if not v: continue
        v_upper = str(v).upper()
        
        if "N.D." in v_upper:
            has_nd = True
        elif "NEGATIVE" in v_upper:
            has_neg = True
        else:
            try:
                nums.append(float(v))
            except:
                pass

    if nums:
        return str(max(nums))
    if has_neg:
        return "NEGATIVE"
    if has_nd:
        return "N.D."
    return ""

# =========================
# 3. Streamlit 主程式
# =========================

uploaded_files = st.file_uploader(
    "請上傳 SGS PDF Report（建議馬來西亞版本）",
    type="pdf",
    accept_multiple_files=True
)

if uploaded_files:
    rows = []
    for file in uploaded_files:
        full_text, pages_text, extracted_data = extract_data_from_pdf(file)
        
        record = {}

        # 填入抓到的表格數據
        for item in ITEM_KEYWORDS:
            # 如果有抓到多筆 (例如 PBBs 細項)，進行 merge
            values = extracted_data.get(item, [])
            record[item] = merge_results(values)

        # 填入日期與 PFAS
        record["PFAS"] = extract_pfas(full_text)
        raw_date = extract_date(pages_text[0]) if pages_text else ""
        record["DATE"] = normalize_date(raw_date)

        rows.append(record)

    df_all = pd.DataFrame(rows)

    # 彙總顯示
    merged = {}
    if not df_all.empty:
        for col in FINAL_COLUMNS:
            if col in ["DATE", "PFAS"]:
                continue
            if col in df_all.columns:
                merged[col] = merge_results(df_all[col].tolist())
            else:
                merged[col] = ""

        merged["PFAS"] = "REPORT" if "REPORT" in df_all["PFAS"].tolist() else ""
        
        valid_dates = [d for d in df_all["DATE"] if d]
        merged["DATE"] = max(valid_dates) if valid_dates else ""

        df_final = pd.DataFrame([merged], columns=FINAL_COLUMNS)

        st.subheader("📊 彙總結果（表格定位版）")
        st.dataframe(df_final, use_container_width=True)

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df_final.to_excel(writer, sheet_name="SGS_Result", index=False)

        st.download_button(
            "⬇️ 下載公司制式 Excel",
            output.getvalue(),
            file_name="SGS_Test_Result.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    else:
        st.warning("未讀取到有效資料。")
