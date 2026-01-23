import streamlit as st
import pdfplumber
import pandas as pd
import re
import math

# --- 設定頁面 ---
st.set_page_config(page_title="通用檢測報告擷取工具 (V4 最終版)", layout="wide")
st.title("🧪 通用型第三方檢測報告數據擷取工具 (V4 最終版)")
st.markdown("""
**V4 重大升級：**
1. **十字座標鎖定**：解決「隱形表格」與「無框線」排版問題。
2. **智慧排除法**：精準避開 MDL、Limit 與測試方法編號。
3. **中英文雙語支援**：完美相容中文報告與英文報告。
4. **日期精準鎖定**：只抓取首頁報告日期，排除干擾。
""")

# --- 1. 定義目標欄位與關鍵字聯集 (中英文) ---
TARGET_FIELDS = {
    "Lead": {"name": "Pb", "keywords": [r"^Lead\b", r"^Pb\b", r"铅", r"Lead \(Pb\)", r"Pb"]},
    "Cadmium": {"name": "Cd", "keywords": [r"^Cadmium\b", r"^Cd\b", r"镉", r"Cadmium \(Cd\)", r"Cd"]},
    "Mercury": {"name": "Hg", "keywords": [r"^Mercury\b", r"^Hg\b", r"汞", r"Mercury \(Hg\)", r"Hg"]},
    "Hexavalent Chromium": {"name": "Cr(VI)", "keywords": [r"Hexavalent Chromium", r"Cr\(VI\)", r"Cr6\+", r"六价铬", r"六價鉻"]},
    "DEHP": {"name": "DEHP", "keywords": [r"Bis\(2-ethylhexyl\) phthalate", r"DEHP", r"邻苯二甲酸二\(2-乙基己基\)酯"]},
    "BBP": {"name": "BBP", "keywords": [r"Butyl benzyl phthalate", r"BBP", r"邻苯二甲酸丁基苄基酯"]},
    "DBP": {"name": "DBP", "keywords": [r"Dibutyl phthalate", r"DBP", r"邻苯二甲酸二丁酯"]},
    "DIBP": {"name": "DIBP", "keywords": [r"Diisobutyl phthalate", r"DIBP", r"邻苯二甲酸二异丁酯"]},
    "Fluorine": {"name": "F", "keywords": [r"Fluorine", r"氟", r"Fluorine \(F\)"]},
    "Chlorine": {"name": "Cl", "keywords": [r"Chlorine", r"氯", r"Chlorine \(Cl\)"]},
    "Bromine": {"name": "Br", "keywords": [r"Bromine", r"溴", r"Bromine \(Br\)"]},
    "Iodine": {"name": "I", "keywords": [r"Iodine", r"碘", r"Iodine \(I\)"]},
    "PFOS": {"name": "PFOS", "keywords": [r"Perfluorooctane Sulfonates", r"PFOS", r"全氟辛磺酸"]},
}

# PBBs/PBDEs 子項目關鍵字
PBBS_KEYWORDS = [r"Monobromobiphenyl", r"Dibromobiphenyl", r"Tribromobiphenyl", r"Tetrabromobiphenyl", 
                 r"Pentabromobiphenyl", r"Hexabromobiphenyl", r"Heptabromobiphenyl", r"Octabromobiphenyl", 
                 r"Nonabromobiphenyl", r"Decabromobiphenyl", r"一溴联苯", r"十溴联苯", r"一溴聯苯"]
PBDES_KEYWORDS = [r"Monobromodiphenyl ether", r"Dibromodiphenyl ether", r"Tribromodiphenyl ether", 
                  r"Tetrabromodiphenyl ether", r"Pentabromodiphenyl ether", r"Hexabromodiphenyl ether", 
                  r"Heptabromodiphenyl ether", r"Octabromodiphenyl ether", r"Nonabromodiphenyl ether", 
                  r"Decabromodiphenyl ether", r"一溴二苯醚", r"十溴二苯醚"]

# --- 2. 輔助函式區 ---

def clean_text(text):
    """清理文字，移除多餘空白與換行"""
    if not text: return ""
    return re.sub(r'\s+', ' ', str(text)).strip()

