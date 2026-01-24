import streamlit as st
import pdfplumber
import pandas as pd
import re
from datetime import datetime

# --- 設定頁面 ---
st.set_page_config(page_title="通用檢測報告擷取工具 (V17 視覺座標版)", layout="wide")
st.title("🧪 通用型第三方檢測報告數據擷取工具 (V17 視覺座標版)")
st.markdown("""
**V17 版本核心特徵：視覺座標引擎 + 多國語言字典**
1.  **👁️ PBBs/PBDEs 視覺掃描**：利用文字座標 (Y-Axis) 鎖定同一行數值，無視排版錯位與隱形表格。
2.  **🌍 多國語言字典**：新增 SGS 專用術語、英文縮寫 (MonoBB)、韓文關鍵字 (모노브로모)。
3.  **📅 韓國日期支援**：支援 `YYYY. MM. DD.` 格式與韓文發行日標籤。
4.  **🛡️ SGS 絕對防禦**：若表格定位失敗，強制鎖定最右欄。
""")

# --- 1. 擴充關鍵字定義 (包含字根、縮寫、韓文) ---
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

# 有機物關鍵字 (視覺掃描用 - 字根匹配)
# 包含：英文全稱字根、縮寫、中文、韓文
PBBS_ROOTS = [
    "Monobromo", "Dibromo", "Tribromo", "Tetrabromo", "Pentabromo", "Hexabromo", "Heptabromo", "Octabromo", "Nonabromo", "Decabromo",
    "MonoBB", "DiBB", "TriBB", "TetraBB", "PentaBB", "HexaBB", "HeptaBB", "OctaBB", "NonaBB", "DecaBB",
    "一溴联苯", "二溴联苯", "三溴联苯", "四溴联苯", "五溴联苯", "六溴联苯", "七溴联苯", "八溴联苯", "九溴联苯", "十溴联苯",
    "모노브로모", "다이브로모", "트라이브로모", "테트라브로모", "펜타브로모", "헥사브로모", "헵타브로모", "옥타브로모", "노나브로모", "데카브로모"
]

PBDES_ROOTS = [
    "Monobromodiphenyl", "Dibromodiphenyl", "Tribromodiphenyl", "Tetrabromodiphenyl", "Pentabromodiphenyl", "Hexabromodiphenyl", 
    "Heptabromodiphenyl", "Octabromodiphenyl", "Nonabromodiphenyl", "Decabromodiphenyl",
    "MonoBDE", "DiBDE", "TriBDE", "TetraBDE", "PentaBDE", "HexaBDE", "HeptaBDE", "OctaBDE", "NonaBDE", "DecaBDE",
    "一溴二苯醚", "二溴二苯醚", "三溴二苯醚", "四溴二苯醚", "五溴二苯醚", "六溴二苯醚", "七溴二苯醚", "八溴二苯醚", "九溴二苯醚", "十溴二苯醚"
]

# --- 2. 輔助函式 ---

def clean_text(text):
    if not text: return ""
    return re.sub(r'\s+', ' ', str(text)).strip()

def parse_date_obj(date_str):
    """強化的日期解析，支援韓文格式與空格點"""
    clean = re.sub(r"Date:|Issue Date:|Report Date:|日期|발행일자|발행\s*\(?Date\)?[:：]?", "", date_str, flags=re.IGNORECASE).strip()
    clean = clean.replace("/", "-").replace(" ", "-") # 先把常見分隔符統一
    
    # 針對韓文/特殊格式 2024. 10. 17. 進行預處理
    # 將 "2024. 10. 17" 轉為 "2024-10-17"
    if "." in clean:
        clean = re.sub(r"\s+", "", clean) # 移除所有空格
        clean = clean.rstrip(".") # 移除結尾的點
        clean = clean.replace(".", "-")

    formats = ["%Y-%m-%d", "%d-%b-%Y", "%d-%B-%Y", "%b-%d-%Y", "%B-%d-%Y", "%d-%b-%y", "%d-%B-%y"]
    for fmt in formats:
        try: return datetime.strptime(clean, fmt)
        except: continue
            
    # Regex 補強
    try:
        # 2025-06-16
        m = re.search(r"(\d{4})[-/. ]*(\d{1,2})[-/. ]*(\d{1,2})", date_str)
        if m: return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        
        # 16-Jun-2025
        m2 = re.search(r"(\d{1,2})[-/\s]([A-Za-z]{3})[-/\s,.]+(\d{4})", date_str, re.IGNORECASE)
        if m2: return datetime.strptime(f"{m2.group(1)}-{m2.group(2)}-{m2.group(3)}", "%d-%b-%Y")
    except: pass
    return None

