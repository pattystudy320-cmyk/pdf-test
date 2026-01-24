import streamlit as st
import pdfplumber
import pandas as pd
import re
import io
from dateutil import parser

# ==========================================
# 1. 關鍵字定義 (化學物質)
# ==========================================

TARGET_ITEMS = [
    "Pb", "Cd", "Hg", "Cr6+", "PBBs", "PBDEs",
    "DEHP", "DBP", "BBP", "DIBP",
    "F", "Cl", "Br", "I",
    "PFOS", "PFAS", "DATE", "FILENAME"
]

# 化學物質關鍵字映射
KEYWORDS_MAP = {
    r"(?i)\b(Lead|Pb)\b": "Pb",
    r"(?i)\b(Cadmium|Cd)\b": "Cd",
    r"(?i)\b(Mercury|Hg)\b": "Hg",
    r"(?i)\b(Hexavalent Chromium|Cr\(?VI\)?|Cr6\+)\b": "Cr6+",
    r"(?i)\b(DEHP|Di\(2-ethylhexyl\)\s*phthalate)\b": "DEHP",
    r"(?i)\b(DBP|Dibutyl\s*phthalate)\b": "DBP",
    r"(?i)\b(BBP|Butyl\s*benzyl\s*phthalate)\b": "BBP",
    r"(?i)\b(DIBP|Diisobutyl\s*phthalate)\b": "DIBP",
    r"(?i)\b(Fluorine|F)\b": "F",
    r"(?i)\b(Chlorine|Cl)\b": "Cl",
    r"(?i)\b(Bromine|Br)\b": "Br",
    r"(?i)\b(Iodine|I)\b": "I",
    r"(?i)\b(PFOS|Perfluorooctane\s*sulfonates)\b": "PFOS"
}

# 子項加總特徵
PBB_SUBITEMS = r"(?i)(Monobromobiphenyl|Dibromobiphenyl|Tribromobiphenyl|Tetrabromobiphenyl|Pentabromobiphenyl|Hexabromobiphenyl|Heptabromobiphenyl|Octabromobiphenyl|Nonabromobiphenyl|Decabromobiphenyl)"
PBDE_SUBITEMS = r"(?i)(Monobromodiphenyl ether|Dibromodiphenyl ether|Tribromodiphenyl ether|Tetrabromodiphenyl ether|Pentabromodiphenyl ether|Hexabromodiphenyl ether|Heptabromodiphenyl ether|Octabromodiphenyl ether|Nonabromodiphenyl ether|Decabromodiphenyl ether)"

# ==========================================
# 2. 核心邏輯：日期提取與標準化
# ==========================================

def standardize_date(date_str):
    """
    將各種雜亂的日期格式統一轉換為 YYYY/MM/DD
    """
    if not date_str:
        return "Unknown"
    
    # 清理雜訊
    clean_str = str(date_str).strip()
    
    # 1. 處理中文格式 (2024年10月10日 -> 2024/10/10)
    clean_str = clean_str.replace("年", "/").replace("月", "/").replace("日", "")
    
    # 2. 處理韓文/ISO 帶點格式 (2024. 10. 17. -> 2024/10/17)
    clean_str = clean_str.replace(".", "/").strip("/") 
    
    try:
        # 使用 fuzzy=True 自動忽略日期字串中的非日期文字 (如 'Date:', 'No.:')
        dt = parser.parse(clean_str, fuzzy=True)
        # 強制格式化為 YYYY/MM/DD
        return dt.strftime("%Y/%m/%d")
    except:
        return clean_str # 解析失敗回傳原值，方便除錯

