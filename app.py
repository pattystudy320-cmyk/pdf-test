import streamlit as st
import pdfplumber
import pandas as pd
import re
import io
import os
from dateutil import parser

# ==========================================
# 1. 全局設定與關鍵字
# ==========================================

TARGET_ITEMS = [
    "Pb", "Cd", "Hg", "Cr6+", "PBBs", "PBDEs",
    "DEHP", "DBP", "BBP", "DIBP",
    "F", "Cl", "Br", "I",
    "PFOS", "PFAS", "DATE", "FILENAME"
]

# 化學物質正則表達式映射
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
# 2. 通用輔助函數
# ==========================================

def standardize_date(date_str):
    """統一日期格式為 YYYY/MM/DD"""
    if not date_str: return "1900/01/01"
    clean_str = str(date_str).strip()
    clean_str = clean_str.replace("年", "/").replace("月", "/").replace("日", "")
    clean_str = clean_str.replace(".", "/") # 處理 CTI/Intertek 的 2025.06.16
    try:
        dt = parser.parse(clean_str, fuzzy=True)
        return dt.strftime("%Y/%m/%d")
    except:
        return "1900/01/01" # 解析失敗回傳舊日期以便排序

def clean_value(val_str):
    """清理數值，保留 N.D. 與 NEGATIVE"""
    if not val_str: return None
    val_str = str(val_str).strip()
    
    if re.search(r"(?i)(n\.?d\.?|not detected|<)", val_str): return "N.D."
    if re.search(r"(?i)(negative|阴性|陰性)", val_str): return "NEGATIVE"
    if re.search(r"(?i)(positive|阳性|陽性)", val_str): return "POSITIVE"
    
    match = re.search(r"(\d+\.?\d*)", val_str)
    if match: return float(match.group(1))
    return None

def get_value_priority(val):
    """
    定義數值優先級，用於加總比較 (Worst-case logic)
    Level 3: 數字 (越大越優先)
    Level 2: NEGATIVE / POSITIVE
    Level 1: N.D.
    Level 0: None (未檢測)
    """
    if isinstance(val, (int, float)): return (3, val)
    if val in ["NEGATIVE", "POSITIVE"]: return (2, 0)
    if val == "N.D.": return (1, 0)
    return (0, 0)

# ==========================================
# 3. 廠商專屬解析模組 (Dictionary Logic)
# ==========================================

# --- SGS Parser ---
def parse_sgs(pdf_obj, full_text, first_page_text):
    result = {k: None for k in KEYWORDS_MAP.values()}
    result['PFAS'] = ""
    
    # 1. 日期抓取 (SGS 頁首)
    date_patterns = [r"(?i)Date\s*[:：]", r"日期\s*[:：]", r"日期\(Date\)\s*[:：]"]
    for line in first_page_text.split('\n')[:25]:
        for pat in date_patterns:
            if re.search(pat, line):
                match = re.search(r"(20\d{2}[-./年]\s?\d{1,2}[-./月]\s?\d{1,2}|[A-Za-z]{3}\s+\d{1,2}[,\s]+\d{4})", line)
                if match: result['DATE'] = standardize_date(match.group(0))
                break
        if result.get('DATE'): break

    # 2. 表格數據抓取 (刪去法)
    pbb_sum = 0; pbde_sum = 0; pbb_found = False; pbde_found = False
    with pdfplumber.open(pdf_obj) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                if not table: continue
                # 欄位定位: 刪除 Limit/Unit/MDL/Method，剩下的就是 Result
                header = table[0]
                blacklist = [r"(?i)Limit", r"(?i)Unit", r"(?i)MDL", r"(?i)Method", r"(?i)Item", r"限值", r"单位", r"方法"]
                potential_idx = [i for i, col in enumerate(header) if col and not any(re.search(b, str(col)) for b in blacklist)]
                
                result_idx = potential_idx[0] if potential_idx else -1 # 預設最後一欄
                
                for row in table[1:]:
                    if len(row) <= result_idx: continue
                    row_str = " ".join([str(c) for c in row if c]).replace("\n", " ")
                    val = clean_value(row[result_idx])
                    
                    for pat, key in KEYWORDS_MAP.items():
                        if re.search(pat, row_str):
                            if val is not None and (result[key] is None or result[key] == "N.D."):
                                result[key] = val
                            break
                    
                    # PBB/PBDE 加總
                    if re.search(PBB_SUBITEMS, row_str):
                        pbb_found = True
                        if isinstance(val, float): pbb_sum += val
                    if re.search(PBDE_SUBITEMS, row_str):
                        pbde_found = True
                        if isinstance(val, float): pbde_sum += val

    if "PFAS" in full_text or "Per- and polyfluoroalkyl" in full_text: result["PFAS"] = "REPORT"
    result["PBBs"] = pbb_sum if pbb_found and pbb_sum > 0 else "N.D."
    result["PBDEs"] = pbde_sum if pbde_found and pbde_sum > 0 else "N.D."
    return result

