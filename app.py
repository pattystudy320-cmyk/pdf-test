import streamlit as st
import pdfplumber
import pandas as pd
import io
import re
from datetime import datetime

# =============================================================================
# 1. 共用設定與基礎函式
# =============================================================================

OUTPUT_COLUMNS = [
    "Pb", "Cd", "Hg", "Cr6+", "PBB", "PBDE", 
    "DEHP", "BBP", "DBP", "DIBP", 
    "PFOS", "PFAS", "F", "CL", "BR", "I", 
    "日期", "檔案名稱"
]

SIMPLE_KEYWORDS = {
    "Pb": ["Lead", "鉛", "Pb"],
    "Cd": ["Cadmium", "鎘", "Cd"],
    "Hg": ["Mercury", "汞", "Hg"],
    "Cr6+": ["Hexavalent Chromium", "六價鉻", "Cr(VI)", "Chromium VI"],
    "DEHP": ["DEHP", "Di(2-ethylhexyl) phthalate"],
    "BBP": ["BBP", "Butyl benzyl phthalate"],
    "DBP": ["DBP", "Dibutyl phthalate"],
    "DIBP": ["DIBP", "Diisobutyl phthalate"],
    "PFOS": ["Perfluorooctane sulfonates", "Perfluorooctane sulfonate", "PFOS"],
    "F": ["Fluorine", "氟"],
    "CL": ["Chlorine", "氯"],
    "BR": ["Bromine", "溴"],
    "I": ["Iodine", "碘"]
}

GROUP_KEYWORDS = {
    "PBB": ["Polybrominated Biphenyls", "PBBs", "Sum of PBBs", "多溴聯苯"],
    "PBDE": ["Polybrominated Diphenyl Ethers", "PBDEs", "Sum of PBDEs", "多溴二苯醚"]
}

def clean_text(text):
    if not text: return ""
    return str(text).replace('\n', ' ').strip()

def is_valid_date(dt):
    if 2000 <= dt.year <= 2030: return True
    return False

def extract_date_general(text):
    """通用日期提取 (適用於標準報告)"""
    lines = text.split('\n')
    candidates = []
    
    # 關鍵字加分
    bonus_kw = ["date:", "dated", "日期"]
    # 關鍵字扣分 (排除收件日、到期日)
    poison_kw = ["received", "expiry", "period", "started", "checked", "approved"]

    pat_ymd = r"(20\d{2})[\./-](0?[1-9]|1[0-2])[\./-](0?[1-9]|[12][0-9]|3[01])"
    pat_dmy = r"(0?[1-9]|[12][0-9]|3[01])[\s-]([a-zA-Z]{3,})[\s-](20\d{2})"
    
    for line in lines:
        line_lower = line.lower()
        score = 1
        if any(p in line_lower for p in poison_kw): score = -10
        if any(b in line_lower for b in bonus_kw): score = 10
        
        # 簡易清洗
        clean = line.replace("年", "/").replace("月", "/").replace("日", "")
        
        # 匹配 YMD
        matches = re.finditer(pat_ymd, clean)
        for m in matches:
            try:
                dt = datetime.strptime(f"{m.group(1)}-{m.group(2)}-{m.group(3)}", "%Y-%m-%d")
                if is_valid_date(dt): candidates.append((score, dt))
            except: pass
            
        # 匹配 DMY (25-Aug-2025)
        matches = re.finditer(pat_dmy, line)
        for m in matches:
            try:
                dt_str = f"{m.group(1)} {m.group(2)} {m.group(3)}"
                for fmt in ["%d %b %Y", "%d %B %Y"]:
                    try:
                        dt = datetime.strptime(dt_str, fmt)
                        if is_valid_date(dt): 
                            candidates.append((score, dt))
                            break
                    except: pass
            except: pass

    if not candidates: return None
    # 取最高分且最新的日期
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return candidates[0][1]

