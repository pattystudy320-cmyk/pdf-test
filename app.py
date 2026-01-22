import pdfplumber
import os
import pandas as pd
import re
import io
import streamlit as st # 確保您有引入 streamlit

class ReportParserV55:
    def __init__(self):
        # 1. 定義目標化學物質關鍵字 (Regex 支援模糊匹配)
        # 增加了 PBBs, PBDEs, PAEs (DEHP, DBP, BBP, DIBP), 鹵素 (F, Cl, Br)
        self.target_map = {
            'Pb': r'Lead|Pb|鉛',
            'Cd': r'Cadmium|Cd|鎘',
            'Hg': r'Mercury|Hg|汞',
            'Cr6+': r'Hexavalent Chromium|Cr\(VI\)|六價鉻',
            'PBB': r'Polybrominated Biphenyls?|PBBs?', # 處理群組名稱
            'PBDE': r'Polybrominated Diphenyl Ethers?|PBDEs?', # 處理群組名稱
            'DEHP': r'Di\(2-ethylhexyl\) phthalate|DEHP|鄰苯二甲酸二\(2-乙基己基\)酯',
            'DBP': r'Dibutyl phthalate|DBP|鄰苯二甲酸二丁酯',
            'BBP': r'Butyl benzyl phthalate|BBP|鄰苯二甲酸丁基苯甲酯',
            'DIBP': r'Diisobutyl phthalate|DIBP|鄰苯二甲酸二異丁酯',
            'F': r'Fluorine|F|氟',
            'Cl': r'Chlorine|Cl|氯',
            'Br': r'Bromine|Br|溴',
            'PFOA': r'Perfluorooctanoic acid|PFOA',
            'PFOS': r'Perfluorooctane sulfonic acid|PFOS',
            'PFAS_General': r'Total Fluorine|PFAS'
        }
        # 常見單位，用來作為定位錨點
        self.unit_keywords = ['mg/kg', 'ppm', 'ug/l', 'wt%', '%']
        # 所有需要的欄位名稱，用於確保 DataFrame 順序
        self.all_fields = ['檔案名稱', '實驗室', 'DATE', 'Pb', 'Cd', 'Hg', 'Cr6+', 'PBB', 'PBDE', 'DEHP', 'DBP', 'BBP', 'DIBP', 'F', 'Cl', 'Br', 'PFOA', 'PFOS', 'PFAS_General']

    def clean_text(self, text):
        if not text: return ""
        return str(text).replace('\n', ' ').strip()

    def is_valid_result(self, value):
        """判斷是否為檢測結果 (含 ND 或 有效數字)"""
        if not value: return False
        val = str(value).replace(' ', '').upper()
        
        # 允許的非數字結果
        if val in ['ND', 'N.D.', 'NEGATIVE', 'NOTDETECTED', 'N.D']: return True
        
        try:
            # 移除 < 或 > 符號後判斷是否為數字
            val_clean = val.replace('<', '').replace('>', '')
            num = float(val_clean)
            
            # 排除常見的 MDL 或 Limit 數字干擾 (可視需求調整)
            if val_clean in ['2', '5', '8', '10', '50', '100', '1000']: 
                return False 
            return True
        except ValueError:
            return False

    def get_dynamic_column_indices(self, table_header):
        """
        [優化重點]：動態尋找數據所在欄位索引
        不只找 'Result'，也找 '001', 'A1' 或位在 'Unit' 之後的欄位
        """
        indices = []
        sample_id_pattern = re.compile(r'^(NO\.)?\d{2,3}$|^[A-Z]\d{1,2}$|^RESULT', re.I)
        
        unit_idx = -1
        for i, cell in enumerate(table_header):
            cell_txt = self.clean_text(cell).upper()
            
            # 1. 匹配樣品編號 (如 001, A1) 或標題 Result
            if sample_id_pattern.search(cell_txt):
                indices.append(i)
            
            # 2. 記錄 Unit 單位欄位置
            if any(u.upper() in cell_txt for u in self.unit_keywords):
                unit_idx = i
        
        # 3. 如果沒找到樣品編號，通常 Unit 的下一欄就是結果
        if not indices and unit_idx != -1:
            indices.append(unit_idx + 1)
            
        return sorted(list(set(indices)))

    def parse_smart_table(self, tables):
        """通用智能解析邏輯"""
        # 初始化字典，確保所有欄位都在
        data = {k: "" for k in self.target_map.keys()}
        
        for table in tables:
            if not table or len(table) < 2: continue
            
            # 預先清理整張表
            clean_table = [[self.clean_text(cell) for cell in row] for row in table]
            
            # 第一步：嘗試從前兩行找出數據欄位索引 (Index)
            data_cols = self.get_dynamic_column_indices(clean_table[0])
            if not data_cols and len(clean_table) > 1:
                data_cols = self.get_dynamic_column_indices(clean_table[1])

            # 第二步：逐行比對關鍵字
            for row in clean_table:
                row_str = " ".join(row).upper()
                
                for key, pattern in self.target_map.items():
                    # 避免 PFAS 誤抓 PFOA/PFOS
                    if key == 'PFAS_General' and ('PFOA' in row_str or 'PFOS' in row_str):
                        continue

                    # 若匹配到關鍵字且該項尚未有值
                    if re.search(pattern, row_str, re.I) and data[key] == "":
                        found_val = ""
                        
                        # 優先從定位到的欄位取值
                        for idx in data_cols:
                            if idx < len(row) and self.is_valid_result(row[idx]):
                                found_val = row[idx]
                                break
                        
                        # 若定位失敗，回退至由後往前搜尋
                        if not found_val:
                            for cell in reversed(row):
                                if self.is_valid_result(cell):
                                    found_val = cell
                                    break
                        
                        data[key] = found_val
        return data

    def process_file(self, file_path):
        filename = os.path.basename(file_path)
        try:
            with pdfplumber.open(file_path) as pdf:
                first_page_text = pdf.pages[0].extract_text() or ""
                lab_type = "SGS/CTI"
                if "INTERTEK" in first_page_text.upper(): lab_type = "INTERTEK"
                
                all_tables = []
                for page in pdf.pages:
                    tables = page.extract_tables()
                    if tables: all_tables.extend(tables)
                
                extracted_data = self.parse_smart_table(all_tables)

                # --- 新增：提取報告日期 ---
                report_date = ""
                # 嘗試用正則表達式尋找日期 (常見格式 YYYY-MM-DD 或 DD MMM YYYY)
                date_match = re.search(r'\d{1,4}[-/年]\d{1,2}[-/月]\d{1,4}日?', first_page_text)
                if date_match:
                    report_date = date_match.group(0).replace("年", "-").replace("月", "-").replace("日", "")

                # 確保結果字典包含所有需要的欄位
                result = {field: "" for field in self.all_fields}
                result.update({"檔案名稱": filename, "實驗室": lab_type, "DATE": report_date})
                # 將解析到的數據合併
                for key, value in extracted_data.items():
                    if key in result:
                        result[key] = value

                return result
                
        except Exception as e:
            # 錯誤發生時，回傳包含錯誤訊息的字典，同時確保欄位齊全
            error_dict = {field: "" for field in self.all_fields}
            error_dict.update({"檔案名稱": filename, "實驗室": "Error", "Pb": f"錯誤: {str(e)}"})
            return error_dict