def extract_value_logic(val_str):
    """數值提取與優先級邏輯"""
    if not val_str: return 0, "N.D."
    
    val_upper = str(val_str).upper().replace(" ", "")
    
    if "N.D." in val_upper or "ND" in val_upper or "<" in val_upper:
        return 0, "N.D."
    if "NEGATIVE" in val_upper or "阴性" in val_upper:
        return 0.0001, "NEGATIVE"
    if "POSITIVE" in val_upper or "阳性" in val_upper:
        return 999999, "POSITIVE"
    
    # 移除可能的單位干擾 (mg/kg, ppm) 再抓數字
    val_clean = re.sub(r"(mg/kg|ppm|%)", "", val_str, flags=re.IGNORECASE)
    match = re.search(r"(\d+(\.\d+)?)", val_clean)
    
    if match:
        num = float(match.group(1))
        # 簡單過濾：排除可能是 MDL/Limit 的常見整數 (僅作輔助，主要靠欄位鎖定)
        return num, match.group(1)
    
    return 0, "N.D."

def find_date_in_first_page(text):
    """只在第一頁文字中抓取日期"""
    # 格式：Jan 08, 2025 | 2025/01/08 | 2025.01.08
    date_patterns = [
        r"(?:Issue Date|Report Date|Date|Testing Period|日期)\s*[:：\n]?\s*([A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4})",
        r"(?:Issue Date|Report Date|Date|Testing Period|日期)\s*[:：\n]?\s*(\d{4}[-/. ]\d{1,2}[-/. ]\d{1,2})",
        # 針對無標題的日期行 (風險較高，放最後)
        r"^\s*([A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4})\s*$"
    ]
    
    for pattern in date_patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            return match.group(1).replace("\n", " ").strip()
    return ""

def get_column_strategy(header_cells):
    """
    十字座標鎖定核心：分析表頭，決定哪一欄是 Result
    回傳：(結果欄索引, 是否有效)
    """
    # 排除關鍵字 (中英文)
    exclude_keywords = ["ITEM", "METHOD", "UNIT", "MDL", "LOQ", "LIMIT", "REQUIREMENT", 
                        "项目", "方法", "单位", "限值", "RL"]
    
    # 目標關鍵字 (找到這些通常就是結果欄)
    target_keywords = ["RESULT", "结果", "SAMPLE", "NO.", "ID", "001", "002", "A1", "DATA"]
    
    best_col_idx = -1
    max_score = -1
    
    for i, cell in enumerate(header_cells):
        if not cell: continue
        txt = clean_text(cell).upper()
        
        # 1. 如果包含排除字，分數設為極低
        if any(ex in txt for ex in exclude_keywords):
            continue
            
        # 2. 如果包含目標字，分數極高
        score = 0
        if any(tg in txt for tg in target_keywords):
            score += 10
        
        # 3. 啟發式規則：結果欄通常在中間或靠右，但不會是最右邊的 Limit
        # 這裡簡單處理：只要不是排除欄位，且分數最高者得標
        if score > max_score:
            max_score = score
            best_col_idx = i
            
    # 如果沒找到明確的 Result，但有排除掉 Method/Unit，且剩下欄位 > 0，取最後一個非排除欄位
    if best_col_idx == -1:
        valid_indices = [i for i, c in enumerate(header_cells) if c and not any(ex in clean_text(c).upper() for ex in exclude_keywords)]
        if valid_indices:
            best_col_idx = valid_indices[0] # 取第一個「非排除」欄位通常比較保險 (避免取到最右邊的 Note)
            
    return best_col_idx

# --- 3. 核心處理邏輯 (支援隱形表格) ---