def extract_date_malaysia(text):
    """馬來西亞專用日期提取 (鎖定 REPORTED DATE)"""
    lines = text.split('\n')
    for line in lines:
        if "REPORTED DATE" in line.upper():
            # 排除 Job Ref 干擾
            if "JOB REF" in line.upper(): continue
            
            # 格式: 23-January-2025 或 23 Jan 2025
            pat = r"(0?[1-9]|[12][0-9]|3[01])[\s-]([a-zA-Z]{3,})[\s-](20\d{2})"
            match = re.search(pat, line)
            if match:
                dt_str = f"{match.group(1)} {match.group(2)} {match.group(3)}"
                for fmt in ["%d %B %Y", "%d %b %Y"]:
                    try:
                        dt = datetime.strptime(dt_str, fmt)
                        if is_valid_date(dt): return dt
                    except: pass
    return None

def extract_value_std(val_str):
    """標準數值清洗"""
    val = clean_text(val_str).lower()
    if not val: return None
    if val in ["-", "---", "n.a.", "/"]: return None
    
    if "n.d." in val or "not detected" in val or "negative" in val or "<" in val:
        return "N.D."
    
    # 提取數字
    match = re.search(r"^\d+(\.\d+)?", val.replace("mg/kg","").strip())
    if match:
        try:
            f = float(match.group(0))
            # 排除常見 MDL/Limit
            if f in [2.0, 5.0, 10.0, 50.0, 100.0, 1000.0]: return None 
            return match.group(0)
        except: pass
    return val # Return original if unsure

# =============================================================================
# 2. 引擎 A: 標準引擎 (基於 v60.3) - 用於 台灣/中國/其他
# =============================================================================

def process_standard(pdf, filename):
    data = {k: [] for k in OUTPUT_COLUMNS}
    data["檔案名稱"] = filename
    full_text = ""
    
    # 日期提取
    dates = []
    for p in pdf.pages[:3]:
        txt = p.extract_text() or ""
        full_text += txt + "\n"
        dt = extract_date_general(txt)
        if dt: dates.append(dt)
    
    if dates:
        data["日期"] = max(dates).strftime("%Y/%m/%d")

    # 表格模式
    for page in pdf.pages:
        tables = page.extract_tables()
        for table in tables:
            if not table or len(table) < 2: continue
            
            # 簡單欄位定位 (Item vs Result)
            item_idx, result_idx = -1, -1
            
            # 掃描表頭
            headers = [str(x).lower() for x in table[0] if x]
            for i, h in enumerate(headers):
                if "item" in h or "項目" in h: item_idx = i
                if "result" in h or "結果" in h: result_idx = i
            
            # 如果找不到 Result，嘗試找 MDL 右邊 (標準邏輯)
            if result_idx == -1:
                for i, h in enumerate(headers):
                    if "mdl" in h or "loq" in h:
                        if i + 1 < len(headers): result_idx = i + 1
                        break
            
            if item_idx == -1: continue # 連項目欄都找不到就跳過

            for row in table[1:]:
                if len(row) <= max(item_idx, result_idx): continue
                
                item_name = clean_text(row[item_idx])
                item_lower = item_name.lower()
                
                # 排除有機氟誤判 (SGS_4 修復關鍵)
                if "aminium" in item_lower or "piperazine" in item_lower or "sulfonate" in item_lower:
                    # 除非明確寫了 Fluorine (無鹵)
                    if "fluorine" not in item_lower: continue

                # 抓取數值
                raw_val = ""
                if result_idx != -1:
                    raw_val = row[result_idx]
                else:
                    # 掃描行尾
                    for cell in reversed(row):
                        if cell and ("n.d." in str(cell).lower() or re.match(r"\d+", str(cell))):
                            raw_val = cell
                            break
                
                val = extract_value_std(raw_val)
                if not val: continue

                # 匹配欄位
                for key, kws in SIMPLE_KEYWORDS.items():
                    if key == "F" and "perfluoro" in item_lower: continue # 再次防禦
                    
                    if any(kw.lower() in item_lower for kw in kws):
                        data[key].append(val)
                        break
                
                for key, kws in GROUP_KEYWORDS.items():
                    if any(kw.lower() in item_lower for kw in kws):
                        data[key].append(val)
                        break

    # 文字模式救援 (僅針對無鹵/PFOA)
    # v60.3 的保守救援: 只有當表格完全沒抓到時才啟動，且不使用寬鬆匹配
    ft_lower = full_text.lower()
    
    # 無鹵救援
    if not data["F"] and ("halogen" in ft_lower or "卤素" in ft_lower):
        # 簡單行掃描
        for line in full_text.split('\n'):
            l_lower = line.lower()
            if "fluorine" in l_lower and "n.d." in l_lower:
                data["F"].append("N.D.")
            if "chlorine" in l_lower and "n.d." in l_lower:
                data["CL"].append("N.D.")
            if "bromine" in l_lower and "n.d." in l_lower:
                data["BR"].append("N.D.")
            if "iodine" in l_lower and "n.d." in l_lower:
                data["I"].append("N.D.")

    return data

