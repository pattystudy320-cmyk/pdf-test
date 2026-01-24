import streamlit as st
import pdfplumber
import pandas as pd
import re
from datetime import datetime

# --- 設定頁面 ---
st.set_page_config(page_title="通用檢測報告擷取工具 (V16 旗艦版)", layout="wide")
st.title("🧪 通用型第三方檢測報告數據擷取工具 (V16 旗艦版)")
st.markdown("""
**V16 版本核心特徵：上下文感知與雙軌提取**
1.  **🔍 樣品編號預讀**：自動偵測 "A1", "A2", "001" 等編號，破解未知表頭。
2.  **📄 PBBs/PBDEs 純文字掃描 (V7)**：無視表格結構，直接從文字流抓取數值，解決空值問題。
3.  **📅 智能日期 V2**：中英雙語支援 + 黑名單過濾 + 最晚日期法則。
4.  **🛡️ 綜合防禦**：CTI PVC 排除、SGS 隱形表格對策、PFOS 單項鎖定。
""")

# --- 1. 關鍵字定義 (中英雙語庫) ---
TARGET_FIELDS = {
    "Lead": {"name": "Pb", "keywords": [r"^Lead\b", r"^Pb\b", r"铅", r"Lead \(Pb\)", r"Pb"]},
    "Cadmium": {"name": "Cd", "keywords": [r"^Cadmium\b", r"^Cd\b", r"镉", r"Cadmium \(Cd\)", r"Cd"]},
    "Mercury": {"name": "Hg", "keywords": [r"^Mercury\b", r"^Hg\b", r"汞", r"Mercury \(Hg\)", r"Hg"]},
    "Hexavalent Chromium": {"name": "Cr(VI)", "keywords": [r"Hexavalent Chromium", r"Cr\(VI\)", r"Cr6\+", r"六价铬", r"六價鉻"]},
    "DEHP": {"name": "DEHP", "keywords": [r"Bis\(2-ethylhexyl\) phthalate", r"DEHP", r"邻苯二甲酸二\(2-乙基己基\)酯"]},
    "BBP": {"name": "BBP", "keywords": [r"Butyl benzyl phthalate", r"BBP", r"邻苯二甲酸丁基苄基酯", r"邻苯二甲酸丁苄酯"]},
    "DBP": {"name": "DBP", "keywords": [r"Dibutyl phthalate", r"DBP", r"邻苯二甲酸二丁酯"]},
    "DIBP": {"name": "DIBP", "keywords": [r"Diisobutyl phthalate", r"DIBP", r"邻苯二甲酸二异丁酯"]},
    "Fluorine": {"name": "F", "keywords": [r"Fluorine", r"氟", r"Fluorine \(F\)"]},
    "Chlorine": {"name": "Cl", "keywords": [r"Chlorine", r"氯", r"Chlorine \(Cl\)"]},
    "Bromine": {"name": "Br", "keywords": [r"Bromine", r"溴", r"Bromine \(Br\)"]},
    "Iodine": {"name": "I", "keywords": [r"Iodine", r"碘", r"Iodine \(I\)"]},
    "PFOS": {"name": "PFOS", "keywords": [r"Perfluorooctane Sulfonates", r"PFOS", r"全氟辛烷磺酸"]},
}

# PBBs/PBDEs 關鍵字 (V7 邏輯用)
PBBS_KEYWORDS = [r"Monobromobiphenyl", r"Dibromobiphenyl", r"Tribromobiphenyl", r"Tetrabromobiphenyl", 
                 r"Pentabromobiphenyl", r"Hexabromobiphenyl", r"Heptabromobiphenyl", r"Octabromobiphenyl", 
                 r"Nonabromobiphenyl", r"Decabromobiphenyl", 
                 r"一溴联苯", r"二溴联苯", r"三溴联苯", r"四溴联苯", r"五溴联苯", 
                 r"六溴联苯", r"七溴联苯", r"八溴联苯", r"九溴联苯", r"十溴联苯"]

