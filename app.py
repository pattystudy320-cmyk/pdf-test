import streamlit as st
import pdfplumber
import pandas as pd
import re
import io
from dateutil import parser

# ==========================================
# 1. 關鍵字定義
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

PBB_SUBITEMS = r"(?i)(Monobromobiphenyl|Dibromobiphenyl|Tribromobiphenyl|Tetrabromobiphenyl|Pentabromobiphenyl|Hexabromobiphenyl|Heptabromobiphenyl|Octabromobiphenyl|Nonabromobiphenyl|Decabromobiphenyl)"
PBDE_SUBITEMS = r"(?i)(Monobromodiphenyl ether|Dibromodiphenyl ether|Tribromodiphenyl ether|Tetrabromodiphenyl ether|Pentabromodiphenyl ether|Hexabromodiphenyl ether|Heptabromodiphenyl ether|Octabromodiphenyl ether|Nonabromodiphenyl ether|Decabromodiphenyl ether)"

# ==========================================
# 2. 日期處理邏輯 (標準化為 YYYY/MM/DD)
# ==========================================

def standardize_date(date_str):
    """將各種雜亂格式統一轉為 YYYY/MM/DD"""
    if not date_str: return "Unknown"
    
    clean_str = str(date_str).strip()
    # 處理中文 (2024年10月10日 -> 2024/10/10)
    clean_str = clean_str.replace("年", "/").replace("月", "/").replace("日", "")
    # 處理韓文/ISO 帶點格式 (2024. 10. 17. -> 2024/10/17)
    clean_str = clean_str.replace(".", "/").strip("/")
    
    try:
        dt = parser.parse(clean_str, fuzzy=True)
        return dt.strftime("%Y/%m/%d")
    except:
        return clean_str

def extract_report_date(text_page1):
    """Head-then-Tail 策略 + 多語言關鍵字"""
    lines = text_page1.split('\n')
    BLACKLIST = [r"(?i)Receive", r"(?i)Period", r"(?i)Tested", r"(?i)Due", r"(?i)Sample"]
    LABEL_PATTERNS = [
        r"(?i)Report\s*Date", r"(?i)Issue\s*Date", r"报告日期", r"日期", r"발행일자",
        r"(?i)^Date\s*[:：]", r"(?i)Date\s*[:：]"
    ]
    # 抓取數值格式 (202x-xx-xx, 202x/xx/xx, 202x.xx.xx, Feb 27 2025)
    DATE_VALUE_PATTERN = r"(20\d{2}[-./年]\s?\d{1,2}[-./月]\s?\d{1,2}[日]?|[A-Za-z]{3}\s+\d{1,2},\s+20\d{2})"

    # 策略 1: 頁首掃描
    for line in lines[:25]:
        if any(re.search(b, line) for b in BLACKLIST): continue
        for label in LABEL_PATTERNS:
            if re.search(label, line):
                match = re.search(DATE_VALUE_PATTERN, line)
                if match: return standardize_date(match.group(0))
                # 備用: 抓冒號後的內容
                parts = re.split(r"[:：]", line, 1)
                if len(parts) > 1 and len(parts[1].strip()) > 5:
                    return standardize_date(parts[1])

    # 策略 2: 頁尾掃描 (針對 CTI)
    for line in lines[max(0, len(lines)-20):]:
        if re.search(r"(?i)Date", line) or re.search(r"\d{4}", line):
             match = re.search(DATE_VALUE_PATTERN, line)
             if match:
                 if not any(re.search(b, line) for b in BLACKLIST):
                     return standardize_date(match.group(0))
    return "Unknown"

# ==========================================
# 3. 核心：智慧欄位定位 (Header Mapping)
# ==========================================

def get_valid_column_indices(header_row):
    """
    分析表頭，回傳「非黑名單」的欄位索引列表 (即潛在的結果欄位)
    """
    if not header_row: return []
    
    # 黑名單關鍵字 (忽略這些欄位)
    IGNORE_KEYWORDS = [
        r"(?i)Limit", r"(?i)限值", 
        r"(?i)MDL", r"(?i)Method\s*Detection", r"(?i)方法检出限", 
        r"(?i)Unit", r"(?i)单位", 
        r"(?i)Method", r"(?i)Test\s*Method",
        r"(?i)Item", r"(?i)Test\s*Item", r"(?i)测试项目" # 項目名稱欄位也不含數值
    ]
    
    valid_indices = []
    for idx, cell in enumerate(header_row):
        cell_str = str(cell).strip()
        is_ignored = False
        for pattern in IGNORE_KEYWORDS:
            if re.search(pattern, cell_str):
                is_ignored = True
                break
        
        if not is_ignored:
            valid_indices.append(idx)
            
    return valid_indices