def find_date_in_first_page(text):
    lines = text.split('\n')
    candidates = []
    # 黑名單：加入韓文 "시험" (Test)
    blacklist = ["RECEIVED", "PERIOD", "STARTED", "SUBMITTED", "COMPLETED", "TESTING", "收件", "接收", "周期", "期间", "시험"]
    
    for line in lines:
        upper_line = line.upper()
        if any(bad in upper_line for bad in blacklist): continue
            
        # 抓取 YYYY.MM.DD 或 DD-Mon-YYYY
        if re.search(r"\d{4}[-/. ]+\d{1,2}[-/. ]+\d{1,2}", line) or \
           (re.search(r"[A-Za-z]{3}", line) and re.search(r"\d{4}", line)):
            candidates.append(line)
            
    valid_dates = []
    for c in candidates:
        dt = parse_date_obj(c)
        if dt and 2015 <= dt.year <= 2030: valid_dates.append(dt)
    
    if valid_dates:
        return max(valid_dates).strftime("%Y/%m/%d")
    return ""

def extract_value_logic(val_str, strict_numeric=False):
    if not val_str: return None, ""
    val_upper = str(val_str).upper().replace(" ", "")
    
    if re.search(r"\b\d{2,7}-\d{2}-\d\b", val_str): return None, "" # CAS No.

    if "N.D." in val_upper or "ND" in val_upper or "<" in val_upper: return 0, "N.D."
    
    if "NEGATIVE" in val_upper or "阴性" in val_upper: 
        if strict_numeric: return None, ""
        return 0.0001, "NEGATIVE"
        
    if "POSITIVE" in val_upper or "阳性" in val_upper: 
        if strict_numeric: return None, ""
        return 999999, "POSITIVE"
    
    val_clean = re.sub(r"(mg/kg|ppm|%|µg/cm²|ug/cm2)", "", val_str, flags=re.IGNORECASE)
    match = re.search(r"(\d+(\.\d+)?)", val_clean)
    
    if match:
        num = float(match.group(1))
        if 2010 <= num <= 2030: return None, "" 
        return num, match.group(1)
    
    return None, ""

# --- 3. 核心功能模組 ---

def find_sample_ids(full_text_pages_1_2):
    """預讀樣品編號"""
    ids = []
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
                if len(found_id) < 15: ids.append(found_id.upper())
    return list(set(ids))

def extract_visual_row_values(page_words, keywords):
    """
    [V17 核心] 視覺座標掃描引擎
    page_words: pdfplumber.extract_words() 的結果
    keywords: 要尋找的關鍵字列表 (字根)
    """
    found_values = []
    
    # 1. 尋找關鍵字所在的 Word 物件
    target_words = []
    for w in page_words:
        txt = w['text'].upper()
        # 使用字根匹配 (只要包含 Monobromo 就算)
        if any(k.upper() in txt for k in keywords):
            target_words.append(w)
    
    if not target_words:
        return []

    # 2. 針對每個找到的關鍵字，掃描「同一高度」的所有文字
    for tw in target_words:
        # 定義掃描區域 (Y軸中心點 +/- 3px)
        y_center = (tw['top'] + tw['bottom']) / 2
        tolerance = 5 
        
        # 找出所有在同一行的文字
        row_words = [
            w for w in page_words 
            if abs((w['top'] + w['bottom']) / 2 - y_center) < tolerance
        ]
        
        # 依 X 軸排序 (從左到右)
        row_words.sort(key=lambda x: x['x0'])
        
        # 提取數值
        for w in row_words:
            v_num, v_disp = extract_value_logic(w['text'])
            if v_num is not None:
                # 智慧過濾: 排除 MDL/Limit
                if v_num in [5, 10, 25, 50, 100, 1000] and v_disp != "N.D.": continue
                found_values.append(v_num)
    
    return found_values

def get_column_score(header_cells, sample_ids, is_sgs=False):
    """V17 表格定位"""
    scores = {}
    num_cols = len(header_cells)
    
    result_kw = ["RESULT", "结果", "SAMPLE", "ID", "001", "002", "A1", "A2", "DATA", "含量"]
    known_cols_kw = ["ITEM", "METHOD", "UNIT", "MDL", "LOQ", "LIMIT", "REQUIREMENT", "项目", "方法", "单位", "限值", "CAS"]
    
    for i, cell in enumerate(header_cells):
        if not cell: continue
        txt = clean_text(str(cell)).upper()
        score = 0
        
        if any(k in txt for k in known_cols_kw): score -= 500
        if any(res in txt for res in result_kw): score += 100
        if txt in sample_ids: score += 500 # 命中 Sample ID 權重最高
        
        if score == 0: score += 50 
        scores[i] = score

    if not scores: return -1
    best_col = max(scores, key=scores.get)
    
    # SGS 專屬：若無明確結果欄，優先信任最右欄
    if is_sgs and scores[best_col] <= 50: 
        return num_cols - 1
        
    if scores[best_col] < 0: return -1
    return best_col