def extract_report_date(text_page1):
    """
    雙區塊掃描策略 (Head-then-Tail) + 黑名單過濾
    """
    lines = text_page1.split('\n')
    found_date_str = None
    
    # --- 定義關鍵字 ---
    # 黑名單：若該行出現這些字，絕對不是報告日期
    BLACKLIST = [r"(?i)Receive", r"(?i)Period", r"(?i)Tested", r"(?i)Due", r"(?i)Sample"]
    
    # 白名單：明確的報告日期標籤 (英/中/韓)
    # 注意：SGS 有時只寫 "Date:"，CTI 有時在簽名區寫 "Date"
    LABEL_PATTERNS = [
        r"(?i)Report\s*Date", 
        r"(?i)Issue\s*Date", 
        r"报告日期", 
        r"日期", 
        r"발행일자",
        r"(?i)^Date\s*[:：]", # 嚴格的 Date 開頭
        r"(?i)Date\s*[:：]"   # 一般 Date 標籤
    ]

    # 日期數值正則 (輔助抓取無標籤的日期，如 CTI 簽名旁)
    # 抓取 202x-xx-xx 或 202x/xx/xx 或 202x.xx.xx
    DATE_VALUE_PATTERN = r"(20\d{2}[-./年]\s?\d{1,2}[-./月]\s?\d{1,2}[日]?|[A-Za-z]{3}\s+\d{1,2},\s+20\d{2})"

    # --- 策略 1: 掃描頁首 (前 20 行) ---
    for i, line in enumerate(lines[:25]):
        # 1. 黑名單檢查
        if any(re.search(b, line) for b in BLACKLIST):
            continue
            
        # 2. 白名單標籤檢查
        for label in LABEL_PATTERNS:
            if re.search(label, line):
                # 嘗試提取標籤後的內容
                # 例如 "Date: 2024/10/10" -> 抓取 "2024/10/10"
                # 利用 split 抓冒號後面的東西，或是利用 regex 抓取日期格式
                match = re.search(DATE_VALUE_PATTERN, line)
                if match:
                    return standardize_date(match.group(0))
                
                # 若無明顯日期格式，嘗試抓標籤後的剩餘字串
                parts = re.split(r"[:：]", line, 1)
                if len(parts) > 1 and len(parts[1].strip()) > 5:
                    return standardize_date(parts[1])

    # --- 策略 2: 掃描頁尾 (後 15 行) ---
    # 針對 CTI 這種簽名在底部的報告
    total_lines = len(lines)
    start_line = max(0, total_lines - 20)
    
    for line in lines[start_line:]:
        # 尋找 "Date" 關鍵字附近的日期
        if re.search(r"(?i)Date", line) or re.search(r"\d{4}", line): # 寬鬆條件
             match = re.search(DATE_VALUE_PATTERN, line)
             if match:
                 # 再次確認不是黑名單 (雖然頁尾很少出現收件日期)
                 if not any(re.search(b, line) for b in BLACKLIST):
                     return standardize_date(match.group(0))

    return "Unknown"

# ==========================================
# 3. 數值清理與處理
# ==========================================

def clean_value(val_str):
    if not val_str: return None
    val_str = str(val_str).strip()
    if re.search(r"(?i)(n\.?d\.?|not detected|<)", val_str): return "N.D."
    if re.search(r"(?i)negative", val_str): return "NEGATIVE"
    match = re.search(r"(\d+\.?\d*)", val_str)
    if match: return float(match.group(1))
    return "N.D."

def get_value_priority(val):
    if isinstance(val, (int, float)): return (3, val)
    if val == "NEGATIVE": return (2, 0)
    if val == "N.D.": return (1, 0)
    return (0, 0)

# ==========================================
# 4. 單一檔案處理邏輯
# ==========================================