PBDES_KEYWORDS = [r"Monobromodiphenyl ether", r"Dibromodiphenyl ether", r"Tribromodiphenyl ether", 
                  r"Tetrabromodiphenyl ether", r"Pentabromodiphenyl ether", r"Hexabromodiphenyl ether", 
                  r"Heptabromodiphenyl ether", r"Octabromodiphenyl ether", r"Nonabromodiphenyl ether", 
                  r"Decabromodiphenyl ether", 
                  r"一溴二苯醚", r"二溴二苯醚", r"三溴二苯醚", r"四溴二苯醚", r"五溴二苯醚", 
                  r"六溴二苯醚", r"七溴二苯醚", r"八溴二苯醚", r"九溴二苯醚", r"十溴二苯醚"]

# --- 2. 輔助函式 ---

def clean_text(text):
    if not text: return ""
    return re.sub(r'\s+', ' ', str(text)).strip()

def extract_value_logic(val_str, strict_numeric=False):
    """
    通用數值提取：
    strict_numeric=True 時 (用於 Cl, PFOS)，拒絕 Negative/Positive，只接受數字。
    """
    if not val_str: return None, ""
    
    val_upper = str(val_str).upper().replace(" ", "")
    
    # 1. CAS No. 防火牆 (常見的誤判來源)
    if re.search(r"\b\d{2,7}-\d{2}-\d\b", val_str): return None, ""

    # 2. 文字狀態處理
    if "N.D." in val_upper or "ND" in val_upper or "<" in val_upper: return 0, "N.D."
    
    if "NEGATIVE" in val_upper or "阴性" in val_upper: 
        if strict_numeric: return None, "" # 對於定量項目，Negative 是無效值 (可能是 PVC)
        return 0.0001, "NEGATIVE"
        
    if "POSITIVE" in val_upper or "阳性" in val_upper: 
        if strict_numeric: return None, ""
        return 999999, "POSITIVE"
    
    # 3. 純數字提取
    # 移除單位干擾
    val_clean = re.sub(r"(mg/kg|ppm|%|µg/cm²|ug/cm2)", "", val_str, flags=re.IGNORECASE)
    match = re.search(r"(\d+(\.\d+)?)", val_clean)
    
    if match:
        num = float(match.group(1))
        # 年份過濾 (2010-2030 視為年份而非結果)
        if 2010 <= num <= 2030: return None, ""
        return num, match.group(1)
    
    return None, ""

# --- 3. 核心功能模組 ---

def find_sample_ids(full_text_pages_1_2):
    """
    [V16 新功能] 預讀樣品編號
    掃描前兩頁，找出 Sample No. 後面的代號 (如 A1, A2, 001)
    """
    ids = []
    # 常見樣品編號標籤
    patterns = [
        r"(?:Sample|Specimen)\s*(?:No\.|ID|Ref\.?)\s*[:：]?\s*([A-Za-z0-9\-]+)",
        r"(?:SN\s*ID)\s*[:：]?\s*([A-Za-z0-9\-]+)",
        r"(?:样品|樣品)\s*(?:编号|序号|ID)\s*[:：]?\s*([A-Za-z0-9\-]+)"
    ]
    
    for line in full_text_pages_1_2.split('\n'):
        for pat in patterns:
            m = re.search(pat, line, re.IGNORECASE)
            if m:
                found_id = m.group(1).strip()
                if len(found_id) < 10: # 避免抓到太長的雜訊
                    ids.append(found_id.upper())
    
    return list(set(ids)) # 去重