def process_file(uploaded_file):
    filename = uploaded_file.name
    results = {k: {"val": 0, "display": ""} for k in TARGET_FIELDS.keys()}
    results["PBBs"] = {"val": 0, "display": "", "sum_val": 0}
    results["PBDEs"] = {"val": 0, "display": "", "sum_val": 0}
    results["PFAS"] = ""
    results["Date"] = ""
    
    is_scanned = True
    full_text_content = ""
    
    with pdfplumber.open(uploaded_file) as pdf:
        # --- A. 全文掃描 (PFAS & 掃描檔檢查) ---
        for i, page in enumerate(pdf.pages):
            text = page.extract_text()
            if text and len(text) > 50:
                is_scanned = False
                full_text_content += text + "\n"
                # 只在第一頁找日期
                if i == 0:
                    results["Date"] = find_date_in_first_page(text)

        if is_scanned:
            return None, filename

        # PFAS 判斷
        if "PFAS" in full_text_content.upper() or "PER- AND POLYFLUOROALKYL" in full_text_content.upper():
            results["PFAS"] = "REPORT"

        # --- B. 數據擷取 (雙模式：表格模式 + 文字流模式) ---
        for page in pdf.pages:
            # 模式 1：標準表格提取
            extracted_tables = page.extract_tables()
            
            # 如果這頁完全沒抓到表格，或者表格太破碎，我們切換到「文字流模式」
            # 這裡簡化處理：先跑表格模式，若表格內有數據就抓
            
            if extracted_tables:
                for table in extracted_tables:
                    if not table or len(table) < 2: continue
                    
                    # 尋找表頭
                    header_row_idx = -1
                    result_col_idx = -1
                    
                    # 掃描前幾行找表頭
                    for r_idx, row in enumerate(table[:5]):
                        # 判斷是否為表頭：含有 Item/Method/Unit 等字眼
                        row_text = " ".join([str(c).upper() for c in row if c])
                        if ("ITEM" in row_text or "项目" in row_text) and ("UNIT" in row_text or "单位" in row_text or "MDL" in row_text):
                            header_row_idx = r_idx
                            result_col_idx = get_column_strategy(row)
                            break
                    
                    if result_col_idx != -1:
                        # 有效表格，開始抓數據
                        for r_idx in range(header_row_idx + 1, len(table)):
                            row = table[r_idx]
                            if len(row) <= result_col_idx: continue
                            
                            # 組合第一欄與第二欄作為項目名稱 (處理跨欄)
                            item_name = clean_text(row[0])
                            if len(row) > 1: item_name += " " + clean_text(row[1])
                            
                            val_text = clean_text(row[result_col_idx])
                            update_results(results, item_name, val_text)
                            
            # 模式 2：隱形表格/文字流 (當表格模式可能漏掉時的補強，或針對無框線報告)
            # pdfplumber 的 extract_text(layout=True) 可以保留視覺相對位置
            # 但為了簡單且高效，我們這裡使用 extract_words 來做簡易的「行對齊」分析
            # 這裡針對「隱形表格」的邏輯：
            # 1. 找到關鍵字 (如 "Lead") 的 Y 軸
            # 2. 找到同一 Y 軸上，最靠右側 (但不是 Limit) 的文字
            
            words = page.extract_words(keep_blank_chars=True)
            # 將文字按行分組 (Tolerance 3)
            rows = {}
            for w in words:
                y = round(w['top'] / 3) * 3 # 模糊 Y 軸
                if y not in rows: rows[y] = []
                rows[y].append(w)
            
            # 遍歷每一行文字
            for y, row_words in rows.items():
                # 將這一行的文字組合成字串
                line_text = " ".join([w['text'] for w in row_words])
                
                # 簡單的啟發式：如果這一行包含我們的關鍵字
                # 嘗試從這一行找出數字或 N.D.
                # 這裡的風險是可能會抓到 MDL，所以我們需要檢查同一行有沒有多個數字
                
                # 針對一般項目
                for field, config in TARGET_FIELDS.items():
                    for kw in config["keywords"]:
                        if re.search(kw, line_text, re.IGNORECASE):
                            # 在這一行中尋找 ND 或 數字
                            # 排除掉 Item Name 本身
                            # 策略：由右向左找 (通常結果在右邊)，且跳過 Limit (通常最大)
                            
                            # 找出所有候選值
                            candidates = []
                            # 簡單切分
                            parts = line_text.split()
                            for part in parts:
                                val, disp = extract_value_logic(part)
                                # 排除明顯的 Limit (如 1000) 和 MDL (如 2, 5, 10) 
                                # 這是一個純文字模式下的妥協：若有表格模式優先信表格
                                if val in [100, 1000, 2, 5, 8, 10, 25, 50] and disp != "N.D.":
                                    continue
                                if disp == "N.D." or val > 0:
                                    candidates.append((val, disp))
                            
                            if candidates:
                                # 優先取 N.D.，或是非 MDL 的數值
                                # 這裡假設：如果有多個候選，取最後一個 (通常結果在右邊) 
                                # 或者取 N.D. (最常見)
                                best_val, best_disp = candidates[-1] # 取最右邊
                                update_results(results, field, best_disp, is_text_mode=True)

                # 針對 PBBs/PBDEs (同樣邏輯)
                for pbb_kw in PBBS_KEYWORDS + PBDES_KEYWORDS:
                    if re.search(pbb_kw, line_text, re.IGNORECASE):
                         parts = line_text.split()
                         for part in parts:
                             val, disp = extract_value_logic(part)
                             if val in [1000, 5, 25] and disp != "N.D.": continue # 排除 Limit/MDL
                             if val > 0: # 只有抓到數值才加總 (N.D. 為 0)
                                 # 判斷是 PBB 還是 PBDE
                                 cat = "PBBs" if any(k in pbb_kw for k in PBBS_KEYWORDS) else "PBDEs"
                                 results[cat]["sum_val"] += val
                                 break

    # --- C. 最終數值結算 ---
    finalize_results(results)
    
    # --- D. 輸出格式化 ---
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
        # 排序用隱藏欄位
        "_sort_pb": results["Lead"]["val"],
        "_sort_max": max([v["val"] for k, v in results.items() if isinstance(v, dict) and "val" in v])
    }
    
    return final_output, None