def process_single_file(uploaded_file):
    filename = uploaded_file.name
    result = {k: None for k in TARGET_ITEMS}
    result["FILENAME"] = filename
    
    pbb_sum = 0
    pbde_sum = 0
    pbb_found = False
    pbde_found = False
    
    try:
        with pdfplumber.open(uploaded_file) as pdf:
            full_text = ""
            
            for i, page in enumerate(pdf.pages):
                text = page.extract_text()
                if not text: continue
                full_text += text + "\n"

                # *** 日期抓取僅針對第一頁 ***
                if i == 0:
                    result["DATE"] = extract_report_date(text)

                # 表格數據提取
                tables = page.extract_tables()
                if tables:
                    for table in tables:
                        for row in table:
                            # 將 row 轉為字串並移除換行符號以便 regex 搜尋
                            row_str = " ".join([str(cell) for cell in row if cell]).replace("\n", " ")
                            
                            # 一般物質提取
                            for pattern, key in KEYWORDS_MAP.items():
                                if re.search(pattern, row_str):
                                    raw_cells = [str(cell).strip() for cell in row if cell]
                                    cleaned_values = [clean_value(c) for c in raw_cells]
                                    
                                    # 過濾 Limit 值 (避免抓到 100/1000)
                                    valid_vals = []
                                    for val in cleaned_values:
                                        if isinstance(val, (int, float)):
                                            valid_vals.append(val)
                                        elif val in ["N.D.", "NEGATIVE"]:
                                            valid_vals.append(val)
                                    
                                    final_val = None
                                    if valid_vals:
                                        final_val = valid_vals[-1] # 取最後一個有效值
                                        # 簡單防呆
                                        if isinstance(final_val, (int, float)) and final_val in [100.0, 1000.0]:
                                            others = [v for v in valid_vals if v != final_val]
                                            if others: final_val = others[-1]

                                    if final_val is not None:
                                        priority_curr = get_value_priority(result[key])
                                        priority_new = get_value_priority(final_val)
                                        if priority_new > priority_curr:
                                            result[key] = final_val
                                        break 

                            # PBBs 加總
                            if re.search(PBB_SUBITEMS, row_str):
                                pbb_found = True
                                cells = [clean_value(str(cell)) for cell in row if cell]
                                for val in cells:
                                    if isinstance(val, (int, float)) and val not in [100.0, 1000.0]:
                                        pbb_sum += val
                                        break
                            
                            # PBDEs 加總
                            if re.search(PBDE_SUBITEMS, row_str):
                                pbde_found = True
                                cells = [clean_value(str(cell)) for cell in row if cell]
                                for val in cells:
                                    if isinstance(val, (int, float)) and val not in [100.0, 1000.0]:
                                        pbde_sum += val
                                        break

            # PFAS 全文搜索
            if "PFAS" in full_text or "Per- and polyfluoroalkyl substances" in full_text:
                result["PFAS"] = "REPORT"
            else:
                result["PFAS"] = ""

            result["PBBs"] = pbb_sum if pbb_found and pbb_sum > 0 else "N.D."
            result["PBDEs"] = pbde_sum if pbde_found and pbde_sum > 0 else "N.D."

            return result

    except Exception as e:
        st.error(f"Error processing {filename}: {e}")
        return None

# ==========================================
# 5. Streamlit 主程式
# ==========================================

def main():
    st.set_page_config(page_title="RoHS 報告彙整工具", layout="wide")
    st.title("📄 化學測試報告自動彙整工具")
    st.markdown("""
    **功能說明：**
    1. 支援 SGS、CTI、Intertek 等多種報告格式。
    2. 自動抓取 **報告發行日期** 並統一格式為 `YYYY/MM/DD`。
    3. 自動過濾法規限值 (Limit)，只抓取測試結果 (Result)。
    """)

    uploaded_files = st.file_uploader("請選擇 PDF 檔案 (可多選)", type="pdf", accept_multiple_files=True)

    if uploaded_files:
        if st.button("開始分析"):
            with st.spinner("正在讀取並分析 PDF..."):
                all_results = []
                progress_bar = st.progress(0)
                
                for i, file in enumerate(uploaded_files):
                    res = process_single_file(file)
                    if res:
                        all_results.append(res)
                    progress_bar.progress((i + 1) / len(uploaded_files))
                
                if not all_results:
                    st.warning("未能從檔案中提取到有效數據。")
                    return

                # 建立 DataFrame
                df_detail = pd.DataFrame(all_results)
                
                # 調整欄位順序：FILENAME, DATE 放最前面
                cols = ["FILENAME", "DATE"] + [c for c in TARGET_ITEMS if c not in ["FILENAME", "DATE"]]
                df_detail = df_detail[cols]

                st.success("分析完成！")
                st.subheader("📊 分析結果預覽")
                st.dataframe(df_detail)

                # 匯出 Excel
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    df_detail.to_excel(writer, index=False, sheet_name='Summary')
                
                output.seek(0)
                
                st.download_button(
                    label="📥 下載 Excel 報告",
                    data=output,
                    file_name=f"RoHS_Summary_{pd.Timestamp.now().strftime('%Y%m%d')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )

if __name__ == "__main__":
    main()