def process_file(uploaded_file):
    filename = uploaded_file.name
    results = {k: {"val": None, "display": ""} for k in TARGET_FIELDS.keys()}
    results["PBBs"] = {"val": None, "display": "", "sum_val": 0}
    results["PBDEs"] = {"val": None, "display": "", "sum_val": 0}
    results["PFAS"] = ""
    results["Date"] = ""
    
    full_text_content = ""
    is_sgs = "SGS" in filename.upper()
    
    with pdfplumber.open(uploaded_file) as pdf:
        # A. 全文掃描 & 日期
        for i, page in enumerate(pdf.pages):
            text = page.extract_text()
            if text:
                full_text_content += text + "\n"
                if "SGS" in text.upper(): is_sgs = True
                if i == 0: results["Date"] = find_date_in_first_page(text)

        sample_ids = find_sample_ids(full_text_content[:3000])
        results["PFAS"] = check_pfas_in_section(full_text_content)

        # --- 軌道 A: PBBs/PBDEs 視覺座標掃描 (Visual Engine) ---
        # 遍歷每一頁，使用 extract_words 獲取座標
        for page in pdf.pages:
            words = page.extract_words()
            
            # 掃描 PBBs
            pbb_vals = extract_visual_row_values(words, PBBS_ROOTS)
            if pbb_vals:
                val = pbb_vals[-1] # 取該行最後一個有效值
                if val > 0:
                    results["PBBs"]["sum_val"] += val
                    results["PBBs"]["val"] = 1
            
            # 掃描 PBDEs
            pbde_vals = extract_visual_row_values(words, PBDES_ROOTS)
            if pbde_vals:
                val = pbde_vals[-1]
                if val > 0:
                    results["PBDEs"]["sum_val"] += val
                    results["PBDEs"]["val"] = 1

        # --- 軌道 B: 重金屬/單項 表格定位 (V17) ---
        for page in pdf.pages:
            tables = page.extract_tables()
            if not tables: continue
            
            for table in tables:
                if not table or len(table) < 2: continue
                
                header_row_idx = -1
                result_col_idx = -1
                
                for r_idx, row in enumerate(table[:6]):
                    row_str = " ".join([str(c).upper() for c in row if c])
                    if ("ITEM" in row_str or "项目" in row_str or "TEST" in row_str) and \
                       ("UNIT" in row_str or "MDL" in row_str or "LIMIT" in row_str or "RESULT" in row_str):
                        header_row_idx = r_idx
                        result_col_idx = get_column_score(row, sample_ids, is_sgs)
                        break
                
                if header_row_idx == -1: continue
                
                for r_idx in range(header_row_idx + 1, len(table)):
                    row = table[r_idx]
                    if not row: continue
                    
                    item_name = clean_text(row[0])
                    if len(row) > 1: item_name += " " + clean_text(row[1])
                    item_upper = item_name.upper()
                    
                    for field, config in TARGET_FIELDS.items():
                        for kw in config["keywords"]:
                            if re.search(kw, item_upper, re.IGNORECASE):
                                if field == "Chlorine" and ("POLYVINYL" in item_upper or "PVC" in item_upper): continue
                                
                                val_text = ""
                                if result_col_idx != -1 and len(row) > result_col_idx:
                                    val_text = clean_text(row[result_col_idx])
                                else:
                                    val_text = clean_text(row[-1]) 
                                
                                is_strict = (field in ["Chlorine", "Bromine", "PFOS"])
                                v_num, v_disp = extract_value_logic(val_text, strict_numeric=is_strict)
                                
                                if v_num is not None:
                                    if v_num in [1000] and v_disp != "N.D.": continue
                                    
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
        results["PBBs"]["display"] = ""
    else:
        results["PBBs"]["display"] = "N.D."

    if results["PBDEs"]["sum_val"] > 0:
        results["PBDEs"]["display"] = str(round(results["PBDEs"]["sum_val"], 2))
    elif results["PBDEs"]["val"] is None:
        results["PBDEs"]["display"] = ""
    else:
        results["PBDEs"]["display"] = "N.D."

    # 安全排序 (V17.1 Fix)
    valid_vals = [v["val"] for k, v in results.items() if isinstance(v, dict) and v["val"] is not None]
    sort_max = max(valid_vals) if valid_vals else 0

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
        "_sort_pb": results["Lead"]["val"] if results["Lead"]["val"] is not None else 0,
        "_sort_max": sort_max
    }
    
    return final_output, None

# --- 主介面 ---
uploaded_files = st.file_uploader("請上傳 PDF 檢測報告 (支援 SGS, CTI, Intertek 等)", type="pdf", accept_multiple_files=True)

if uploaded_files:
    all_data = []
    scanned_files = []

    with st.spinner('正在進行 V17 視覺引擎分析 (視覺座標 + 字典擴充)...'):
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
        
        st.success(f"✅ 成功擷取 {len(all_data)} 份報告！(V17 核心)")
        st.dataframe(display_df, use_container_width=True)
        
        csv = display_df.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            label="📥 下載 Excel/CSV 報表",
            data=csv,
            file_name="rohs_report_v17_visual.csv",
            mime="text/csv",
        )

    if scanned_files:
        st.error("⚠️ 以下檔案為掃描圖片 (無法擷取文字)：")
        for f in scanned_files:
            st.write(f"- {f}")
else:
    st.info("請上傳 PDF 檔案以開始分析。")
