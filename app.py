import streamlit as st
import pdfplumber
import pandas as pd
import re
from dateutil import parser

st.set_page_config(page_title="SGS Report Parser", layout="wide")
st.title("📄 SGS Report 檢測結果彙總工具")

# =========================
# 欄位與關鍵字定義
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
    "PartNo",
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
    blocks = re.findall(
        rf"{keyword}.*?(?:\n|$)",
        text,
        re.IGNORECASE | re.DOTALL
    )
    if not blocks:
        return ""

    block_text = " ".join(blocks)

    # 1️⃣ 數值優先
    num = re.search(r"(\d+(\.\d+)?)", block_text)
    if num:
        return num.group(1)

    # 2️⃣ NEGATIVE
    if re.search(r"NEGATIVE", block_text, re.IGNORECASE):
        return "NEGATIVE"

    # 3️⃣ N.D.
    if re.search(r"N\.D\.", block_text, re.IGNORECASE):
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
    "請上傳 SGS PDF（可多選）",
    type="pdf",
    accept_multiple_files=True
)

part_no = st.text_input(
    "料號（同一批 Report 請填同一料號）",
    placeholder="例如：S1-Substrate"
)

# =========================
# 主流程
# =========================
if uploaded_files and part_no:
    rows = []

    for file in uploaded_files:
        full_text, pages_text = extract_text_and_pages(file)

        record = {"PartNo": part_no}

        for item, keyword in ITEM_RULES.items():
            record[item] = extract_result(full_text, keyword)

        record["PFAS"] = extract_pfas(full_text)

        raw_date = extract_date(pages_text[0])
        record["DATE"] = normalize_date(raw_date)

        rows.append(record)

    df_all = pd.DataFrame(rows)

    # ===== 同料號彙總 =====
    merged = {"PartNo": part_no}
    for col in FINAL_COLUMNS:
        if col in ["PartNo", "DATE", "PFAS"]:
            continue
        merged[col] = merge_results(df_all[col].tolist())

    merged["PFAS"] = "REPORT" if "REPORT" in df_all["PFAS"].tolist() else ""
    merged["DATE"] = max(df_all["DATE"])

    df_final = pd.DataFrame([merged], columns=FINAL_COLUMNS)

    st.subheader("📊 彙總結果（同料號最嚴格）")
    st.dataframe(df_final, use_container_width=True)

    # ===== Excel 匯出 =====
    import io
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df_final.to_excel(writer, sheet_name="SGS_Result", index=False)

    st.download_button(
        "⬇️ 下載公司制式 Excel",
        output.getvalue(),
        file_name=f"{part_no}_SGS_Result.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

elif uploaded_files and not part_no:
    st.warning("⚠️ 請先輸入料號，才能進行彙總")
