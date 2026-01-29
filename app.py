import streamlit as st
import pdfplumber
import pandas as pd
import re
from dateutil import parser
import io

# 設定頁面資訊
st.set_page_config(page_title="SGS Report Parser", layout="wide")
st.title("📄 SGS Report 檢測結果彙總工具 (最終邏輯版)")

# =========================
# 1. 欄位定義規則 (Regex)
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
    "F": r"\bFluorine\b",   # 加 \b 避免抓到部分單字
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
    最終版邏輯 V5 (數字計數法):
    1. 找到關鍵字所在行。
    2. 強力清除雜訊 (Year, CAS, Limit)。
    3. 優先檢查 N.D.。
    4. 計算剩餘數字數量：
       - PBBs/PBDEs: 剩下 1 個數字 -> 視為 Result (因 MDL 為 -)。
       - 其他項目: 剩下 1 個數字 -> 視為 MDL -> 回傳 N.D.。
                 剩下 2 個數字 -> 第一個為 Result。
    """
    lines = text.splitlines()

    for i, line in enumerate(lines):
        # 步驟 A: 鎖定關鍵字所在的行
        if re.search(keyword, line, re.IGNORECASE):
            # 抓取上下文 (當行 + 下一行)，縮小範圍
            context = " ".join(lines[i:i+2])

            # ==========================================
            # 步驟 B: 手術室 - 強力切除雜訊
            # ==========================================
            
            # 1. 切除單位
            context = re.sub(r"mg/kg|ppm|%|wt%", " ", context, flags=re.IGNORECASE)

            # 2. 切除 CAS No.
            context = re.sub(r"\(?CAS\s*No\.?[\s\d-]+\)?", " ", context, flags=re.IGNORECASE)

            # 3. 切除 標準編號與年份 (IEC 62321...:2017)
            context = re.sub(r"IEC\s*62321[-\d:+A]*", " ", context, flags=re.IGNORECASE)
            context = re.sub(r"\b(19|20)\d{2}\b", " ", context) # 移除 20xx 年份

            # 4. 切除 Limit / MDL 標籤與數值 (Max 1000, MDL 2)
            context = re.sub(r"(Max|Limit|MDL|LOQ)\s*\d+(\.\d+)?", " ", context, flags=re.IGNORECASE)

            # ==========================================
            # 步驟 C: N.D. 判定 (最高優先級)
            # ==========================================
            
            # 只要有 N 和 D，且非單字一部分 (例如 N.D., ND, N. D., Not Detected)
            nd_pattern = r"(\bN\s*\.?\s*D\s*\.?\b)|(Not\s*Detected)"
            if re.search(nd_pattern, context, re.IGNORECASE):
                return "N.D."
            
            if re.search(r"NEGATIVE", context, re.IGNORECASE):
                return "NEGATIVE"

            # ==========================================
            # 步驟 D: 數字計數法 (核心邏輯)
            # ==========================================
            
            # 抓出剩餘的所有數字 (支援整數與小數)
            nums = re.findall(r"\b\d+(?:\.\d+)?\b", context)
            
            if not nums:
                # 沒數字也沒 N.D.，保守回傳 N.D.
                return "N.D."

            # --- 依照 Item 決定策略 ---
            
            # 特權項目: PBBs / PBDEs (MDL 可能是 Dash "-")
            if item_name in ["PBBs", "PBDEs"]:
                # 如果有數字，就直接抓第一個 (忽略只有一個數字只能是 MDL 的規則)
                return nums[0]
            
            # 一般項目: Pb, Cd, F, Cl 等 (MDL 必填)
            else:
                if len(nums) >= 2:
                    # 剩下兩個以上數字：[結果] [MDL]
                    # 第一個是結果
                    found_val = nums[0]
                    # 防呆：如果是年份殘渣 (1990-2030 整數)，跳過
                    try:
                        f_val = float(found_val)
                        if 1990 <= f_val <= 2030 and f_val.is_integer():
                            # 如果第一個像是年份，且還有第二個數字，那就取第二個
                            return nums[1]
                    except:
                        pass
                    return found_val
                
                elif len(nums) == 1:
                    # 只剩下一個數字！
                    # 極大機率是 N.D. 沒抓到，剩下的這個是 MDL (如 2.0, 50.0)
                    # 強制判定為 N.D.
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
    """
    彙總邏輯：取最大值，若有 N.D. 則優先級低於數值
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

        # 抓取各項目，傳入 item key 以便區分特權邏輯
        for item, keyword in ITEM_RULES.items():
            record[item] = extract_result(full_text, keyword, item)

        # 特殊項目與日期
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

        # 顯示與下載
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