# --- CTI Parser ---
def parse_cti(pdf_obj, full_text, first_page_text):
    result = {k: None for k in KEYWORDS_MAP.values()}
    result['PFAS'] = ""
    
    # 1. 日期抓取 (CTI 優先找頁尾)
    lines = first_page_text.split('\n')
    date_pat = r"(?i)Date\s*[:：]?\s*([A-Za-z]{3}\.?\s*\d{1,2},?\s*\d{4}|\d{4}[./]\d{1,2}[./]\d{1,2})"
    # 先掃後 25 行
    for line in lines[max(0, len(lines)-25):]:
        match = re.search(date_pat, line)
        if match: 
            result['DATE'] = standardize_date(match.group(1))
            break
    # 若無，掃前 20 行
    if not result.get('DATE'):
        for line in lines[:20]:
            match = re.search(date_pat, line)
            if match:
                result['DATE'] = standardize_date(match.group(1))
                break

    # 2. 表格數據 (Result 錨點法)
    pbb_sum = 0; pbde_sum = 0; pbb_found = False; pbde_found = False
    with pdfplumber.open(pdf_obj) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                if not table: continue
                header = table[0]
                # 定位 Result 欄位
                res_idx = -1
                for i, col in enumerate(header):
                    if col and re.search(r"(?i)(Result|结果)", str(col)):
                        res_idx = i
                        break
                # 若找不到 Result，找 MDL 左邊
                if res_idx == -1:
                    for i, col in enumerate(header):
                        if col and re.search(r"(?i)(MDL|Method\s*Detection)", str(col)):
                            res_idx = max(0, i - 1)
                            break
                if res_idx == -1: res_idx = 1 # Fallback

                for row in table[1:]:
                    if len(row) <= res_idx: continue
                    row_str = " ".join([str(c) for c in row if c]).replace("\n", " ")
                    val = clean_value(row[res_idx])
                    
                    for pat, key in KEYWORDS_MAP.items():
                        if re.search(pat, row_str):
                            if val is not None and (result[key] is None or result[key] == "N.D."):
                                result[key] = val
                            break

                    if re.search(PBB_SUBITEMS, row_str):
                        pbb_found = True
                        if isinstance(val, float): pbb_sum += val
                    if re.search(PBDE_SUBITEMS, row_str):
                        pbde_found = True
                        if isinstance(val, float): pbde_sum += val

    if "PFAS" in full_text: result["PFAS"] = "REPORT"
    result["PBBs"] = pbb_sum if pbb_found and pbb_sum > 0 else "N.D."
    result["PBDEs"] = pbde_sum if pbde_found and pbde_sum > 0 else "N.D."
    return result