def find_issue_date(full_text_page_1):
    """
    [V16 日期鎖定] 黑名單 + 最晚日期法則
    """
    lines = full_text_page_1.split('\n')
    candidates = []
    
    # 黑名單：出現這些字的行，日期通常是過程而非結果
    blacklist = ["RECEIVED", "PERIOD", "STARTED", "SUBMITTED", "COMPLETED", "TESTING", "收件", "接收", "周期", "期间"]
    
    for line in lines:
        upper_line = line.upper()
        if any(bad in upper_line for bad in blacklist):
            continue
            
        # 抓取各種日期格式
        # 1. 2025/06/16, 2025.06.16, 2025-06-16, 2025年06月16日
        m1 = re.search(r"(\d{4})[-/. 年](\d{1,2})[-/. 月](\d{1,2})", line)
        if m1:
            try:
                dt = datetime(int(m1.group(1)), int(m1.group(2)), int(m1.group(3)))
                if 2015 <= dt.year <= 2030: candidates.append(dt)
            except: pass
            
        # 2. 16-Jun-2025, 16 Jan 2025
        m2 = re.search(r"(\d{1,2})[-/\s]([A-Za-z]{3})[-/\s,.]+(\d{4})", line)
        if m2:
            try:
                dt = datetime.strptime(f"{m2.group(1)}-{m2.group(2)}-{m2.group(3)}", "%d-%b-%Y")
                if 2015 <= dt.year <= 2030: candidates.append(dt)
            except: pass

        # 3. Jun 16, 2025
        m3 = re.search(r"([A-Za-z]{3})\.?\s+(\d{1,2})[,\s]+(\d{4})", line)
        if m3:
            try:
                dt = datetime.strptime(f"{m3.group(2)}-{m3.group(1)}-{m3.group(3)}", "%d-%b-%Y")
                if 2015 <= dt.year <= 2030: candidates.append(dt)
            except: pass

    if candidates:
        # 發行日期永遠是時間軸上最晚的
        latest = max(candidates)
        return latest.strftime("%Y/%m/%d")
    
    return ""

def check_pfas_in_section(full_text):
    """PFAS 區塊限定：只在 Test Requested 區域搜尋"""
    start_keywords = ["TEST REQUESTED", "测试需求", "检测要求", "TEST REQUEST"]
    end_keywords = ["TEST METHOD", "TEST RESULTS", "CONCLUSION", "测试结果", "结论", "检测方法"]
    
    upper = full_text.upper()
    start_idx = -1
    for kw in start_keywords:
        idx = upper.find(kw)
        if idx != -1:
            start_idx = idx
            break
            
    if start_idx == -1: return ""
    
    end_idx = len(upper)
    for kw in end_keywords:
        idx = upper.find(kw, start_idx)
        if idx != -1:
            end_idx = idx
            break
            
    target_text = upper[start_idx:end_idx]
    if "PFAS" in target_text or "PER- AND POLYFLUOROALKYL" in target_text:
        return "REPORT"
    return ""

# --- 4. 數據提取引擎 ---

