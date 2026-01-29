import streamlit as st
import pdfplumber
import pandas as pd
import re
from dateutil import parser
import io

st.set_page_config(page_title="SGS Report Parser", layout="wide")
st.title("📄 SGS Report 檢測結果彙總工具 (馬來西亞修正版)")

# =========================
# [cite_start]欄位定義（順序固定） [cite: 1, 2]
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
    [cite_start]return full_text, pages_text [cite: 3]


def extract_result(text, keyword):
    """
    修正版 V4 (包含除噪與優先判定 N.D. 邏輯)：
    1. 強力清洗：在讀取數據前，強制刪除 Max, MDL, CAS, Year 等干擾項。
    2. N.D. 優先：只要偵測到 N.D. 變體，直接回傳標準 "N.D."，不看後續數字。
    3. 抓取數值：只在沒有 N.D. 時才抓取剩餘的第一個數字。
    """
    lines = text.splitlines()

    for i, line in enumerate(lines):
        # 步驟 A: 鎖定關鍵字所在的行
        if re.search(keyword, line, re.IGNORECASE):
            # 抓取上下文 (當行 + 下一行)，縮小範圍避免抓到隔壁欄位
            context = " ".join(lines[i:i+2])

            # ==========================================
            # 步驟 B: 手術室 - 強力切除干擾源 (順序很重要)
            # ==========================================
            
            # 1. 切除單位 (mg/kg, ppm, %, wt%)
            context = re.sub(r"mg/kg|ppm|%|wt%", " ", context, flags=re.IGNORECASE)

            # 2. 切除 CAS No. (例如 "(CAS No. 84-74-2)" -> 刪除整個括號內容)
            # 避免抓到 84 或 117
            context = re.sub(r"\(?CAS\s*No\.?[\s\d-]+\)?", " ", context, flags=re.IGNORECASE)

            # 3. 切除 標準編號與年份 (例如 "IEC 62321-5:2013")
            # 這一步非常關鍵，避免抓到 2013, 2017
            context = re.sub(r"IEC\s*62321[-\d:+A]*", " ", context, flags=re.IGNORECASE)
            # 額外清除獨立的年份 (1990-2030)
            context = re.sub(r"\b(19|20)\d{2}\b", " ", context)

            # 4. 切除 Limit / MDL 標籤與數值 (例如 "Max 1000", "MDL 2")
            # 避免抓到 1000 或 2 (支援小數點)
            context = re.sub(r"(Max|Limit|MDL|LOQ)\s*\d+(\.\d+)?", " ", context, flags=re.IGNORECASE)

            # ==========================================
            # 步驟 C: 判斷結果 - N.D. 優先
            # ==========================================

            # 規則：詞界(\b) + N + 任意點或空 + D + 詞界 OR Not Detected
            # 這可以抓到: "N.D.", "ND", "N. D.", "Not Detected"
            nd_pattern = r"(\bN\s*\.?\s*D\s*\.?\b)|(Not\s*Detected)"
            
            if re.search(nd_pattern, context, re.IGNORECASE):
                # 您的需求：不管原文寫什麼，統一回傳 "N.D."
                return "N.D."

            # 判斷 NEGATIVE
            if re.search(r"NEGATIVE", context, re.IGNORECASE):
                return "NEGATIVE"

            # ==========================================
            # 步驟 D: 抓取數值
            # ==========================================
            
            # 因為上面已經把 Max, MDL, Year 都刪了，
            # 這裡抓到的第一個數字，極大機率就是真正的檢測結果
            nums = re.findall(r"\b\d+(\.\d+)?\b", context)
            
            if nums:
                # nums 回傳 list of tuples，取第一個匹配到的數字字串
                found_value = nums[0][0] 
                
                # 最後一道防線：雖然前面已經刪了年份，但以防萬一再擋一次
                try:
                    val_float = float(found_value)
                    # 如果抓到 2025 這種整數，且看起來像年份，就忽略
                    if 1990 <= val_float <= 2030 and val_float.is_integer():
                        continue 
                    return found_value
                except:
                    pass

    return ""


def extract_pfas(text):
    [cite_start]return "REPORT" if re.search(r"\bPFAS\b", text, re.IGNORECASE) else "" [cite: 6]


def extract_date(first_page_text):
    match = re.search(
        r"(REPORTED DATE|TEST REPORT REPORTED DATE)\s*[:\-]?\s*([^\n]+)",
        first_page_text,
        re.IGNORECASE
    )
    [cite_start]return match.group(2).strip() if match else "" [cite: 6]


def normalize_date(date_text):
    if not date_text:
        return ""
    try:
        dt = parser.parse(date_text, dayfirst=True)
        return dt.strftime("%Y/%m/%d")
    except:
        [cite_start]return "" [cite: 7]


def merge_results(values):
    """
    彙總邏輯：同批次取最大值，若有 N.D. 則優先級低於數值
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
        return str(max(nums))
    if has_neg:
        return "NEGATIVE"
    if has_nd:
        return "N.D."
    [cite_start]return "" [cite: 8, 9, 10]


# =========================
# UI 與主流程
# =========================
uploaded_files = st.file_uploader(
    "請上傳 SGS PDF Report（可一次多選）",
    type="pdf",
    accept_multiple_files=True
[cite_start]) [cite: 10]

if uploaded_files:
    rows = []

    for file in uploaded_files:
        full_text, pages_text = extract_text_and_pages(file)

        record = {}

        # 各檢測項目
        for item, keyword in ITEM_RULES.items():
            record[item] = extract_result(full_text, keyword)

        # PFAS（是否有測）
        record["PFAS"] = extract_pfas(full_text)

        # DATE（只看第一頁）
        raw_date = extract_date(pages_text[0]) if pages_text else ""
        record["DATE"] = normalize_date(raw_date)

        [cite_start]rows.append(record) [cite: 11]

    df_all = pd.DataFrame(rows)

    # ===== 同批 PDF 彙總（最嚴格結果）=====
    merged = {}
    if not df_all.empty:
        for col in FINAL_COLUMNS:
            if col in ["DATE", "PFAS"]:
                continue
            # 確保欄位存在
            if col in df_all.columns:
                merged[col] = merge_results(df_all[col].tolist())
            else:
                merged[col] = ""

        # PFAS 邏輯
        merged["PFAS"] = "REPORT" if "REPORT" in df_all["PFAS"].tolist() else ""
        
        # DATE 邏輯 (取最新)
        valid_dates = [d for d in df_all["DATE"] if d]
        merged["DATE"] = max(valid_dates) if valid_dates else ""

        [cite_start]df_final = pd.DataFrame([merged], columns=FINAL_COLUMNS) [cite: 12]

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
        [cite_start]) [cite: 13]
    else:
        st.warning("無法讀取資料，請確認 PDF 內容。")