# =============================================================================
# 3. 引擎 B: 馬來西亞專用引擎 (v61.0 暴力版)
# =============================================================================

def process_malaysia(pdf, filename):
    data = {k: [] for k in OUTPUT_COLUMNS}
    data["檔案名稱"] = filename
    
    full_text = ""
    for p in pdf.pages:
        full_text += (p.extract_text() or "") + "\n"
        
    # 1. 日期提取
    dt = extract_date_malaysia(full_text)
    if dt: data["日期"] = dt.strftime("%Y/%m/%d")

    # 2. RoHS2 (表格 MDL 錨點法)
    for page in pdf.pages:
        tables = page.extract_tables()
        for table in tables:
            if not table or len(table) < 2: continue
            
            # 尋找 MDL 欄位 (特徵: 全是數字)
            mdl_col_idx = -1
            cols = len(table[0])
            for c in range(cols):
                num_count = 0
                total_count = 0
                for r in range(1, len(table)): # 跳過標題
                    val = clean_text(table[r][c])
                    if not val: continue
                    total_count += 1
                    # 檢查是否為常見 MDL 數字
                    if val in ["2", "5", "8", "50", "100"]:
                        num_count += 1
                
                if total_count > 0 and (num_count / total_count) > 0.5:
                    mdl_col_idx = c
                    break # 找到一個像 MDL 的就停
            
            # 如果找到 MDL，Result 就在左邊 (MDL-1)
            if mdl_col_idx > 0:
                result_col_idx = mdl_col_idx - 1
                
                # 開始暴力抓取
                for row in table:
                    if len(row) <= mdl_col_idx: continue
                    
                    # 判斷 Item (通常在第 0 欄，但也可能跟 Method 黏在一起)
                    # 策略: 把 row[0] 到 row[result_col_idx-1] 全部合起來當 Item Description
                    item_text = " ".join([str(x) for x in row[:result_col_idx] if x]).lower()
                    
                    # 提取 Result 欄位的內容
                    raw_res = str(row[result_col_idx])
                    
                    # 強力 Regex 清洗 (取出 N.D. 或數值)
                    # 排除 62321, IEC 等方法編號
                    final_val = None
                    
                    # 優先找 N.D.
                    if re.search(r"(?i)\bn\.?d\.?", raw_res):
                        final_val = "N.D."
                    else:
                        # 找數字 (排除方法編號)
                        nums = re.findall(r"\d+(?:\.\d+)?", raw_res)
                        for num in nums:
                            if num in ["62321", "2013", "2015", "2017"]: continue # 排除年份與標準號
                            if float(num) > 10000: continue # 排除大編號
                            final_val = num
                            break
                    
                    if not final_val: continue

                    # 匹配項目
                    for key, kws in SIMPLE_KEYWORDS.items():
                        if any(kw.lower() in item_text for kw in kws):
                            # Cd 防禦
                            if key == "Cd" and "hexabromocyclododecane" in item_text: continue
                            data[key].append(final_val)
                            break
                    for key, kws in GROUP_KEYWORDS.items():
                        if any(kw.lower() in item_text for kw in kws):
                            data[key].append(final_val)
                            break

    # 3. HF 無鹵 (區塊文字搜索法)
    # 針對無鹵數據換行嚴重的問題，放棄表格，直接掃文字區塊
    ft_lower = full_text.lower()
    
    targets = {
        "F": "fluorine",
        "CL": "chlorine",
        "BR": "bromine",
        "I": "iodine"
    }
    
    for key, kw in targets.items():
        if not data[key]: # 如果表格沒抓到
            idx = ft_lower.find(kw)
            if idx != -1:
                # 開窗搜索: 往後看 200 字元
                window = ft_lower[idx:idx+200]
                
                # 找 N.D.
                if "n.d." in window:
                    data[key].append("N.D.")
                else:
                    # 找數字 (排除 MDL 50)
                    nums = re.findall(r"\b\d+\b", window)
                    for n in nums:
                        if n == "50": continue # 馬來西亞無鹵 MDL 均為 50
                        if n in ["2020", "62321"]: continue # 排除年份標準
                        data[key].append(n)
                        break

    return data