# --- Intertek Parser ---
def parse_intertek(pdf_obj, full_text, first_page_text):
    result = {k: None for k in KEYWORDS_MAP.values()}
    result['PFAS'] = ""

    # 1. 日期抓取 (Intertek 頁首)
    lines = first_page_text.split('\n')
    date_pat = r"(?i)(?:Date|Issue Date|발행일자)\s*[:：]?\s*([A-Za-z]{3}\s+\d{1,2},?\s*\d{4}|\d{4}[.\s]+\d{1,2}[.\s]+\d{1,2})"
    for line in lines[:25]:
        match = re.search(date_pat, line)
        if match:
            result['DATE'] = standardize_date(match.group(1))
            break
            
    # 2. 表格數據 (MDL 錨點 + 左右探測)
    pbb_sum = 0; pbde_sum = 0; pbb_found = False; pbde_found = False
    with pdfplumber.open(pdf_obj) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                if not table: continue
                header = table[0]
                
                # 找 MDL/RL 錨點
                mdl_idx = -1
                for i, col in enumerate(header):
                    if col and re.search(r"(?i)(MDL|RL|Limit of Detection|검출한계)", str(col)):
                        mdl_idx = i
                        break
                
                if mdl_idx == -1: continue # 找不到錨點跳過
                
                # 左右探測 Result
                res_idx = -1
                # 先看表頭
                if mdl_idx + 1 < len(header) and re.search(r"(?i)(Result|결과)", str(header[mdl_idx+1])):
                    res_idx = mdl_idx + 1
                elif mdl_idx - 1 >= 0 and re.search(r"(?i)(Result|结果)", str(header[mdl_idx-1])):
                    res_idx = mdl_idx - 1
                
                # 若表頭無 Result，偷看第一行數據
                if res_idx == -1 and len(table) > 1:
                    row1 = table[1]
                    val_left = clean_value(row1[mdl_idx-1]) if mdl_idx > 0 else None
                    val_right = clean_value(row1[mdl_idx+1]) if mdl_idx + 1 < len(row1) else None
                    
                    if val_left in ["N.D.", "NEGATIVE"] or isinstance(val_left, float):
                        res_idx = mdl_idx - 1
                    elif val_right in ["N.D.", "NEGATIVE"] or isinstance(val_right, float):
                        res_idx = mdl_idx + 1

                if res_idx == -1: continue

                for row in table[1:]:
                    if len(row) <= res_idx: continue
                    row_str = " ".join([str(c) for c in row if c]).replace("\n", " ")
                    val = clean_value(row[res_idx])
                    
                    for pat, key in KEYWORDS_MAP.items():
                        if re.search(pat, row_str):
                            if val is not None and (result[key] is None or result[key] == "N.D."):
                                result[key] = val
                            break

                    if re.search(PBB_SUBITEMS, row_str):
                        pbb_found = True
                        if isinstance(val, float): pbb_sum += val
                    if re.search(PBDE_SUBITEMS, row_str):
                        pbde_found = True
                        if isinstance(val, float): pbde_sum += val

    if "PFAS" in full_text: result["PFAS"] = "REPORT"
    result["PBBs"] = pbb_sum if pbb_found and pbb_sum > 0 else "N.D."
    result["PBDEs"] = pbde_sum if pbde_found and pbde_sum > 0 else "N.D."
    return result

# ==========================================
# 4. 主控邏輯 (Dispatcher & Aggregation)
# ==========================================

def identify_vendor(first_page_text):
    text = first_page_text.lower()
    if "intertek" in text: return "INTERTEK"
    if "cti" in text or "华测" in text: return "CTI"
    if "sgs" in text: return "SGS"
    return "UNKNOWN"

def aggregate_reports(valid_results):
    """
    執行「同料號最差情境」聚合：
    1. FILENAME: 取 Pb 最高者 (同分取日期新)
    2. DATE: 取所有報告中最新者
    3. VALUES: 取每個欄位的最高優先級值
    """
    if not valid_results: return pd.DataFrame()

    final_row = {k: None for k in TARGET_ITEMS}
    
    # 1. 決定代表檔名 (Pb 霸主)
    # 排序鍵: (Pb 優先級, Pb 數值, 日期字串) -> 由大到小
    sorted_by_pb = sorted(
        valid_results, 
        key=lambda x: (
            get_value_priority(x.get("Pb"))[0], # 優先級
            get_value_priority(x.get("Pb"))[1], # 數值
            x.get("DATE", "1900/01/01")         # 日期
        ), 
        reverse=True
    )
    final_row["FILENAME"] = sorted_by_pb[0]["FILENAME"]

    # 2. 決定最新日期
    all_dates = [r.get("DATE", "1900/01/01") for r in valid_results if r.get("DATE")]
    final_row["DATE"] = max(all_dates) if all_dates else "Unknown"

    # 3. 決定各數值 (最差情境)
    for key in TARGET_ITEMS:
        if key in ["FILENAME", "DATE"]: continue
        
        best_val = None
        for res in valid_results:
            val = res.get(key)
            # 比較優先級
            if get_value_priority(val) > get_value_priority(best_val):
                best_val = val
        
        final_row[key] = best_val

    return pd.DataFrame([final_row])

