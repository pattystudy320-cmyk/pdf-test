import streamlit as st
import pdfplumber
import pandas as pd
import re
from dateutil import parser
import io

st.set_page_config(page_title="SGS Report Parser", layout="wide")
st.title("📄 SGS Report 檢測結果彙總工具 (修正版)")

# =========================
# 欄位定義（順序固定）
# =========================
ITEM_RULES = {
    "Pb": r"Lead\s*\(Pb\)",
    "Cd": r"Cadmium\s*\(Cd\)",
    "Hg": r"Mercury\s*\(Hg\)",
    "CrVI": r"Hexavalent Chromium",
    "PBBs": r"Sum of PBBs",
    "PBDEs": r"Sum of PBDEs",
    "DEHP": r"DEHP",
    "BBP": r"BBP",
    "DBP": r"DBP",
    "DIBP": r"DIBP",
    "F": r"Fluorine",
    "CL": r"Chlorine",
    "BR": r"Bromine",
    "I": r"Iodine",
    "PFOS": r"PFOS"
}

FINAL_COLUMNS = [
    "Pb","Cd","Hg","CrVI","PBBs","PBDEs",
    "DEHP","BBP","DBP","DIBP",
    "F","CL","BR","I",
    "PFOS","PFAS","DATE"
]

# =========================
# 工具函式
# =========================
def extract_text_and_pages(pdf_file):
    full_text = ""
    pages_text = []
    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            pages_text.append(text)
            full_text += text + "\n"
    return full_text, pages_text


def extract_result(text, keyword):
    """
    修正後的邏輯：
    1. 先抓取關鍵字附近的文字。
    2. 強力清除 CAS No (避免抓到 117-81-7 的 117)。
    3. 強力清除 IEC 標準與年份 (避免抓到 2017)。
    4. 優先判斷 N.D. (避免抓到後面的 MDL 數值如 2, 5, 50)。
    5. 最後才抓取數值。
    """
    lines = text.splitlines()

    for i, line in enumerate(lines):
        # 找到關鍵字（例如 Lead, Cadmium）
        if re.search(keyword, line, re.IGNORECASE):
            # 抓取上下文（當行 + 後兩行）合併處理，處理跨行問題
            context = " ".join(lines[i:i+3])

            # --- 步驟 1: 清除干擾雜訊 (順序很重要) ---
            
            # (A) 清除 CAS No. (例如 "(CAS No. 117-81-7)"，一定要在找數字前刪掉)
            context = re.sub(r"\(?CAS\s*No\.?\s*[\d-]+\)?", " ", context, flags=re.IGNORECASE)

            # (B) 清除 IEC 標準編號與年份 (例如 "IEC 62321-4:2013+A1:2017")
            # 這會把 2017 這種年份清掉，避免汞 (Hg) 抓錯
            context = re.sub(r"IEC\s*62321[-\d:+A]*", " ", context, flags=re.IGNORECASE)

            # (C) 清除 Limit 限制值 (例如 "Max 1000")
            context = re.sub(r"Max\s*\d+", " ", context, flags=re.IGNORECASE)

            # --- 步驟 2: 優先判斷結果狀態 ---

            # 先找 N.D. (Not Detected)
            # 只要看到 N.D. 就直接回傳，這樣就不會去抓後面的 MDL (例如 2, 5, 50)
            if re.search(r"\bN\.?D\.?\b", context, re.IGNORECASE):
                return "N.D."

            # 再找 NEGATIVE
            if re.search(r"NEGATIVE", context, re.IGNORECASE):
                return "NEGATIVE"

            # --- 步驟 3: 最後才抓數字 ---
            
            # 尋找剩下的數字 (包含小數點)
            num = re.search(r"\b(\d+(\.\d+)?)\b", context)
            if num:
                value_str = num.group(1)
                
                # 防呆機制：過濾掉像年份的整數 (例如 2024, 2025)
                # 如果抓到的數字是整數，且在 1990-2030 之間，很可能是漏網之魚的年份
                try:
                    val_float = float(value_str)
                    if 1990 <= val_float <= 2030 and val_float.is_integer():
                        continue # 跳過這個數字，可能是年份
                except:
                    pass
                
                return value_str

    return ""


def extract_pfas(text):
    # 只要內文出現 PFAS 關鍵字，就視為有測
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
        # 嘗試解析日期格式
        dt = parser.parse(date_text, dayfirst=True)
        return dt.strftime("%Y/%m/%d")
    except:
        return ""


def merge_results(values):
    """
    彙總邏輯：
    1. 如果有多個數值，取最大值。
    2. 如果有 N.D. 或 NEGATIVE，優先級低於數值。
    """
    nums = []
    has_nd = False
    has_neg = False

    for v in values:
        if not v:
            continue
        
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
        return str(max(nums)) # 有數值回傳最大值
    if has_neg:
        return "NEGATIVE"
    if has_nd:
        return "N.D."
    
    return ""


# =========================
# 主流程 (UI)
# =========================
uploaded_files = st.file_uploader(
    "請上傳 SGS PDF Report（可一次多選）",
    type="pdf",
    accept_multiple_files=True
)

if uploaded_files:
    rows = []

    for file in uploaded_files:
        # 讀取 PDF
        full_text, pages_text = extract_text_and_pages(file)

        record = {}

        # 逐一抓取各檢測項目
        for item, keyword in ITEM_RULES.items():
            record[item] = extract_result(full_text, keyword)

        # PFAS 特別處理（是否有測）
        record["PFAS"] = extract_pfas(full_text)

        # DATE（只看第一頁）
        raw_date = extract_date(pages_text[0]) if pages_text else ""
        record["DATE"] = normalize_date(raw_date)

        rows.append(record)

    df_all = pd.DataFrame(rows)

    # ===== 同批 PDF 彙總（最嚴格結果）=====
    merged = {}
    if not df_all.empty:
        for col in FINAL_COLUMNS:
            if col in ["DATE", "PFAS"]:
                continue
            # 確保欄位存在，避免報錯
            if col in df_all.columns:
                merged[col] = merge_results(df_all[col].tolist())
            else:
                merged[col] = ""

        # PFAS: 只要有一份是 REPORT，結果就是 REPORT
        merged["PFAS"] = "REPORT" if "REPORT" in df_all["PFAS"].tolist() else ""
        
        # DATE: 取日期最大值 (最新的日期)
        valid_dates = [d for d in df_all["DATE"] if d]
        merged["DATE"] = max(valid_dates) if valid_dates else ""

        df_final = pd.DataFrame([merged], columns=FINAL_COLUMNS)

        # ===== 顯示結果 =====
        st.subheader("📊 彙總結果（同批 SGS Report）")
        st.dataframe(df_final, use_container_width=True)

        # ===== Excel 匯出 =====
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
        st.warning("未偵測到任何資料，請檢查 PDF 內容。")
