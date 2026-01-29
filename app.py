import streamlit as st
import pdfplumber
import pandas as pd
import re
from dateutil import parser
import io

st.set_page_config(page_title="SGS Report Parser", layout="wide")
st.title("📄 SGS Report 檢測結果彙總工具")

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
    lines = text.splitlines()

    for i, line in enumerate(lines):
        if re.search(keyword, line, re.IGNORECASE):
            context = " ".join(lines[i:i+3])

            # 排除 IEC 62321 方法編號
            context = re.sub(r"IEC\s*62321[-\d:]*", "", context, flags=re.IGNORECASE)

            # 1️⃣ 數值（只抓結果）
            num = re.search(r"\b(\d+(\.\d+)?)\b", context)
            if num:
                return num.group(1)

            # 2️⃣ NEGATIVE
            if re.search(r"NEGATIVE", context, re.IGNORECASE):
                return "NEGATIVE"

            # 3️⃣ N.D.
            if re.search(r"N\.D\.", context, re.IGNORECASE):
                return "N.D."

    return ""


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
    nums = []
    for v in values:
        if not v:
            continue
        if v not in ["N.D.", "NEGATIVE"]:
            try:
                nums.append(float(v))
            except:
                pass

    if nums:
        return str(max(nums))
    if "NEGATIVE" in values:
        return "NEGATIVE"
    if "N.D." in values:
        return "N.D."
    return ""


# =========================
# UI
# =========================
uploaded_files = st.file_uploader(
    "請上傳 SGS PDF Report（可一次多選）",
    type="pdf",
    accept_multiple_files=True
)

# =========================
# 主流程
# =========================
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
        raw_date = extract_date(pages_text[0])
        record["DATE"] = normalize_date(raw_date)

        rows.append(record)

    df_all = pd.DataFrame(rows)

    # ===== 同批 PDF 彙總（最嚴格結果）=====
    merged = {}
    for col in FINAL_COLUMNS:
        if col in ["DATE", "PFAS"]:
            continue
        merged[col] = merge_results(df_all[col].tolist())

    merged["PFAS"] = "REPORT" if "REPORT" in df_all["PFAS"].tolist() else ""
    merged["DATE"] = max(df_all["DATE"])

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