# =============================================================================
# 4. 主程式與分流器
# =============================================================================

def process_files(files):
    results = []
    progress_bar = st.progress(0)
    
    for i, file in enumerate(files):
        try:
            with pdfplumber.open(file) as pdf:
                # 0. 讀取第一頁判斷引擎
                first_page_text = (pdf.pages[0].extract_text() or "").upper()
                
                # 分流邏輯
                if "MALAYSIA" in first_page_text and "SGS" in first_page_text:
                    # 進入馬來西亞引擎
                    file_data = process_malaysia(pdf, file.name)
                else:
                    # 進入標準引擎 (v60.3)
                    file_data = process_standard(pdf, file.name)
                
                # 資料整理: 取 list 中的第一個值 (通常是最優解)
                final_row = {}
                for k in OUTPUT_COLUMNS:
                    if k == "檔案名稱":
                        final_row[k] = file.name
                    elif k == "日期":
                        final_row[k] = file_data.get("日期", "")
                    else:
                        vals = file_data.get(k, [])
                        # 過濾重複與無效值
                        valid_vals = [v for v in vals if v]
                        if valid_vals:
                            final_row[k] = valid_vals[0] # 取第一個抓到的
                        else:
                            final_row[k] = ""
                
                results.append(final_row)

        except Exception as e:
            st.error(f"處理檔案 {file.name} 時發生錯誤: {e}")
            
        progress_bar.progress((i + 1) / len(files))
        
    return results

# =============================================================================
# 5. Streamlit 介面
# =============================================================================

st.set_page_config(page_title="SGS 報告聚合工具 v61.0", layout="wide")
st.title("📄 萬用型檢測報告聚合工具 (v61.0 雙核心引擎版)")
st.info("💡 v61.0：導入「自動分流」技術。標準報告使用穩定舊核心，SGS 馬來西亞報告使用專用暴力核心。")

uploaded_files = st.file_uploader("請一次選取所有 PDF 檔案", type="pdf", accept_multiple_files=True)

if uploaded_files:
    if st.button("🔄 重新執行"): st.rerun()

    try:
        result_data = process_files(uploaded_files)
        df = pd.DataFrame(result_data)
        
        # 確保欄位順序
        df = df.reindex(columns=OUTPUT_COLUMNS)

        st.success("✅ 處理完成！")
        st.dataframe(df)

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Summary')
        
        st.download_button(
            label="📥 下載 Excel",
            data=output.getvalue(),
            file_name="SGS_Summary_v61.0.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        
    except Exception as e:
        st.error(f"系統錯誤: {e}")
