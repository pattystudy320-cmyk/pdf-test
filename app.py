import streamlit as st
import pdfplumber
import pandas as pd
import re
from datetime import datetime
import io

# --- 頁面設定 ---
st.set_page_config(page_title="RoHS/REACH 報告彙整工具", layout="wide")
st.title("📄 化學檢測報告數據自動彙整工具")
st.markdown("""
本工具支援 SGS 與 CTI 格式報告。
**邏輯說明：**
1. **數值取樣：** 多份報告中取最大值 (數字 > N.D.)。
2. **PFAS 判斷：** 僅當「Test Requested/檢測要求」欄位明確出現 "PFAS" 字串時顯示 "REPORT"。
3. **FILE NAME：** 顯示鉛 (Pb) 數值最高的來源檔名。
""")

# --- 核心關鍵字映射 ---
# 根據上傳的文件內容優化關鍵字
KEYWORDS_MAP = {
    'Pb': ['Lead', 'Pb', '铅'],
    'Cd': ['Cadmium', 'Cd', '镉'],
    'Hg': ['Mercury', 'Hg', '汞'],
    'Cr6+': ['Hexavalent Chromium', 'Cr(VI)', 'Cr6+', '六价铬'],
    'PBB': ['PBBs', 'Polybrominated biphenyls', 'Sum of PBBs', '多溴联苯'],
    'PBDE': ['PBDEs', 'Polybrominated diphenyl ethers', 'Sum of PBDEs', '多溴二苯醚'],
    'DEHP': ['DEHP', 'Bis(2-ethylhexyl) phthalate', '邻苯二甲酸二(2-乙基己基)酯'],
    'DBP':  ['DBP', 'Dibutyl phthalate', '邻苯二甲酸二丁酯'],
    'BBP':  ['BBP', 'Butyl benzyl phthalate', '邻苯二甲酸丁苄酯'],
    'DIBP': ['DIBP', 'Diisobutyl phthalate', '邻苯二甲酸二异丁酯'],
    'F':    ['Fluorine', 'Halogen-Fluorine', '氟', 'Fluorine (F)'],
    'CL':   ['Chlorine', 'Halogen-Chlorine', '氯', 'Chlorine (Cl)'],
    'BR':   ['Bromine', 'Halogen-Bromine', '溴', 'Bromine (Br)'],
    'PFOS': ['Perfluorooctane sulfonates', 'PFOS', '全氟辛烷磺酸'],
}

