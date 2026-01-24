import streamlit as st
import pdfplumber
import pandas as pd
import re
import io
import os
from dateutil import parser

# ==========================================
# 1. 全局配置 (Global Settings)
# ==========================================

# 最終輸出的欄位順序
TARGET_ITEMS = [
    "Pb", "Cd", "Hg", "Cr6+", "PBBs", "PBDEs",
    "DEHP", "DBP", "BBP", "DIBP",
    "F", "Cl", "Br", "I",
    "PFOS", "PFOA", "PFAS", "DATE", "FILENAME"
]

# 化學物質關鍵字映射 (Regex -> 統一欄位名)
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
    r"(?i)\b(PFOS|Perfluorooctane\s*sulfonates)\b": "PFOS",
    r"(?i)\b(PFOA|Perfluorooctanoic\s*acid)\b": "PFOA"
}

# PBBs/PBDEs 加總用關鍵字
PBB_SUBITEMS = r"(?i)(Monobromobiphenyl|Dibromobiphenyl|Tribromobiphenyl|Tetrabromobiphenyl|Pentabromobiphenyl|Hexabromobiphenyl|Heptabromobiphenyl|Octabromobiphenyl|Nonabromobiphenyl|Decabromobiphenyl)"
PBDE_SUBITEMS = r"(?i)(Monobromodiphenyl ether|Dibromodiphenyl ether|Tribromodiphenyl ether|Tetrabromodiphenyl ether|Pentabromodiphenyl ether|Hexabromodiphenyl ether|Heptabromodiphenyl ether|Octabromodiphenyl ether|Nonabromodiphenyl ether|Decabromodiphenyl ether)"

# ==========================================
# 2. 通用工具函數 (Helper Functions)
# ==========================================

def standardize_date(date_str):
    """
    標準化日期格式為 YYYY/MM/DD
    支援：2024. 10. 17. (韓系), 2024年10月10日 (中系), Jan 08, 2025 (英系)
    """
    if not date_str: return "1900/01/01"
    clean_str = str(date_str).strip()
    
    # 處理中文與特殊符號
    clean_str = clean_str.replace("年", "/").replace("月", "/").replace("日", "")
    # 處理點分隔 (2024. 10. 17.) -> 2024/10/17
    clean_str = re.sub(r"(\d{4})[\.\s]+(\d{1,2})[\.\s]+(\d{1,2})\.?", r"\1/\2/\3", clean_str)
    
    try:
        dt = parser.parse(clean_str, fuzzy=True)
        return dt.strftime("%Y/%m/%d")
    except:
        return "1900/01/01" # 解析失敗回傳預設舊日期

def clean_value(val_str):
    """
    數據清洗：
    - 轉為 N.D. / NEGATIVE / POSITIVE
    - 提取純數字 (Float)
    - 過濾掉 CAS No (如 123-45-6) 與長文字描述
    """
    if not val_str: return None
    val_str = str(val_str).strip()
    
    # 排除 CAS No. (格式: 數字-數字-數字)
    if re.search(r"\b\d{2,}-\d{2,}-\d{2,}\b", val_str): 
        return None
    
    # 排除過長的非結果描述 (例如測試方法名稱)
    if len(val_str) > 20 and not re.search(r"(negative|positive|n\.d\.)", val_str, re.I):
        return None

    # 標準化定性結果
    if re.search(r"(?i)(n\.?d\.?|not detected|<)", val_str): return "N.D."
    if re.search(r"(?i)(negative|阴性|陰性)", val_str): return "NEGATIVE"
    if re.search(r"(?i)(positive|阳性|陽性)", val_str): return "POSITIVE"
    
    # 提取數字
    match = re.search(r"(\d+\.?\d*)", val_str)
    if match: 
        return float(match.group(1))
    
    return None

