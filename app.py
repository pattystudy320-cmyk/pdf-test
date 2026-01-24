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
    # 處理 CTI/Intertek 韓文版的 2025. 06. 16. (去除點與多餘空格)
    clean_str = re.sub(r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.?", r"\1/\2/\3", clean_str)
    try:
        dt = parser.parse(clean_str, fuzzy=True)
        return dt.strftime("%Y/%m/%d")
    except:
        return "1900/01/01"

def clean_value(val_str):
    """清理數值，保留 N.D. 與 NEGATIVE"""
    if not val_str: return None
    val_str = str(val_str).strip()
    
    # 排除 CAS No. 或長字串 (SGS 修正邏輯)
    if re.search(r"\d{2,}-\d{2,}-\d{2,}", val_str): return None # 這是 CAS No
    if len(val_str) > 20 and not re.search(r"(negative|positive)", val_str, re.I): return None # 可能是測試方法描述

    if re.search(r"(?i)(n\.?d\.?|not detected|<)", val_str): return "N.D."
    if re.search(r"(?i)(negative|阴性|陰性)", val_str): return "NEGATIVE"
    if re.search(r"(?i)(positive|阳性|陽性)", val_str): return "POSITIVE"
    
    match = re.search(r"(\d+\.?\d*)", val_str)
    if match: return float(match.group(1))
    return None

def get_value_priority(val):
    """數值優先級：數字 > NEGATIVE > N.D. > None"""
    if isinstance(val, (int, float)): return (3, val)
    if val in ["NEGATIVE", "POSITIVE"]: return (2, 0)
    if val == "N.D.": return (1, 0)
    return (0, 0)

# ==========================================
# 3. 廠商專屬解析模組
# ==========================================

# --- SGS Parser (修正版：排除 CAS No.) ---
def parse_sgs(pdf_obj, full_text, first_page_text):
    result = {k: None for k in KEYWORDS_MAP.values()}
    result['PFAS'] = ""
    result['DATE'] = ""

    # 1. 日期抓取
    date_patterns = [r"(?i)Date\s*[:：]", r"日期\s*[:：]", r"日期\(Date\)\s*[:：]"]
    for line in first_page_text.split('\n')[:25]:
        for pat in date_patterns:
            if re.search(pat, line):
                match = re.search(r"(20\d{2}[-./年]\s?\d{1,2}[-./月]\s?\d{1,2}|[A-Za-z]{3}\s+\d{1,2}[,\s]+\d{4})", line)
                if match: result['DATE'] = standardize_date(match.group(0))
                break
        if result['DATE']: break

    # 2. 表格數據 (黑名單擴充 + 特徵鎖定)
    pbb_sum = 0; pbde_sum = 0; pbb_found = False; pbde_found = False
    
    with pdfplumber.open(pdf_obj) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                if not table: continue
                header = table[0]
                
                # A. 黑名單過濾 (新增 CAS, Item)
                blacklist = [
                    r"(?i)Limit", r"(?i)限值", 
                    r"(?i)MDL", r"(?i)Method", r"(?i)方法", 
                    r"(?i)Unit", r"(?i)单位", r"(?i)單位",
                    r"(?i)CAS", r"(?i)Item", r"(?i)项目", r"(?i)項目"
                ]
                
                potential_cols = []
                for i, col in enumerate(header):
                    col_str = str(col).strip()
                    if not any(re.search(b, col_str) for b in blacklist):
                        potential_cols.append(i)
                
                if not potential_cols: continue

                # B. 白名單鎖定 (優先找 A1, No.1)
                result_idx = potential_cols[-1] # 預設取過濾後的最後一欄
                for idx in potential_cols:
                    col_text = str(header[idx]).strip()
                    if re.search(r"(?i)(A\d+|No\.|Result|結果)", col_text):
                        result_idx = idx
                        break
                
                # C. 數據提取
                for row in table[1:]:
                    if len(row) <= result_idx: continue
                    row_str = " ".join([str(c) for c in row if c]).replace("\n", " ")
                    val = clean_value(row[result_idx])
                    
                    # 匹配 PFAS 嚴格模式 (表格內掃描)
                    if "PFAS" in row_str and not result['PFAS']:
                        result['PFAS'] = "REPORT"

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

    # D. PFAS 首頁掃描
    if "PFAS" in first_page_text or "Per- and polyfluoroalkyl" in first_page_text:
        result["PFAS"] = "REPORT"
        
    result["PBBs"] = pbb_sum if pbb_found and pbb_sum > 0 else "N.D."
    result["PBDEs"] = pbde_sum if pbde_found and pbde_sum > 0 else "N.D."
    return result

# --- CTI Parser (修正版：避開測試方法表) ---
def parse_cti(pdf_obj, full_text, first_page_text):
    result = {k: None for k in KEYWORDS_MAP.values()}
    result['PFAS'] = ""
    result['DATE'] = ""
    
    # 1. 日期抓取 (優先掃描頁尾)
    lines = first_page_text.split('\n')
    date_pat = r"(?i)Date\s*[:：]?\s*([A-Za-z]{3}\.?\s*\d{1,2},?\s*\d{4}|\d{4}[./]\d{1,2}[./]\d{1,2})"
    for line in lines[max(0, len(lines)-25):]: # 頁尾
        match = re.search(date_pat, line)
        if match: 
            result['DATE'] = standardize_date(match.group(1))
            break
    if not result['DATE']: # 頁首備援
        for line in lines[:20]:
            match = re.search(date_pat, line)
            if match:
                result['DATE'] = standardize_date(match.group(1))
                break

    # 2. 表格數據 (章節錨點 + 表頭驗證)
    pbb_sum = 0; pbde_sum = 0; pbb_found = False; pbde_found = False
    result_section_started = False

    with pdfplumber.open(pdf_obj) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            
            # A. 章節鎖定：看到 "Test Result" 才開啟
            if re.search(r"(?i)(Test Result|检测结果|檢測結果)", page_text):
                result_section_started = True
            
            if not result_section_started: continue 

            tables = page.extract_tables()
            for table in tables:
                if not table: continue
                header = table[0]
                header_str = " ".join([str(c) for c in header if c])

                # B. 表頭驗證：必須包含 MDL/Limit，排除 Test Method 表
                if not re.search(r"(?i)(MDL|Limit|RL|LOQ|Method\s*Detection)", header_str):
                    continue

                # C. 定位 Result 欄位
                res_idx = -1
                for i, col in enumerate(header):
                    if col and re.search(r"(?i)(Result|结果)", str(col)):
                        res_idx = i
                        break
                if res_idx == -1: # 找不到 Result，找 MDL 左邊
                    for i, col in enumerate(header):
                        if col and re.search(r"(?i)(MDL|LOQ)", str(col)):
                            res_idx = max(0, i - 1)
                            break
                if res_idx == -1: res_idx = 1 # Fallback

                for row in table[1:]:
                    if len(row) <= res_idx: continue
                    row_str = " ".join([str(c) for c in row if c]).replace("\n", " ")
                    val = clean_value(row[res_idx])
                    
                    # PFAS 嚴格掃描
                    if "PFAS" in row_str and not result['PFAS']:
                        result['PFAS'] = "REPORT"

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

    if "PFAS" in first_page_text: result["PFAS"] = "REPORT"
    result["PBBs"] = pbb_sum if pbb_found and pbb_sum > 0 else "N.D."
    result["PBDEs"] = pbde_sum if pbde_found and pbde_sum > 0 else "N.D."
    return result

# --- Intertek Parser (修正版：數據特徵鎖定) ---
def parse_intertek(pdf_obj, full_text, first_page_text):
    result = {k: None for k in KEYWORDS_MAP.values()}
    result['PFAS'] = ""
    result['DATE'] = ""

    # 1. 日期抓取 (韓文/英文)
    lines = first_page_text.split('\n')
    date_pat = r"(?i)(?:Date|Issue Date|발행일자)\s*[:：]?\s*([A-Za-z]{3}\s+\d{1,2},?\s*\d{4}|\d{4}[.\s]+\d{1,2}[.\s]+\d{1,2})"
    for line in lines[:25]:
        match = re.search(date_pat, line)
        if match:
            result['DATE'] = standardize_date(match.group(1))
            break
            
    # 2. 表格數據 (MDL 錨點 + 數據特徵鎖定)
    pbb_sum = 0; pbde_sum = 0; pbb_found = False; pbde_found = False
    with pdfplumber.open(pdf_obj) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                if not table: continue
                header = table[0]
                
                # A. 找 MDL 錨點
                mdl_idx = -1
                for i, col in enumerate(header):
                    if col and re.search(r"(?i)(MDL|RL|Limit of Detection|검출한계)", str(col)):
                        mdl_idx = i
                        break
                if mdl_idx == -1: continue 
                
                # B. 數據特徵鎖定 (偷看數據來決定 Result 在左邊還是右邊)
                res_idx = -1
                if len(table) > 1:
                    row1 = table[1]
                    left_val = str(row1[mdl_idx-1]) if mdl_idx > 0 else ""
                    right_val = str(row1[mdl_idx+1]) if mdl_idx + 1 < len(row1) else ""
                    
                    # 誰有 N.D. 誰就是老大
                    if re.search(r"(?i)(N\.?D|Negative|<)", left_val):
                        res_idx = mdl_idx - 1
                    elif re.search(r"(?i)(N\.?D|Negative|<)", right_val):
                        res_idx = mdl_idx + 1
                    # 若都沒 N.D.，找表頭
                    elif mdl_idx + 1 < len(header) and re.search(r"(?i)(Result|결과)", str(header[mdl_idx+1])):
                        res_idx = mdl_idx + 1
                    elif mdl_idx - 1 >= 0 and re.search(r"(?i)(Result|结果)", str(header[mdl_idx-1])):
                        res_idx = mdl_idx - 1
                
                if res_idx == -1: continue

                # C. 數據提取
                for row in table[1:]:
                    if len(row) <= res_idx: continue
                    row_str = " ".join([str(c) for c in row if c]).replace("\n", " ")
                    val = clean_value(row[res_idx])
                    
                    if "PFAS" in row_str and not result['PFAS']:
                        result['PFAS'] = "REPORT"

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

    if "PFAS" in first_page_text: result["PFAS"] = "REPORT"
    result["PBBs"] = pbb_sum if pbb_found and pbb_sum > 0 else "N.D."
    result["PBDEs"] = pbde_sum if pbde_found and pbde_sum > 0 else "N.D."
    return result

# ==========================================
# 4. 主控邏輯
# ==========================================

def identify_vendor(first_page_text):
    text = first_page_text.lower()
    if "intertek" in text: return "INTERTEK"
    if "cti" in text or "华测" in text: return "CTI"
    if "sgs" in text: return "SGS"
    return "UNKNOWN"

def aggregate_reports(valid_results):
    if not valid_results: return pd.DataFrame()

    final_row = {k: None for k in TARGET_ITEMS}
    
    # 1. 決定代表檔名 (Pb 最高者)
    sorted_by_pb = sorted(
        valid_results, 
        key=lambda x: (
            get_value_priority(x.get("Pb"))[0],
            get_value_priority(x.get("Pb"))[1],
            x.get("DATE", "1900/01/01")
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
            if get_value_priority(val) > get_value_priority(best_val):
                best_val = val
        
        final_row[key] = best_val

    return pd.DataFrame([final_row])

# ==========================================
# 5. Streamlit App
# ==========================================

def main():
    st.set_page_config(page_title="化學報告自動彙整系統 (SGS/CTI/Intertek)", layout="wide")
    st.title("🧪 化學測試報告自動彙整系統 v2.0")
    st.markdown("""
    **支援：** SGS (CAS 排除)、CTI (方法排除)、Intertek (韓文/動態欄位)。
    **邏輯：** 自動識別廠商 > 提取數據 > 多報告聚合 (取最大值) > 產出 Excel。
    """)

    uploaded_files = st.file_uploader("請上傳 PDF 報告", type="pdf", accept_multiple_files=True)

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
                    with pdfplumber.open(file) as pdf:
                        if len(pdf.pages) == 0:
                            bucket_error.append(file.name)
                            continue
                        
                        first_page_text = pdf.pages[0].extract_text()
                        if not first_page_text:
                            bucket_error.append(file.name)
                            continue
                        
                        full_text = ""
                        for page in pdf.pages:
                            txt = page.extract_text()
                            if txt: full_text += txt + "\n"

                    vendor = identify_vendor(first_page_text)
                    
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
                        data["FILENAME"] = file.name
                        valid_results.append(data)
                    else:
                        bucket_error.append(file.name)

                except Exception as e:
                    bucket_error.append(f"{file.name} ({str(e)})")
                
                progress_bar.progress((i + 1) / len(uploaded_files))

            status_text.text("分析完成！")

            if valid_results:
                df_final = aggregate_reports(valid_results)
                cols = ["FILENAME", "DATE"] + [c for c in TARGET_ITEMS if c not in ["FILENAME", "DATE"]]
                df_final = df_final[cols]
                
                st.success("✅ 分析成功！")
                st.dataframe(df_final)
                
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    df_final.to_excel(writer, index=False, sheet_name='Summary')
                output.seek(0)
                
                st.download_button(
                    label="📥 下載 Excel",
                    data=output,
                    file_name=f"Merged_Report_{pd.Timestamp.now().strftime('%Y%m%d')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            else:
                st.warning("沒有成功提取到任何有效數據。")

            if bucket_unknown or bucket_error:
                st.divider()
                st.subheader("⚠️ 異常檔案報告")
                if bucket_unknown:
                    with st.expander("🟡 未識別出廠商 (已跳過)", expanded=True):
                        for name in bucket_unknown: st.write(f"- {name}")
                if bucket_error:
                    with st.expander("🔴 無法讀取或錯誤 (已跳過)", expanded=True):
                        for name in bucket_error: st.write(f"- {name}")

if __name__ == "__main__":
    main()