def clean_value(val_str):
    """清理數值，保留 N.D."""
    if not val_str: return None
    val_str = str(val_str).strip()
    if re.search(r"(?i)(n\.?d\.?|not detected|<)", val_str): return "N.D."
    if re.search(r"(?i)negative", val_str): return "NEGATIVE"
    
    # 嘗試提取數字
    match = re.search(r"(\d+\.?\d*)", val_str)
    if match: return float(match.group(1))
    return None

def extract_value_from_row(row, valid_indices):
    """從一行中提取唯一的有效結果 (數字 或 N.D.)"""
    candidates = []
    for idx in valid_indices:
        if idx < len(row):
            val = clean_value(str(row[idx]))
            if val is not None:
                candidates.append(val)
    
    if not candidates: return None
    
    # 邏輯：同一行應該只有一個結果。如果有數字，優先回傳數字 (防止 N.D. 混入)
    # 如果全是 N.D.，回傳 N.D.
    numbers = [c for c in candidates if isinstance(c, float)]
    if numbers:
        return numbers[0] # 假設只有一個有效數字結果
    return "N.D." # 若無數字，則回傳 N.D.

# ==========================================
# 4. 檔案處理邏輯
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
                if text: full_text += text + "\n"
                if i == 0: result["DATE"] = extract_report_date(text)

                tables = page.extract_tables()
                if tables:
                    for table in tables:
                        # 1. 分析表頭 (假設第一列是表頭)
                        if not table: continue
                        header_row = table[0]
                        valid_indices = get_valid_column_indices(header_row)
                        
                        # 若找不到有效欄位，可能表頭識別失敗，嘗試全行掃描 (Fallback)
                        if not valid_indices: 
                            valid_indices = range(len(header_row)) 

                        # 2. 遍歷數據行
                        for row in table[1:]: # 跳過表頭
                            row_str = " ".join([str(cell) for cell in row if cell]).replace("\n", " ")
                            
                            # 一般物質提取
                            for pattern, key in KEYWORDS_MAP.items():
                                if re.search(pattern, row_str):
                                    val = extract_value_from_row(row, valid_indices)
                                    if val is not None:
                                        # 簡單更新邏輯 (若有值則覆蓋)
                                        result[key] = val
                                    break

                            # PBBs 加總
                            if re.search(PBB_SUBITEMS, row_str):
                                pbb_found = True
                                val = extract_value_from_row(row, valid_indices)
                                if isinstance(val, float): pbb_sum += val
                            
                            # PBDEs 加總
                            if re.search(PBDE_SUBITEMS, row_str):
                                pbde_found = True
                                val = extract_value_from_row(row, valid_indices)
                                if isinstance(val, float): pbde_sum += val

            # PFAS
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
# 5. Streamlit 介面
# ==========================================

def main():
    st.set_page_config(page_title="RoHS 報告彙整工具", layout="wide")
    st.title("📄 化學測試報告自動彙整工具")
    st.markdown("""
    **版本特點：**
    1. **精準抓取**：自動識別表頭，排除限值(Limit)與檢出限(MDL)，精準鎖定結果欄位。
    2. **日期標準化**：支援中/英/韓格式，統一轉為 `YYYY/MM/DD`。
    3. **N.D.保留**：Excel 輸出保留 "N.D." 字樣。
    """)

    uploaded_files = st.file_uploader("請選擇 PDF 檔案", type="pdf", accept_multiple_files=True)

    if uploaded_files:
        if st.button("開始分析"):
            with st.spinner("正在讀取並分析 PDF..."):
                all_results = []
                progress_bar = st.progress(0)
                
                for i, file in enumerate(uploaded_files):
                    res = process_single_file(file)
                    if res: all_results.append(res)
                    progress_bar.progress((i + 1) / len(uploaded_files))
                
                if not all_results:
                    st.warning("未能提取有效數據。")
                    return

                df_detail = pd.DataFrame(all_results)
                # 欄位排序
                cols = ["FILENAME", "DATE"] + [c for c in TARGET_ITEMS if c not in ["FILENAME", "DATE"]]
                df_detail = df_detail[cols]

                st.success("分析完成！")
                st.dataframe(df_detail)

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
