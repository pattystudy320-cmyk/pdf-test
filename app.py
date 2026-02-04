# ==========================================
# 🇲🇾 馬來西亞專用模組 (SGS Malaysia Parser)
# ==========================================

# 定義馬來西亞版專用的 MDL 黑名單
MY_MDL_BLOCKLIST = {
    "Pb": [2.0], "Cd": [2.0], "Hg": [2.0], "CrVI": [8.0, 10.0],
    "F": [50.0], "CL": [50.0], "BR": [50.0], "I": [50.0],
    "DEHP": [50.0], "BBP": [50.0], "DBP": [50.0], "DIBP": [50.0]
}

def is_malaysia_report(text):
    """偵測是否為馬來西亞 SGS 報告"""
    return "MALAYSIA" in text.upper() and "SGS" in text.upper()

def extract_result_malaysia(text, keyword, item_name):
    """
    從 V7 版移植過來的核心邏輯
    專門處理馬來西亞排版 (隱形 N.D.、DEHP 跨行、MDL 誤抓)
    """
    lines = text.splitlines()

    for i, line in enumerate(lines):
        if re.search(keyword, line, re.IGNORECASE):
            
            # 1. DEHP 特例: 擴大讀取 4 行
            if item_name == "DEHP":
                context = " ".join(lines[i:i+4])
            else:
                context = " ".join(lines[i:i+2])

            # 2. 除噪: 刪除內鬼與雜訊
            if item_name == "DEHP":
                context = re.sub(r"2-ethylhexyl", " ", context, flags=re.IGNORECASE)
                context = re.sub(r"Di\(2-", " ", context, flags=re.IGNORECASE)
            
            context = re.sub(r"mg/kg|ppm|%|wt%", " ", context, flags=re.IGNORECASE)
            context = re.sub(r"\(?CAS\s*No\.?[\s\d-]+\)?", " ", context, flags=re.IGNORECASE)
            context = re.sub(r"IEC\s*62321[-\d:+A]*", " ", context, flags=re.IGNORECASE)
            context = re.sub(r"\b(19|20)\d{2}\b", " ", context) 
            context = re.sub(r"(Max|Limit|MDL|LOQ)\s*\d+(\.\d+)?", " ", context, flags=re.IGNORECASE)

            # 3. N.D. 判定
            nd_pattern = r"(\bN\s*\.?\s*D\s*\.?\b)|(Not\s*Detected)"
            if re.search(nd_pattern, context, re.IGNORECASE):
                return "N.D."
            if re.search(r"NEGATIVE", context, re.IGNORECASE):
                return "NEGATIVE"

            # 4. 數字抓取與黑名單
            nums = re.findall(r"\b\d+(?:\.\d+)?\b", context)
            if not nums: return "N.D."

            final_val = None
            
            # PBBs/PBDEs 特權 (MDL 為 -)
            if item_name in ["PBBs", "PBDEs"]:
                final_val = nums[0]
            else:
                if len(nums) >= 2:
                    # 防呆年份殘渣
                    candidate = nums[0]
                    try:
                        f_val = float(candidate)
                        if 1990 <= f_val <= 2030 and f_val.is_integer(): candidate = nums[1]
                    except: pass
                    final_val = candidate
                elif len(nums) == 1:
                    return "N.D."

            # 5. 黑名單過濾
            if final_val:
                try:
                    val_float = float(final_val)
                    if item_name in MY_MDL_BLOCKLIST:
                        if val_float in MY_MDL_BLOCKLIST[item_name]:
                            return "N.D."
                    return final_val
                except: pass
    return ""