# ==========================================
# Streamlit 執行區
# ==========================================
if __name__ == "__main__":
    st.title("📄 化學分析報告 PDF 自動解析器 (V55.0)")
    st.write("本工具支援SGS、Intertek、CTI報告，請上傳 PDF 檔案以開始分析。")

    # 建立上傳元件
    uploaded_files = st.file_uploader("選擇 PDF 檔案", type="pdf", accept_multiple_files=True)

    if uploaded_files:
        parser = ReportParserV55()
        all_results = []
        
        progress_bar = st.progress(0)
        for i, uploaded_file in enumerate(uploaded_files):
            # 將上傳的檔案暫存到本地以便讀取
            with open(uploaded_file.name, "wb") as f:
                f.write(uploaded_file.getbuffer())
            
            res = parser.process_file(uploaded_file.name)
            all_results.append(res)
            
            # 更新進度條
            progress_bar.progress((i + 1) / len(uploaded_files))
            # 刪除暫存檔
            os.remove(uploaded_file.name)

        df = pd.DataFrame(all_results)
        
        # 確保 Excel 欄位順序正確
        df = df[[c for c in parser.all_fields if c in df.columns]]

        # 顯示結果預覽
        st.subheader("📊 解析結果預覽")
        st.dataframe(df)

        # 製作 Excel 下載按鈕
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        
        st.download_button(
            label="📥 下載 Excel 結果檔",
            data=output.getvalue(),
            file_name="Analysis_Results.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