def update_results(results, item_name, val_text, is_text_mode=False):
    """
    統一更新結果的邏輯，包含比較大小 (若同一檔案多個數據)
    item_name: 項目名稱 (如 "Lead")
    val_text: 抓到的數值文字 (如 "N.D." 或 "8")
    """
    item_upper = str(item_name).upper()
    val_num, val_disp = extract_value_logic(val_text)
    
    # 1. 一般項目匹配
    for field_key, config in TARGET_FIELDS.items():
        for kw in config["keywords"]:
            if re.search(kw, item_upper, re.IGNORECASE):
                # 如果是文字模式且該欄位已有值 (來自表格模式)，則跳過 (表格模式較準)
                if is_text_mode and results[field_key]["val"] > 0:
                    return

                if val_num > results[field_key]["val"]:
                    results[field_key]["val"] = val_num
                    results[field_key]["display"] = val_disp
                elif val_num == 0 and results[field_key]["val"] == 0:
                    if val_disp == "NEGATIVE": results[field_key]["display"] = "NEGATIVE"
                    elif not results[field_key]["display"]: results[field_key]["display"] = "N.D."
                return

    # 2. PBBs/PBDEs 加總
    for pbb_kw in PBBS_KEYWORDS:
        if re.search(pbb_kw, item_upper, re.IGNORECASE):
            # 排除文字模式下的重複加總風險 (簡單做：只信表格模式的加總，或是非常確定才加)
            if is_text_mode: return 
            results["PBBs"]["sum_val"] += val_num
            return

    for pbde_kw in PBDES_KEYWORDS:
        if re.search(pbde_kw, item_upper, re.IGNORECASE):
            if is_text_mode: return
            results["PBDEs"]["sum_val"] += val_num
            return

def finalize_results(results):
    """計算最終顯示 (PBBs/PBDEs)"""
    if results["PBBs"]["sum_val"] > 0:
        results["PBBs"]["display"] = str(round(results["PBBs"]["sum_val"], 2))
        results["PBBs"]["val"] = results["PBBs"]["sum_val"]
    elif not results["PBBs"]["display"]:
        results["PBBs"]["display"] = "N.D."

    if results["PBDEs"]["sum_val"] > 0:
        results["PBDEs"]["display"] = str(round(results["PBDEs"]["sum_val"], 2))
        results["PBDEs"]["val"] = results["PBDEs"]["sum_val"]
    elif not results["PBDEs"]["display"]:
        results["PBDEs"]["display"] = "N.D."

# --- 主介面 ---

uploaded_files = st.file_uploader("請上傳 PDF 檢測報告 (支援 SGS, CTI, Intertek 等)", type="pdf", accept_multiple_files=True)

if uploaded_files:
    all_data = []
    scanned_files = []

    with st.spinner('正在進行 V4 引擎分析 (十字鎖定 + 雙模式掃描)...'):
        for pdf_file in uploaded_files:
            data, scanned_name = process_file(pdf_file)
            if scanned_name:
                scanned_files.append(scanned_name)
            else:
                all_data.append(data)

    if all_data:
        df = pd.DataFrame(all_data)
        # 排序：Pb 優先，其他最大值次之
        df = df.sort_values(by=["_sort_pb", "_sort_max"], ascending=[False, False])
        display_df = df.drop(columns=["_sort_pb", "_sort_max"])
        
        st.success(f"✅ 成功擷取 {len(all_data)} 份報告！")
        st.dataframe(display_df, use_container_width=True)
        
        csv = display_df.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            label="📥 下載 Excel/CSV 報表",
            data=csv,
            file_name="rohs_report_v4.csv",
            mime="text/csv",
        )

    if scanned_files:
        st.error("⚠️ 以下檔案為掃描圖片 (無法擷取文字)：")
        for f in scanned_files:
            st.write(f"- {f}")

else:
    st.info("請上傳 PDF 檔案以開始分析。")