# --- 輔助函式：日期解析 ---
def parse_date(date_str):
    """
    解析多種日期格式，統一回傳 datetime 物件
    支援格式: 
    - Feb 27, 2025 (SGS)
    - 2025.06.16 (CTI)
    - 27-Feb-2025 (CTI)
    """
    if not date_str:
        return None
    
    date_str = date_str.strip()
    # 定義常見日期格式
    formats = [
        "%b %d, %Y",      # Feb 27, 2025
        "%Y.%m.%d",       # 2025.06.16
        "%d-%b-%Y",       # 27-Feb-2025
        "%Y/%m/%d",
        "%Y-%m-%d",
        "%Y年%m月%d日"
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None

# --- 核心函式：單一 PDF 解析 ---
def extract_pdf_data(file_obj, filename):
    data = {key: "N.D." for key in KEYWORDS_MAP.keys()}
    data['PFAS'] = ""
    data['DATE'] = None
    data['Filename'] = filename
    
    full_text = ""
    header_text = "" # 用於搜尋 Test Requested 和日期

    try:
        with pdfplumber.open(file_obj) as pdf:
            # 1. 讀取頁面內容
            for i, page in enumerate(pdf.pages):
                text = page.extract_text()
                if text:
                    full_text += text + "\n"
                    if i < 3: # 通常關鍵資訊在前 3 頁
                        header_text += text + "\n"

            # 2. 提取日期 (Date)
            # Regex 針對 SGS 和 CTI 格式進行匹配
            date_patterns = [
                r"Date:\s*([A-Z][a-z]{2}\s\d{1,2},\s\d{4})",  # SGS: Date: Feb 27, 2025
                r"Date:\s*(\d{4}\.\d{2}\.\d{2})",             # CTI: Date: 2025.06.16
                r"Date:\s*(\d{2}-[A-Z][a-z]{2}-\d{4})",       # CTI: Date: 27-Feb-2025
                r"日期：\s*(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)"
            ]
            
            for pat in date_patterns:
                match = re.search(pat, header_text)
                if match:
                    dt = parse_date(match.group(1))
                    if dt:
                        data['DATE'] = dt
                        break

            # 3. 提取化學物質數值
            # 逐行掃描，尋找 "關鍵字 ... 數值" 的模式
            lines = full_text.split('\n')
            for line in lines:
                for key, keywords in KEYWORDS_MAP.items():
                    # 優化：F, Cl, Br 容易誤判，需增加邊界檢查或確保不是單字的一部分
                    for kw in keywords:
                        if kw in line:
                            # 尋找行尾的數值或 N.D.
                            # 邏輯：抓取 "N.D." 或 "ND" 或 數字 (排除年份 20xx)
                            # Regex 說明: 
                            # (N\.D\.|ND) -> 抓取未檢出
                            # (\d+(?:\.\d+)?) -> 抓取數字
                            # 排除掉前面有 "ISO" 或 "IEC" 的數字 (方法編號)
                            if "ISO" in line or "IEC" in line or "EPA" in line:
                                continue

                            # 尋找測試結果
                            # 這裡假設結果通常在行的後段
                            result_match = re.search(r"(N\.D\.|ND|Negative|<[\d\.]+|\d+(?:\.\d+)?)", line.split(kw)[-1])
                            
                            if result_match:
                                val_str = result_match.group(1)
                                
                                # 判斷是否為有效數值
                                if re.match(r"^\d", val_str): # 是數字
                                    try:
                                        val_num = float(val_str)
                                        # 過濾年份 (例如 2025) 或法規編號
                                        if val_num > 1980 and val_num < 2100 and key not in ['BR', 'CL', 'F']:
                                            continue
                                        
                                        # 比較大小，保留最大值 (處理同份報告多個測試點的情況)
                                        current_val = data[key]
                                        if current_val == "N.D." or current_val == "Negative":
                                            data[key] = val_num
                                        elif isinstance(current_val, (int, float)):
                                            if val_num > current_val:
                                                data[key] = val_num
                                    except:
                                        pass
                                elif "Negative" in val_str:
                                     # Negative 視為 N.D.，除非已有數字
                                     pass 

            # 4. 判斷 PFAS
            # 邏輯：檢查 "Test Requested" 或 "检测要求" 區塊是否包含 "PFAS" 字串
            # 先找到 Header 區塊
            req_match = re.search(r"(Test Requested|检测要求|Test Conducted)([\s\S]{1,500})", header_text, re.IGNORECASE)
            if req_match:
                content = req_match.group(0)
                if "PFAS" in content:
                    data['PFAS'] = "REPORT"
            # 若無 PFAS 字串，保持空白

    except Exception as e:
        st.error(f"解析檔案 {filename} 時發生錯誤: {str(e)}")
        return None
        
    return data

# --- 核心函式：數據加總與彙整 ---
def aggregate_reports(extracted_list):
    if not extracted_list:
        return None

    # 初始化結果 Row
    final_data = {key: "N.D." for key in KEYWORDS_MAP.keys()}
    final_data['PFAS'] = ""
    final_data['DATE'] = None
    final_data['FILE NAME'] = ""

    max_pb = -1.0 # 用於追蹤最大鉛含量
    latest_date = datetime.min

    for item in extracted_list:
        fname = item['Filename']
        
        # 1. 日期取最新
        if item['DATE'] and item['DATE'] > latest_date:
            latest_date = item['DATE']
            
        # 2. PFAS 判斷 (聯集：只要有一份是 REPORT 就顯示)
        if item['PFAS'] == "REPORT":
            final_data['PFAS'] = "REPORT"

        # 3. 數值取最大 (數字 > N.D.)
        # 特別處理 Pb 以決定 FILE NAME
        pb_val = item['Pb']
        current_pb_num = 0.0
        
        if isinstance(pb_val, (int, float)):
            current_pb_num = pb_val
        
        # 更新 Pb 最大值與對應檔名
        if current_pb_num > max_pb:
            max_pb = current_pb_num
            final_data['FILE NAME'] = fname
        elif current_pb_num == max_pb and final_data['FILE NAME'] == "":
            final_data['FILE NAME'] = fname # 處理都是 N.D. 的情況，取第一份

        # 處理所有化學物質
        for key in KEYWORDS_MAP.keys():
            val = item[key]
            # 如果新值是數字
            if isinstance(val, (int, float)):
                # 如果舊值也是數字，取大者
                if isinstance(final_data[key], (int, float)):
                    if val > final_data[key]:
                        final_data[key] = val
                # 如果舊值是 N.D.，直接覆蓋
                else:
                    final_data[key] = val
            # 如果新值是 N.D.，不動作 (保留可能的舊數字)

    # 4. 格式化日期
    if latest_date != datetime.min:
        final_data['DATE'] = latest_date.strftime("%Y/%m/%d")
    else:
        final_data['DATE'] = ""

    return final_data

# --- Streamlit UI 主程式 ---
uploaded_files = st.file_uploader("請上傳 PDF 測試報告 (SGS/CTI)", type="pdf", accept_multiple_files=True)

if uploaded_files:
    if st.button("開始分析與彙整"):
        all_data = []
        progress_bar = st.progress(0)
        
        for idx, file in enumerate(uploaded_files):
            # 解析
            result = extract_pdf_data(file, file.name)
            if result:
                all_data.append(result)
            progress_bar.progress((idx + 1) / len(uploaded_files))
            
        if all_data:
            # 加總
            summary_row = aggregate_reports(all_data)
            
            # 轉為 DataFrame
            df = pd.DataFrame([summary_row])
            
            # 調整欄位順序
            cols = ['FILE NAME', 'Pb', 'Cd', 'Hg', 'Cr6+', 'PBB', 'PBDE', 
                    'DEHP', 'DBP', 'BBP', 'DIBP', 
                    'F', 'CL', 'BR', 'PFOS', 'PFAS', 'DATE']
            df = df[cols]
            
            st.success("彙整完成！")
            st.dataframe(df)
            
            # 下載 CSV
            csv = df.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                label="📥 下載 Excel/CSV 報表",
                data=csv,
                file_name="Summary_Report.csv",
                mime="text/csv"
            )
        else:
            st.warning("無法提取數據，請確認 PDF 格式。")