# ==========================================
# 5. Streamlit App
# ==========================================

def main():
    st.set_page_config(page_title="化學報告自動彙整系統", layout="wide")
    st.title("🧪 化學測試報告自動彙整系統")
    st.markdown("""
    **功能：** 支援 SGS / CTI / Intertek 報告。自動識別廠商、提取數據、並將多份報告合併為 **「最嚴格結果」**。
    """)

    uploaded_files = st.file_uploader("請上傳 PDF 報告 (可多選，視為同一料號)", type="pdf", accept_multiple_files=True)

    if uploaded_files:
        if st.button("開始分析"):
            valid_results = []
            bucket_unknown = []
            bucket_error = []
            
            progress_bar = st.progress(0)
            status_text = st.empty()

            for i, file in enumerate(uploaded_files):
                status_text.text(f"正在處理: {file.name}...")
                try:
                    # 1. 讀取 PDF
                    with pdfplumber.open(file) as pdf:
                        if len(pdf.pages) == 0:
                            bucket_error.append(file.name)
                            continue
                        
                        first_page_text = pdf.pages[0].extract_text()
                        if not first_page_text:
                            bucket_error.append(file.name) # 可能是圖片檔
                            continue
                        
                        # 為了全文搜索，讀取所有頁面
                        full_text = ""
                        for page in pdf.pages:
                            txt = page.extract_text()
                            if txt: full_text += txt + "\n"

                    # 2. 識別廠商
                    vendor = identify_vendor(first_page_text)
                    
                    # 3. 分流處理
                    data = None
                    if vendor == "SGS":
                        data = parse_sgs(file, full_text, first_page_text)
                    elif vendor == "CTI":
                        data = parse_cti(file, full_text, first_page_text)
                    elif vendor == "INTERTEK":
                        data = parse_intertek(file, full_text, first_page_text)
                    else:
                        bucket_unknown.append(file.name)
                        continue

                    if data:
                        data["FILENAME"] = file.name # 補上檔名
                        valid_results.append(data)
                    else:
                        bucket_error.append(file.name) # 解析失敗

                except Exception as e:
                    print(f"Error processing {file.name}: {e}")
                    bucket_error.append(file.name)
                
                progress_bar.progress((i + 1) / len(uploaded_files))

            status_text.text("分析完成！")

            # === 顯示結果區 (成功區) ===
            if valid_results:
                df_final = aggregate_reports(valid_results)
                
                # 欄位排序
                cols = ["FILENAME", "DATE"] + [c for c in TARGET_ITEMS if c not in ["FILENAME", "DATE"]]
                df_final = df_final[cols]
                
                st.success("✅ 分析成功！以下為合併後的最終結果：")
                st.dataframe(df_final)
                
                # 下載按鈕
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    df_final.to_excel(writer, index=False, sheet_name='Summary')
                output.seek(0)
                
                st.download_button(
                    label="📥 下載 Excel 報告",
                    data=output,
                    file_name=f"Merged_Report_{pd.Timestamp.now().strftime('%Y%m%d')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            else:
                st.warning("沒有成功提取到任何有效數據。")

            # === 顯示異常區 (警告區) ===
            if bucket_unknown or bucket_error:
                st.divider()
                st.subheader("⚠️ 異常檔案報告")
                
                if bucket_unknown:
                    with st.expander("🟡 未識別出廠商的檔案 (已跳過)", expanded=True):
                        for name in bucket_unknown:
                            st.write(f"- {name}")
                            
                if bucket_error:
                    with st.expander("🔴 無法讀取或純圖片的檔案 (已跳過)", expanded=True):
                        for name in bucket_error:
                            st.write(f"- {name}")

if __name__ == "__main__":
    main()