def process_file(uploaded_file):
    filename = uploaded_file.name
    results = {k: {"val": None, "display": ""} for k in TARGET_FIELDS.keys()}
    results["PBBs"] = {"val": None, "display": "", "sum_val": 0}
    results["PBDEs"] = {"val": None, "display": "", "sum_val": 0}
    results["PFAS"] = ""
    results["Date"] = ""
    
    full_text_content = ""
    first_page_text = ""
    
    with pdfplumber.open(uploaded_file) as pdf:
        # --- Phase 1: 預掃描 (Pre-scan) ---
        for i, page in enumerate(pdf.pages):
            text = page.extract_text()
            if text:
                full_text_content += text + "\n"
                if i == 0: first_page_text = text
                if i < 2: # 只掃前兩頁找 Sample ID
                    pass # 實際邏輯在下面
        
        # 1. 抓日期
        results["Date"] = find_issue_date(first_page_text)
        
        # 2. 抓 Sample IDs (A1, A2, 001...)
        sample_ids = find_sample_ids(full_text_content[:3000]) # 限制長度避免跑太久
        
        # 3. 抓 PFAS 狀態
        results["PFAS"] = check_pfas_in_section(full_text_content)

        # --- Phase 2: 雙軌數據提取 ---
        
        # 軌道 A: [純文字流] 專抓 PBBs / PBDEs (V7 邏輯)
        # 因為 SGS 的有機物表格常隱形，用文字抓最穩
        text_lines = full_text_content.split('\n')
        for line in text_lines:
            line_upper = line.upper()
            
            # 定義加總處理函式
            def process_text_sum(keywords, cat_key):
                if any(k.upper() in line_upper for k in keywords):
                    # 在這一行文字中找所有數字
                    # 排除常見干擾: 1000(Limit), 5/10/25(MDL), CAS No.
                    potential_vals = []
                    # 分割字串來分析
                    parts = line.split()
                    for part in parts:
                        v, d = extract_value_logic(part)
                        if v is not None:
                            # 智慧過濾: 若是 MDL/Limit 常見值且不是 N.D.，跳過
                            if v in [5, 10, 25, 50, 100, 1000] and d != "N.D.":
                                continue
                            potential_vals.append(v)
                    
                    if potential_vals:
                        val = potential_vals[-1] # 取最後一個有效值
                        if val > 0:
                            results[cat_key]["sum_val"] += val
                            results[cat_key]["val"] = 1

            process_text_sum(PBBS_KEYWORDS, "PBBs")
            process_text_sum(PBDES_KEYWORDS, "PBDEs")

        # 軌道 B: [表格定位] 專抓 Pb, Cd, Cl, PFOS (V15 邏輯)
        for page in pdf.pages:
            tables = page.extract_tables()
            if not tables: continue
            
            for table in tables:
                if not table or len(table) < 2: continue
                
                # 尋找表頭 (Header)
                header_row_idx = -1
                result_col_idx = -1
                
                for r_idx, row in enumerate(table[:6]):
                    row_str = " ".join([str(c).upper() for c in row if c])
                    
                    # 判斷是否為檢測數據表
                    if ("ITEM" in row_str or "项目" in row_str or "TEST" in row_str):
                        header_row_idx = r_idx
                        
                        # 嘗試定位 Result 欄位
                        # 優先級 1: 明確關鍵字
                        for c_idx, cell in enumerate(row):
                            txt = clean_text(str(cell)).upper()
                            if "RESULT" in txt or "结果" in txt or "DATA" in txt:
                                result_col_idx = c_idx
                                break
                        
                        # 優先級 2: 樣品編號匹配 (A1, A2...)
                        if result_col_idx == -1:
                            for c_idx, cell in enumerate(row):
                                txt = clean_text(str(cell)).upper()
                                if txt in sample_ids: # 命中 Sample ID!
                                    result_col_idx = c_idx
                                    break
                                    
                        # 優先級 3: 消去法 (排除 Unit, MDL, Limit)
                        if result_col_idx == -1:
                            scores = {}
                            for c_idx, cell in enumerate(row):
                                txt = clean_text(str(cell)).upper()
                                if any(x in txt for x in ["UNIT", "MDL", "LIMIT", "LOQ", "单位", "限值", "方法", "CAS"]):
                                    scores[c_idx] = -100
                                else:
                                    scores[c_idx] = 50 # 可能是結果
                            
                            if scores:
                                best = max(scores, key=scores.get)
                                if scores[best] > 0:
                                    result_col_idx = best
                        
                        break # 找到表頭就停止
                
                if header_row_idx == -1: continue # 這張表不是數據表
                
                # 遍歷數據行
                for r_idx in range(header_row_idx + 1, len(table)):
                    row = table[r_idx]
                    if not row: continue
                    
                    # 組合項目名稱 (防跨欄)
                    item_name = clean_text(row[0])
                    if len(row) > 1: item_name += " " + clean_text(row[1])
                    item_upper = item_name.upper()
                    
                    # 只處理非 PBBs/PBDEs 的項目 (有機物已在軌道 A 處理)
                    for field, config in TARGET_FIELDS.items():
                        for kw in config["keywords"]:
                            if re.search(kw, item_upper, re.IGNORECASE):
                                
                                # 特殊防護: Cl (氯) 排除 PVC
                                if field == "Chlorine" and ("POLYVINYL" in item_upper or "PVC" in item_upper):
                                    continue
                                
                                # 決定從哪裡抓值
                                val_text = ""
                                if result_col_idx != -1 and len(row) > result_col_idx:
                                    val_text = clean_text(row[result_col_idx])
                                else:
                                    # 如果定位失敗，嘗試抓最後一欄
                                    val_text = clean_text(row[-1])
                                
                                # 數值解析
                                is_strict = (field in ["Chlorine", "Bromine", "PFOS"]) # 這些不接受 Negative
                                v_num, v_disp = extract_value_logic(val_text, strict_numeric=is_strict)
                                
                                if v_num is not None:
                                    # 更新結果 (取最大值)
                                    curr = results[field]["val"]
                                    if curr is None or v_num > curr:
                                        results[field]["val"] = v_num
                                        results[field]["display"] = v_disp
                                    elif v_num == 0 and (curr is None or curr == 0):
                                        if v_disp == "NEGATIVE": results[field]["display"] = "NEGATIVE"
                                        elif not results[field]["display"]: results[field]["display"] = "N.D."
                                        results[field]["val"] = 0

    # --- 最終整理 ---
    if results["PBBs"]["sum_val"] > 0:
        results["PBBs"]["display"] = str(round(results["PBBs"]["sum_val"], 2))
    elif results["PBBs"]["val"] is None:
        results["PBBs"]["display"] = "" # 沒抓到顯示空白
    else:
        results["PBBs"]["display"] = "N.D." # 抓到但都是 0

    if results["PBDEs"]["sum_val"] > 0:
        results["PBDEs"]["display"] = str(round(results["PBDEs"]["sum_val"], 2))
    elif results["PBDEs"]["val"] is None:
        results["PBDEs"]["display"] = ""
    else:
        results["PBDEs"]["display"] = "N.D."

    final_output = {
        "File Name": filename,
        "Pb": results["Lead"]["display"],
        "Cd": results["Cadmium"]["display"],
        "Hg": results["Mercury"]["display"],
        "Cr(VI)": results["Hexavalent Chromium"]["display"],
        "PBBs": results["PBBs"]["display"],
        "PBDEs": results["PBDEs"]["display"],
        "DEHP": results["DEHP"]["display"],
        "BBP": results["BBP"]["display"],
        "DBP": results["DBP"]["display"],
        "DIBP": results["DIBP"]["display"],
        "F": results["Fluorine"]["display"],
        "Cl": results["Chlorine"]["display"],
        "Br": results["Bromine"]["display"],
        "I": results["Iodine"]["display"],
        "PFOS": results["PFOS"]["display"],
        "PFAS": results["PFAS"],
        "Date": results["Date"],
        "_sort_pb": results["Lead"]["val"],
        "_sort_max": max([v["val"] for k, v in results.items() if isinstance(v, dict) and v["val"] is not None])
    }
    
    return final_output, None

