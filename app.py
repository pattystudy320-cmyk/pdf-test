import streamlit as st
import pdfplumber
import pandas as pd
import re
from dateutil import parser
import io

st.set_page_config(page_title="SGS Report Parser", layout="wide")
st.title("📄 SGS Report 檢測結果彙總工具 (座標切割版)")

# =========================
# 1. 測項關鍵字定義
# =========================
# 用來定位 Y 軸高度 (Row)
ITEM_KEYWORDS = {
    "Pb": "Lead",
    "Cd": "Cadmium",
    "Hg": "Mercury",
    "CrVI": "Hexavalent Chromium",
    "PBBs": "Sum of PBBs",
    "PBDEs": "Sum of PBDEs",
    "DEHP": "Di(2-ethylhexyl) phthalate",
    "BBP": "Benzyl butyl phthalate",
    "DBP": "Dibutyl phthalate",
    "DIBP": "Diisobutyl phthalate",
    "F": "Fluorine",
    "CL": "Chlorine",
    "BR": "Bromine",
    "I": "Iodine",
    "PFOS": "PFOS"
}

FINAL_COLUMNS = [
    "Pb", "Cd", "Hg", "CrVI", "PBBs", "PBDEs",
    "DEHP", "BBP", "DBP", "DIBP",
    "F", "CL", "BR", "I",
    "PFOS", "PFAS", "DATE"
]

# =========================
# 2. 核心功能：座標定位與切割
# =========================

def get_result_column_x_range(page):
    """
    掃描頁面標題，找出 'Result' 欄位的左右邊界 (X軸範圍)
    回傳: (x0, x1) 或 None
    """
    words = page.extract_words()
    
    result_header = None
    mdl_header = None
    
    # 尋找表頭關鍵字
    for w in words:
        text = w["text"].strip()
        # 找 Result 標題
        if text == "Result" or text == "Result(s)":
            # 有時候表頭會有兩行，取最上面的
            if result_header is None or w["top"] < result_header["top"]:
                result_header = w
        
        # 找 MDL 標題 (作為右邊界)
        if text == "MDL" or text == "LOQ":
            if mdl_header is None or w["top"] < mdl_header["top"]:
                mdl_header = w
    
    if result_header:
        x0 = result_header["x0"] - 5  # 左邊界稍微寬一點，怕對齊誤差
        
        # 如果有找到 MDL，右邊界就是 MDL 的左邊
        if mdl_header:
            x1 = mdl_header["x0"] - 2 # 不要在邊界重疊，稍微留空
        else:
            # 沒找到 MDL，就假設一個寬度 (例如 80 單位)
            x1 = x0 + 80 
            
        return (x0, x1)
    
    return None

def extract_value_by_crop(page, keyword, x_range):
    """
    已知 Result 欄位的 X 範圍 (x_range)，
    搜尋 keyword (如 Cadmium) 的 Y 高度，
    然後切割出該區域的文字。
    """
    if not x_range:
        return ""
    
    result_x0, result_x1 = x_range
    words = page.extract_words()
    
    # 1. 找到測項名稱的 Y 座標
    target_row_top = None
    target_row_bottom = None
    
    for w in words:
        # 簡單模糊比對：只要測項關鍵字出現在字詞中
        if keyword.lower() in w["text"].lower():
            # 為了避免抓到內文，通常測項都在左側 (x < 300)
            if w["x0"] < 300:
                target_row_top = w["top"]
                target_row_bottom = w["bottom"]
                break # 找到就停，假設測項名稱只出現一次或取第一次出現
    
    if target_row_top is not None:
        # 2. 定義切割框 (Bounding Box)
        # (x0, top, x1, bottom)
        # Y 軸稍微放寬一點 (+- 2)，避免切到字
        crop_box = (
            result_x0, 
            target_row_top - 2, 
            result_x1, 
            target_row_bottom + 2
        )
        
        try:
            # 3. 執行切割並抓字
            cropped_page = page.crop(crop_box)
            text = cropped_page.extract_text()
            return text.strip() if text else ""
        except Exception:
            # 發生切割錯誤 (例如座標超出範圍)
            return ""

    return ""

def normalize_result(value):
    """
    清洗結果：統一 N.D.，排除單位
    """
    if not value:
        return ""
    
    val_str = str(value).strip()
    
    # 移除常見單位與雜訊
    val_str = re.sub(r"mg/kg|ppm|%|wt%", "", val_str, flags=re.IGNORECASE)
    
    # 判斷 N.D. (包含 ND, N. D., Not Detected)
    # 這裡使用寬鬆判定，只要有 N 和 D 且非單字一部分
    if re.search(r"(\bN\s*\.?\s*D\s*\.?\b)|(Not\s*Detected)", val_str, re.IGNORECASE):
        return "N.D."
    
    if "NEGATIVE" in val_str.upper():
        return "NEGATIVE"

    # 抓取數字 (支援小數點)
    match = re.search(r"\d+(\.\d+)?", val_str)
    if match:
        return match.group(0)
    
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
# 3. 主程式流程
# =========================

uploaded_files = st.file_uploader(
    "請上傳 SGS PDF Report (座標切割版)",
    type="pdf",
    accept_multiple_files=True
)

if uploaded_files:
    rows = []
    for file in uploaded_files:
        full_text = ""
        pages_text = []
        extracted_data = {key: [] for key in ITEM_KEYWORDS}
        
        with pdfplumber.open(file) as pdf:
            for page in pdf.pages:
                # 1. 收集全文 (給 PFAS 和 Date 用)
                text = page.extract_text() or ""
                full_text += text + "\n"
                pages_text.append(text)
                
                # 2. 座標切割邏輯
                # 先找出這頁有沒有 Result 欄位
                x_range = get_result_column_x_range(page)
                
                if x_range:
                    # 如果這頁有 Result 表頭，就去搜刮各個測項
                    for item, keyword in ITEM_KEYWORDS.items():
                        # 特別處理 PBBs/PBDEs 這種標題
                        # 這裡使用精確關鍵字去對應高度
                        raw_val = extract_value_by_crop(page, keyword, x_range)
                        clean_val = normalize_result(raw_val)
                        if clean_val:
                            extracted_data[item].append(clean_val)
        
        # 整理單檔結果
        record = {}
        for item in ITEM_KEYWORDS:
            record[item] = merge_results(extracted_data[item])

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

        st.subheader("📊 彙總結果（座標切割版）")
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
