import streamlit as st
import pdfplumber
import pandas as pd
import re
import io
import os

# ==========================================
# 核心邏輯區 (ReportParserV55)
# ==========================================
class ReportParserV55:
    def __init__(self):
        self.target_map = {
            'Pb': r'Lead|Pb|鉛',
            'Cd': r'Cadmium|Cd|鎘',
            'Hg': r'Mercury|Hg|汞',
            'Cr6+': r'Hexavalent Chromium|Cr\(VI\)|六價鉻',
            'PFOA': r'Perfluorooctanoic acid|PFOA',
            'PFOS': r'Perfluorooctane sulfonic acid|PFOS',
            'PFAS_General': r'Total Fluorine|PFAS'
        }
        self.unit_keywords = ['mg/kg', 'ppm', 'ug/l', 'wt%', '%']

    def clean_text(self, text):
        if not text: return ""
        return str(text).replace('\n', ' ').strip()

    def is_valid_result(self, value):
        if not value: return False
        val = str(value).replace(' ', '').upper()
        if val in ['ND', 'N.D.', 'NEGATIVE', 'NOTDETECTED', 'N.D']: return True
        try:
            val_clean = val.replace('<', '').replace('>', '')
            num = float(val_clean)
            if val_clean in ['2', '5', '8', '10', '50', '100', '1000']: 
                return False 
            return True
        except ValueError:
            return False

    def get_dynamic_column_indices(self, table_header):
        indices = []
        sample_id_pattern = re.compile(r'^(NO\.)?\d{2,3}$|^[A-Z]\d{1,2}$|^RESULT', re.I)
        unit_idx = -1
        for i, cell in enumerate(table_header):
            cell_txt = self.clean_text(cell).upper()
            if sample_id_pattern.search(cell_txt):
                indices.append(i)
            if any(u.upper() in cell_txt for u in self.unit_keywords):
                unit_idx = i
        if not indices and unit_idx != -1:
            indices.append(unit_idx + 1)
        return sorted(list(set(indices)))

    def parse_smart_table(self, tables):
        data = {k: "" for k in self.target_map.keys()}
        for table in tables:
            if not table or len(table) < 2: continue
            clean_table = [[self.clean_text(cell) for cell in row] for row in table]
            data_cols = self.get_dynamic_column_indices(clean_table[0])
            if not data_cols and len(clean_table) > 1:
                data_cols = self.get_dynamic_column_indices(clean_table[1])

            for row in clean_table:
                row_str = " ".join(row).upper()
                for key, pattern in self.target_map.items():
                    if key == 'PFAS_General' and ('PFOA' in row_str or 'PFOS' in row_str):
                        continue
                    if re.search(pattern, row_str, re.I) and data[key] == "":
                        found_val = ""
                        for idx in data_cols:
                            if idx < len(row) and self.is_valid_result(row[idx]):
                                found_val = row[idx]
                                break
                        if not found_val:
                            for cell in reversed(row):
                                if self.is_valid_result(cell):
                                    found_val = cell
                                    break
                        data[key] = found_val
        return data

    def process_stream(self, uploaded_file):
        """處理 Streamlit 上傳的文件流"""
        filename = uploaded_file.name
        try:
            with pdfplumber.open(uploaded_file) as pdf:
                first_page_text = pdf.pages[0].extract_text() or ""
                lab_type = "INTERTEK" if "INTERTEK" in first_page_text.upper() else "SGS/CTI"
                
                all_tables = []
                for page in pdf.pages:
                    tables = page.extract_tables()
                    if tables: all_tables.extend(tables)
                
                extracted_data = self.parse_smart_table(all_tables)
                result = {"檔案名稱": filename, "實驗室": lab_type}
                result.update(extracted_data)
                return result
        except Exception as e:
            return {"檔案名稱": filename, "實驗室": "Error", "Pb": f"讀取錯誤: {str(e)}"}

# ==========================================
# Streamlit 網頁介面區
# ==========================================
def main():
    st.set_page_config(page_title="化學報告 PDF 解析器", layout="wide")
    
    st.title("🧪 化學分析報告 PDF 自動解析器 (V55.0)")
    st.markdown("""
    本工具支援 **SGS、Intertek、CTI** 報告，可自動識別樣品編號（如 001, A1）並提取關鍵數值。
    1. 上傳多份 PDF 報告。
    2. 檢查下方預覽表格。
    3. 點擊按鈕下載 Excel 總表。
    """)

    uploaded_files = st.file_uploader("選擇 PDF 檔案 (可多選)", type="pdf", accept_multiple_files=True)

    if uploaded_files:
        parser = ReportParserV55()
        all_results = []
        
        with st.spinner(f'正在解析 {len(uploaded_files)} 份檔案，請稍候...'):
            for uploaded_file in uploaded_files:
                data = parser.process_stream(uploaded_file)
                all_results.append(data)
        
        # 顯示結果表格
        df = pd.DataFrame(all_results)
        # 重新排序欄位
        cols_order = ['檔案名稱', '實驗室', 'Pb', 'Cd', 'Hg', 'Cr6+', 'PFOA', 'PFOS', 'PFAS_General']
        existing_cols = [c for c in cols_order if c in df.columns]
        df = df[existing_cols]

        st.success("解析完成！")
        st.dataframe(df, use_container_width=True)

        # 準備 Excel 下載
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        
        st.download_button(
            label="📥 下載 Excel 結果表",
            data=output.getvalue(),
            file_name="化學報告解析結果.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

if __name__ == "__main__":
    main()