# --- 主介面 ---
uploaded_files = st.file_uploader("請上傳 PDF 檢測報告 (支援 SGS, CTI, Intertek 等)", type="pdf", accept_multiple_files=True)

if uploaded_files:
    all_data = []
    scanned_files = []

    with st.spinner('正在進行 V16 旗艦引擎分析 (雙軌提取 + 上下文感知)...'):
        for pdf_file in uploaded_files:
            data, scanned_name = process_file(pdf_file)
            if scanned_name:
                scanned_files.append(scanned_name)
            else:
                all_data.append(data)

    if all_data:
        df = pd.DataFrame(all_data)
        # 排序
        if "_sort_pb" in df.columns:
            df = df.sort_values(by=["_sort_pb", "_sort_max"], ascending=[False, False])
            display_df = df.drop(columns=["_sort_pb", "_sort_max"])
        else:
            display_df = df
        
        st.success(f"✅ 成功擷取 {len(all_data)} 份報告！(V16 核心)")
        st.dataframe(display_df, use_container_width=True)
        
        csv = display_df.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            label="📥 下載 Excel/CSV 報表",
            data=csv,
            file_name="rohs_report_v16_final.csv",
            mime="text/csv",
        )

    if scanned_files:
        st.error("⚠️ 以下檔案為掃描圖片 (無法擷取文字)：")
        for f in scanned_files:
            st.write(f"- {f}")
else:
    st.info("請上傳 PDF 檔案以開始分析。")
