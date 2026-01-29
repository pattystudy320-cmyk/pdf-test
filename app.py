import streamlit as st
import pdfplumber
import pandas as pd
import re
from dateutil import parser
import io

# 設定頁面資訊
st.set_page_config(page_title="SGS Report Parser", layout="wide")
st.title("📄 SGS Report 檢測結果彙總工具 (DEHP 修正版)")

# =========================
# 1. 欄位定義規則
# =========================
ITEM_RULES = {
    "Pb": r"Lead\s*\(Pb\)",
    "Cd": r"Cadmium\s*\(Cd\)",
    "Hg": r"Mercury\s*\(Hg\)",
    "CrVI": r"Hexavalent Chromium",
    "PBBs": r"Sum of PBBs",
    "PBDEs": r"Sum of PBDEs",
    "DEHP": r"DEHP|Di\(2-ethylhexyl\)\s*phthalate",
    "BBP": r"BBP|Benzyl\s*butyl\s*phthalate",
    "DBP": r"DBP|Dibutyl\s*phthalate",
    "DIBP": r"DIBP|Diisobutyl\s*phthalate",
    "F": r"\bFluorine\b",
    "CL": r"\bChlorine\b",
    "BR": r"\bBromine\b",
    "I": r"\bIodine\b",
    "PFOS": r"PFOS"
}

FINAL_COLUMNS = [
    "Pb", "Cd", "Hg", "CrVI", "PBBs", "PBDEs",
    "DEHP", "BBP", "DBP", "DIBP",
    "F", "CL", "BR", "I",
    "PFOS", "PFAS", "DATE"
]

# =========================
# 2. 核心功能函式
# =========================

def extract_text_and_pages(pdf_file):
    """讀取 PDF 文字內容"""
    full_text = ""
    pages_text = []
    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            pages_text.append(text)
            full_text += text + "\n"
    return full_text, pages_text

def extract_result(text, keyword, item_name):
    """
    V6 最終邏輯:
    1. DEHP 特例: 擴大讀取 4 行，並刪除名字裡的 "2"。
    2. 除噪: 刪除 Max, MDL, Year, CAS。
    3. N.D. 優先: 只要有 N.D. 就回傳。
    4. 數字計數: 
       - PBBs/PBDEs: 1 個數字 -> Result
       - 其他: 1 個數字 -> MDL (回傳 N.D.) / 2 個數字 -> 取第 1 個
    """
    lines = text.splitlines()

    for i, line in enumerate(lines):
        # 步驟 A: 鎖定關鍵字所在的行
        if re.search(keyword, line, re.IGNORECASE):
            
            # --- DEHP 特例設定 1: 擴大視野 ---
            if item_name == "DEHP":
                # DEHP 名字長且常換行，多讀幾行確保抓到 N.D.
                context = " ".join(lines[i:i+4])
            else:
                # 一般項目讀 2 行就夠 (避免抓到別欄)
                context = " ".join(lines[i:i+2])

            # ==========================================
            # 步驟 B: 手術室 - 強力切除雜訊
            # ==========================================
            
            # --- DEHP 特例設定 2: 消滅內鬼 ---
            if item_name == "DEHP":
                # 刪除 "2-ethylhexyl" 和 "Di(2-"，避免抓到名字裡的 2
                context = re.sub(r"2-ethylhexyl", " ", context, flags=re.IGNORECASE)
                context = re.sub(r"Di\(2-", " ", context, flags=re.IGNORECASE)

            # 1. 切除單位
            context = re.sub(r"mg/kg|ppm|%|wt%", " ", context, flags=re.IGNORECASE)

            # 2. 切除 CAS No.
            context = re.sub(r"\(?CAS\s*No\.?[\s\d-]+\)?", " ", context, flags=re.IGNORECASE)

            # 3. 切除 標準編號與年份
            context = re.sub(r"IEC\s*62321[-\d:+A]*", " ", context, flags=re.IGNORECASE)
            context = re.sub(r"\b(19|20)\d{2}\b", " ", context) 

            # 4. 切除 Limit / MDL 標籤與數值
            context = re.sub(r"(Max|Limit|MDL|LOQ)\s*\d+(\.\d+)?", " ", context, flags=re.IGNORECASE)

            # ==========================================
            # 步驟 C: N.D. 判定 (最高優先級)
            # ==========================================
            
            nd_pattern = r"(\bN\s*\.?\s*D\s*\.?\b)|(Not\s*Detected)"
            if re.search(nd_pattern, context, re.IGNORECASE):
                return "N.D."
            
            if re.search(r"NEGATIVE", context, re.IGNORECASE):
                return "NEGATIVE"

            # ==========================================
            # 步驟 D: 數字計數法 (核心邏輯)
            # ==========================================
            
            nums = re.findall(r"\b\d+(?:\.\d+)?\b", context)
            
            if not nums:
                return "N.D."

            # --- 依照 Item 決定策略 ---
            
            # 特權項目: PBBs / PBDEs (MDL 為 Dash "-")
            if item_name in ["PBBs", "PBDEs"]:
                return nums[0] # 直接回傳唯一的數字
            
            # 一般項目: Pb, Cd, DEHP 等 (MDL 必填)
            else:
                if len(nums) >= 2:
                    # 剩下兩個以上數字：[結果] [MDL] -> 取第 1 個
                    found_val = nums[0]
                    # 防呆：如果是年份殘渣
                    try:
                        f_val = float(found_val)
                        if 1990 <= f_val <= 2030 and f_val.is_integer():
                             return nums[1]
                    except:
                        pass
                    return found_val
                
                elif len(nums) == 1:
                    # 只剩下一個數字，極大機率是 MDL (如 2.0, 50.0) -> 強制判定為 N.D.
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
    return ""

# =========================
# 3. Streamlit 主程式介面
# =========================

uploaded_files = st.file_uploader(
    "請上傳 SGS PDF Report（可一次多選）",
    type="pdf",
    accept_multiple_files=True
)

if uploaded_files:
    rows = []
    for file in uploaded_files:
        full_text, pages_text = extract_text_and_pages(file)
        record = {}

        # 傳入 item name 以便啟用 DEHP 特例邏輯
        for item, keyword in ITEM_RULES.items():
            record[item] = extract_result(full_text, keyword, item)

        record["PFAS"] = extract_pfas(full_text)
        raw_date = extract_date(pages_text[0]) if pages_text else ""
        record["DATE"] = normalize_date(raw_date)

        rows.append(record)

    df_all = pd.DataFrame(rows)

    # 同批次彙總
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

        st.subheader("📊 彙總結果（同批 SGS Report）")
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