def get_value_priority(val):
    """
    數值優先級 (用於 Worst-case 比較)：
    Level 3: 實測數字 (越大越優先)
    Level 2: 定性結果 (NEGATIVE/POSITIVE)
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

# --- [SGS Parser] 特徵權重計分法 ---
def parse_sgs(pdf_obj, full_text, first_page_text):
    result = {k: None for k in KEYWORDS_MAP.values()}
    result['PFAS'] = ""
    result['DATE'] = ""

    # 1. 日期抓取 (SGS 頁首優先)
    date_patterns = [r"(?i)Date\s*[:：]", r"日期\s*[:：]", r"日期\(Date\)\s*[:：]"]
    for line in first_page_text.split('\n')[:25]:
        for pat in date_patterns:
            if re.search(pat, line):
                # 抓取 YYYY/MM/DD 或 Mon DD, YYYY
                match = re.search(r"(20\d{2}[-./年]\s?\d{1,2}[-./月]\s?\d{1,2}|[A-Za-z]{3}\s+\d{1,2}[,\s]+\d{4})", line)
                if match: result['DATE'] = standardize_date(match.group(0))
                break
        if result['DATE']: break

    # 2. 表格數據 (權重計分法)
    pbb_sum = 0; pbde_sum = 0; pbb_found = False; pbde_found = False
    
    with pdfplumber.open(pdf_obj) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                if not table: continue
                header = table[0]
                
                # A. 欄位計分 (Scoring)
                col_scores = {}
                for idx, col in enumerate(header):
                    col_str = str(col).strip()
                    score = 0
                    
                    # 扣分項 (Blacklist)
                    if re.search(r"(?i)(Limit|限值|MDL|Method|方法|Unit|单位|單位|CAS|Item|项目)", col_str):
                        score -= 1000
                    
                    # 加分項 (Header Whitelist)
                    if re.search(r"(?i)(Result|结果|No\.|A\d+)", col_str):
                        score += 50
                    
                    # 偷看數據特徵 (Data Peeking)
                    if len(table) > 1:
                        sample_val = str(table[1][idx]).strip()
                        if re.search(r"(?i)(N\.?D|Negative|<)", sample_val): # 結果欄特徵
                            score += 100
                        elif re.search(r"\d+-\d+-\d+", sample_val): # CAS No 特徵
                            score -= 1000
                        elif re.search(r"^\d+$", sample_val) and not re.search(r"N\.?D", sample_val, re.I): 
                            # 純數字且無 N.D. (可能是 Limit 或 MDL)
                            score -= 50 
                    
                    col_scores[idx] = score

                # 選出分數最高的欄位
                if not col_scores: continue
                best_col_idx = max(col_scores, key=col_scores.get)
                
                # 若最高分仍是負的，代表這張表可能沒結果，跳過
                if col_scores[best_col_idx] < 0: continue

                # B. 數據提取
                for row in table[1:]:
                    if len(row) <= best_col_idx: continue
                    row_str = " ".join([str(c) for c in row if c]).replace("\n", " ")
                    val = clean_value(row[best_col_idx])
                    
                    # PFAS 嚴格模式 (表格內容掃描)
                    if "PFAS" in row_str and not result['PFAS']:
                        result['PFAS'] = "REPORT"

                    for pat, key in KEYWORDS_MAP.items():
                        if re.search(pat, row_str):
                            # 更新邏輯: 若新值是數字，或原值是空/ND，則更新
                            if val is not None:
                                current_val = result[key]
                                if current_val is None or current_val == "N.D.":
                                    result[key] = val
                                elif isinstance(val, (int, float)) and isinstance(current_val, (int, float)):
                                    result[key] = max(val, current_val)
                            break
                    
                    if re.search(PBB_SUBITEMS, row_str):
                        pbb_found = True
                        if isinstance(val, (int, float)): pbb_sum += val
                    if re.search(PBDE_SUBITEMS, row_str):
                        pbde_found = True
                        if isinstance(val, (int, float)): pbde_sum += val

    # C. PFAS 首頁掃描
    if "PFAS" in first_page_text or "Per- and polyfluoroalkyl" in first_page_text:
        result["PFAS"] = "REPORT"
        
    result["PBBs"] = pbb_sum if pbb_found and pbb_sum > 0 else "N.D."
    result["PBDEs"] = pbde_sum if pbde_found and pbde_sum > 0 else "N.D."
    return result

# --- [CTI Parser] 雙重鎖定 + 頁尾日期 ---
def parse_cti(pdf_obj, full_text, first_page_text):
    result = {k: None for k in KEYWORDS_MAP.values()}
    result['PFAS'] = ""
    result['DATE'] = ""
    
    # 1. 日期抓取 (優先掃描頁尾，避開 Received Date)
    lines = first_page_text.split('\n')
    # 尋找 "Date:" 且該行不含 "Received"
    date_pat = r"(?i)Date\s*[:：]?\s*([A-Za-z]{3}\.?\s*\d{1,2},?\s*\d{4}|\d{4}[./]\d{1,2}[./]\d{1,2})"
    
    # 從最後一行往上掃描 (Footer)
    for line in reversed(lines):
        if re.search(r"(?i)Received", line): continue # 避開送樣日
        match = re.search(date_pat, line)
        if match:
            result['DATE'] = standardize_date(match.group(1))
            break
            
    # 2. 表格數據 (章節鎖定 + 表頭驗證)
    pbb_sum = 0; pbde_sum = 0; pbb_found = False; pbde_found = False
    result_section_started = False # 章節開關

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

                # B. 表頭驗證：必須包含 MDL/Limit/LOQ，排除 Method 表
                if not re.search(r"(?i)(MDL|Limit|RL|LOQ|Method\s*Detection)", header_str):
                    continue

                # C. 定位 Result 欄位 (錨點法)
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
                    
                    if "PFAS" in row_str and not result['PFAS']:
                        result['PFAS'] = "REPORT"

                    for pat, key in KEYWORDS_MAP.items():
                        if re.search(pat, row_str):
                            if val is not None:
                                current_val = result[key]
                                if current_val is None or current_val == "N.D.":
                                    result[key] = val
                                elif isinstance(val, (int, float)) and isinstance(current_val, (int, float)):
                                    result[key] = max(val, current_val)
                            break

                    if re.search(PBB_SUBITEMS, row_str):
                        pbb_found = True
                        if isinstance(val, (int, float)): pbb_sum += val
                    if re.search(PBDE_SUBITEMS, row_str):
                        pbde_found = True
                        if isinstance(val, (int, float)): pbde_sum += val

    if "PFAS" in first_page_text: result["PFAS"] = "REPORT"
    result["PBBs"] = pbb_sum if pbb_found and pbb_sum > 0 else "N.D."
    result["PBDEs"] = pbde_sum if pbde_found and pbde_sum > 0 else "N.D."
    return result

# --- [Intertek Parser] N.D. 導航 + 韓文支援 ---
def parse_intertek(pdf_obj, full_text, first_page_text):
    result = {k: None for k in KEYWORDS_MAP.values()}
    result['PFAS'] = ""
    result['DATE'] = ""

    # 1. 日期抓取 (含韓文支援)
    lines = first_page_text.split('\n')
    # 支援: Date, Issue Date, 발행일자(發行日)
    date_pat = r"(?i)(?:Date|Issue Date|발행일자)\s*[:：]?\s*([A-Za-z]{3}\s+\d{1,2},?\s*\d{4}|\d{4}[.\s]+\d{1,2}[.\s]+\d{1,2})"
    for line in lines[:25]:
        match = re.search(date_pat, line)
        if match:
            result['DATE'] = standardize_date(match.group(1))
            break
            
    # 2. 表格數據 (MDL 錨點 + N.D. 導航)
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
                
                # B. N.D. 導航 (偷看數據決定 Result 在左邊還是右邊)
                res_idx = -1
                if len(table) > 1:
                    row1 = table[1]
                    left_val = str(row1[mdl_idx-1]) if mdl_idx > 0 else ""
                    right_val = str(row1[mdl_idx+1]) if mdl_idx + 1 < len(row1) else ""
                    
                    # 誰有 N.D./Negative 誰就是結果
                    if re.search(r"(?i)(N\.?D|Negative|<)", left_val):
                        res_idx = mdl_idx - 1
                    elif re.search(r"(?i)(N\.?D|Negative|<)", right_val):
                        res_idx = mdl_idx + 1
                    # 否則看表頭
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
                            if val is not None:
                                current_val = result[key]
                                if current_val is None or current_val == "N.D.":
                                    result[key] = val
                                elif isinstance(val, (int, float)) and isinstance(current_val, (int, float)):
                                    result[key] = max(val, current_val)
                            break

                    if re.search(PBB_SUBITEMS, row_str):
                        pbb_found = True
                        if isinstance(val, (int, float)): pbb_sum += val
                    if re.search(PBDE_SUBITEMS, row_str):
                        pbde_found = True
                        if isinstance(val, (int, float)): pbde_sum += val

    if "PFAS" in first_page_text: result["PFAS"] = "REPORT"
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
    同料號聚合邏輯：
    1. 檔名：Pb 最高的檔案 (若 N.D. 則取日期最新的)
    2. 日期：所有報告中最新的日期
    3. 數值：取最大值 (Worst-case)
    """
    if not valid_results: return pd.DataFrame()

    final_row = {k: None for k in TARGET_ITEMS}
    
    # 1. 決定代表檔名
    sorted_by_pb = sorted(
        valid_results, 
        key=lambda x: (
            get_value_priority(x.get("Pb"))[0], # 優先級 (數字 > N.D.)
            get_value_priority(x.get("Pb"))[1], # 數值大小
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
            if get_value_priority(val) > get_value_priority(best_val):
                best_val = val
        
        final_row[key] = best_val

    return pd.DataFrame([final_row])

# ==========================================
# 5. Streamlit UI
# ==========================================

def main():
    st.set_page_config(page_title="化學報告自動彙整系統 v3.0", layout="wide")
    st.title("🧪 化學測試報告自動彙整系統 v3.0")
    st.markdown("""
    **功能特點：**
    * **廠商支援**：SGS (權重計分)、CTI (頁尾日期)、Intertek (韓文/N.D.導航)。
    * **PFAS 嚴格判斷**：僅當 "PFAS" 關鍵字出現時標記 REPORT。
    * **聚合邏輯**：多份報告自動合併，取最嚴格數值，顯示 Pb 最高者檔名。
    """)

    uploaded_files = st.file_uploader("請上傳 PDF 報告 (可多選)", type="pdf", accept_multiple_files=True)

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
                            bucket_error.append(f"{file.name} (無法讀取文字/圖片檔)")
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
                        bucket_error.append(f"{file.name} (解析失敗)")

                except Exception as e:
                    bucket_error.append(f"{file.name} (錯誤: {str(e)})")
                
                progress_bar.progress((i + 1) / len(uploaded_files))

            status_text.text("分析完成！")

            # --- 顯示結果 ---
            if valid_results:
                df_final = aggregate_reports(valid_results)
                
                # 欄位重新排序
                cols = ["FILENAME", "DATE"] + [c for c in TARGET_ITEMS if c not in ["FILENAME", "DATE"]]
                df_final = df_final[cols]
                
                st.success(f"✅ 成功處理 {len(valid_results)} 份報告，已合併為 1 筆結果：")
                st.dataframe(df_final)
                
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
                st.warning("未提取到有效數據。")

            # --- 顯示異常 ---
            if bucket_unknown or bucket_error:
                st.divider()
                st.subheader("⚠️ 異常報告清單")
                col1, col2 = st.columns(2)
                
                with col1:
                    if bucket_unknown:
                        st.warning(f"🟡 未識別廠商 ({len(bucket_unknown)})")
                        for name in bucket_unknown: st.write(f"- {name}")
                
                with col2:
                    if bucket_error:
                        st.error(f"🔴 處理失敗/圖片檔 ({len(bucket_error)})")
                        for name in bucket_error: st.write(f"- {name}")

if __name__ == "__main__":
    main()
