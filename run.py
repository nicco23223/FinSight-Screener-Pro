
import os
import io
import pandas as pd
import streamlit as st
import numpy as np
import sys
import subprocess
import re

st.set_page_config(page_title="Dividend & Fundamentals Filter", layout="wide")

# -------------------------
# Session state bootstrap
# -------------------------
if "started" not in st.session_state:
    st.session_state.started = False
if "api_key" not in st.session_state:
    st.session_state.api_key = ""

# -------------------------
# Helpers
# -------------------------
def _find_col_by_keywords(df: pd.DataFrame, keywords):
    """
    Return the first matching column name whose lowercased name contains ALL keywords (as substrings).
    keywords: list[str] (lowercased substrings to match). Each can also be a tuple for OR among variants.
    """
    cols = list(df.columns)
    lowered = {c: str(c).strip().lower() for c in cols}
    for c in cols:
        lc = lowered[c]
        ok = True
        for kw in keywords:
            if isinstance(kw, (tuple, list)):
                # any of the OR-variants must appear
                if not any(k in lc for k in kw):
                    ok = False
                    break
            else:
                if kw not in lc:
                    ok = False
                    break
        if ok:
            return c
    return None

@st.cache_data(show_spinner=False)
def load_dataframe(file_path: str | None, uploaded_file) -> pd.DataFrame | None:
    """Load a DataFrame from a path or an uploaded file. Supports CSV and Excel."""
    try:
        if uploaded_file is not None:
            name = uploaded_file.name.lower()
            if name.endswith(".csv"):
                return pd.read_csv(uploaded_file)
            elif name.endswith((".xlsx", ".xls")):
                return pd.read_excel(uploaded_file)
            else:
                st.warning("只支援 .csv/.xlsx/.xls。")
                return None
        if file_path:
            lp = file_path.strip()
            if not os.path.exists(lp):
                st.warning(f"找不到檔案：{lp}")
                return None
            if lp.lower().endswith(".csv"):
                return pd.read_csv(lp)
            elif lp.lower().endswith((".xlsx", ".xls")):
                return pd.read_excel(lp)
            else:
                st.warning("只支援 .csv/.xlsx/.xls。")
                return None
    except Exception as e:
        st.error(f"reading error：{e}")
        return None
    return None


def coerce_bool(series: pd.Series) -> pd.Series:
    """
    將 bool / 整數 / 'true'/'false'/'yes'/'no' 等統一轉成 pandas BooleanDtype。
    """
    # 已經是 bool
    if pd.api.types.is_bool_dtype(series):
        return series.astype("boolean")

    # 數值型：0 -> False，其它非空 -> True
    if pd.api.types.is_numeric_dtype(series):
        return series.map(
            lambda x: (pd.notna(x) and float(x) != 0) if pd.notna(x) else pd.NA
        ).astype("boolean")

    # 文字型：常見 true/false 字串
    s = series.astype(str).str.strip().str.lower()
    true_set = {"true", "1", "yes", "y", "t"}
    false_set = {"false", "0", "no", "n", "f", ""}
    out = s.map(lambda x: True if x in true_set else (False if x in false_set else pd.NA))
    return out.astype("boolean")


def _find_col(df: pd.DataFrame, candidates) -> str | None:
    """
    在 df 中用不分大小寫方式尋找欄位名，回傳實際欄位名或 None。
    candidates 可以是單一字串或字串列表。
    """
    if isinstance(candidates, str):
        candidates = [candidates]
    lower_map = {c.lower(): c for c in df.columns}
    for c in candidates:
        key = c.lower()
        if key in lower_map:
            return lower_map[key]
    return None

# -------------------------
# Callback for clearing filters
# -------------------------
def clear_all_filters_callback():
    """Clear all filter state variables."""
    if "mcap_choice" in st.session_state:
        st.session_state["mcap_choice"] = []
    if "status_choice" in st.session_state:
        st.session_state["status_choice"] = []
    if "sector_choice" in st.session_state:
        st.session_state["sector_choice"] = []
    if "eq_choice" in st.session_state:
        st.session_state["eq_choice"] = "All"
    if "debt_choice" in st.session_state:
        st.session_state["debt_choice"] = "All"
    if "qv_eq_choice" in st.session_state:
        st.session_state["qv_eq_choice"] = "All"
    if "qv_db_choice" in st.session_state:
        st.session_state["qv_db_choice"] = "All"
    if "cross_choice" in st.session_state:
        st.session_state["cross_choice"] = "All"
    if "trend_choice" in st.session_state:
        st.session_state["trend_choice"] = "All"
    if "macd_choice" in st.session_state:
        st.session_state["macd_choice"] = "All"
    if "price_ema_choice" in st.session_state:
        st.session_state["price_ema_choice"] = "All"
    if "volume_min" in st.session_state:
         st.session_state["volume_min"] = 0
    if "net_income_q_choice" in st.session_state:
        st.session_state["net_income_q_choice"] = "All"
    if "net_income_y1_choice" in st.session_state:
        st.session_state["net_income_y1_choice"] = "All"
    if "ticker_multi_input" in st.session_state:
        st.session_state["ticker_multi_input"] = []
    if "__selected_tickers__" in st.session_state:
        st.session_state["__selected_tickers__"] = []

# -------------------------
# Landing (Start gate)
# -------------------------
st.title("Stock Screening | Filtering Interface")

st.markdown(
    "<br>This application provides a data-driven environment for exploring and evaluating equity datasets."
    " Users can upload or generate stock data and apply a configurable set of screening parameters."
    " All filters are applied dynamically, and the resulting subset can be exported as **filtered_stocks.csv** "
    "for downstream analysis or integration into external workflows.<br>"
    "The platform also includes an optimized preset configuration derived from our multi-factor evaluation "
    "framework, providing a reproducible baseline for systematic equity selection.",
    unsafe_allow_html=True
)


if not st.session_state.started:
    with st.container(border=True):
        st.subheader("Start")
        st.write("Click **Start** below to proceed to settings and filtering.")
        if st.button("Start", use_container_width=True, type="primary"):
            st.session_state.started = True
    st.stop()

# ---- 可選：快取資料生成 ----
def generate_csv_in_app(api_key: str | None = None,
                        limit_stocks: int | None = None) -> pd.DataFrame:
    """
    作法一（動態載入 capstone 腳本）：
    - 不改 capstone 原始檔，僅在此函式內動態載入其函式定義，並呼叫 run_full_market_inputs_with_reason(...)
    - 自動跳過 capstone 底部的「# 執行」示範區塊（避免 import 時就直接跑）
    - 維持原 UI 流程與下載邏輯：回傳 DataFrame，並同時寫出 full_market_inputs_with_reason.csv
    """
    import os, types, inspect, pandas as pd

    # 1) 將 UI 輸入的 API Key 傳遞給 capstone 程式
    if api_key:
        os.environ["POLYGON_API_KEY"] = api_key

    # 2) capstone 檔案路徑（保持與目前檔案同資料夾）
    current_dir = os.path.dirname(os.path.abspath(__file__))
    CAP_PATH = os.path.join(current_dir, "generatecsv.py")


    # 3) 讀取 capstone 原始碼，並在執行前移除底部自動執行區塊
    #    原始檔底部通常有：
    #      # 執行
    #      df_all = run_full_market_inputs_with_reason(...)
    #      display(df_all.head(30))
    #      print("總列數：", len(df_all))
    with open(CAP_PATH, "r", encoding="utf-8", errors="replace") as f:
        src = f.read()

    # 優先用標記「# 執行」來截斷
    cut_markers = ["\n# 執行", "\n#\u57F7\u884C", "\n# Execute", "\n# RUN", "\n# run"]
    cut_pos = -1
    for mk in cut_markers:
        pos = src.find(mk)
        if pos != -1:
            cut_pos = pos
            break

    # 若找不到標記，再嘗試偵測常見的呼叫行
    if cut_pos == -1:
        import re
        m = re.search(r'^\s*df_all\s*=\s*run_full_market_inputs_with_reason\s*\(', src, re.M)
        if m:
            cut_pos = m.start()

    # 真的都找不到就整檔執行（但一般會找到；若整檔執行，capstone 會直接跑，可能花較久）
    safe_src = src if cut_pos == -1 else src[:cut_pos]

    # 4) 建立一個臨時模組，僅注入「函式與常數定義」進來
    cap_mod = types.ModuleType("cap_mod_sandbox")
    cap_mod.__file__ = CAP_PATH
    # 給常見的基本匯入，避免原檔案依賴這些名稱卻未顯式 import
    import math, time, json, datetime
    import builtins
    cap_mod.__dict__.update({
        "__name__": "cap_mod_sandbox",
        "__builtins__": builtins.__dict__,
        "math": math, "time": time, "json": json, "datetime": datetime,
        "pd": pd,  # 很多函式直接用 pd
        "os": os,
    })

    code = compile(safe_src, CAP_PATH, "exec")
    exec(code, cap_mod.__dict__)  # 這裡不會跑到底部示範區塊

    # 5) 取得 run_full_market_inputs_with_reason(...)，並以相容方式傳參
    if not hasattr(cap_mod, "run_full_market_inputs_with_reason"):
        raise RuntimeError("capstone 檔案中找不到 run_full_market_inputs_with_reason(...)")

    run_fn = cap_mod.run_full_market_inputs_with_reason
    sig = inspect.signature(run_fn)

    # 基本參數：輸出檔名與 share classes 過濾
    kwargs = {}
    if "outfile" in sig.parameters:
        kwargs["outfile"] = "full_market_inputs_with_reason.csv"
    if "skip_share_classes" in sig.parameters:
        kwargs["skip_share_classes"] = True

    # 速度與切檔設定（若 capstone 定義了參數，才帶）
    if "max_workers" in sig.parameters:
        # 若 capstone 內有 MAX_WORKERS 常數就沿用，否則給一個合理值
        max_workers = getattr(cap_mod, "MAX_WORKERS", 32)
        kwargs["max_workers"] = max_workers
    if "part_every" in sig.parameters:
        kwargs["part_every"] = 1500

    # 限縮掃描股票數量（若 capstone 支援 limit 參數才帶）
    if "limit" in sig.parameters and limit_stocks:
        kwargs["limit"] = int(limit_stocks)

    # 6) 執行 capstone 的主流程，安全執行
    df = None
    try:
        df = run_fn(**kwargs)
    except Exception as e:
        import traceback
        print("⚠️ 執行 capstone 主程式時發生錯誤：", e)
        traceback.print_exc()

    # 若 df 不存在或不是 DataFrame，嘗試讀取輸出檔
    if df is None or not isinstance(df, pd.DataFrame):
        try:
            df = pd.read_csv("full_market_inputs_with_reason.csv")
            print("✅ 已從 full_market_inputs_with_reason.csv 讀回資料")
        except Exception as e:
            print("❌ 無法取得 DataFrame，也無法讀取 CSV：", e)
            # 回傳空表，避免整個 Streamlit 爆掉
            df = pd.DataFrame()

    return df

# ---------------------- Settings 區塊 ----------------------
with st.expander("Settings", expanded=True):
    st.write("Please enter your Polygon API key (only stored in this session):")
    if "api_key" not in st.session_state or not st.session_state.api_key:
        st.session_state.api_key = "J_wZYB3rGZBaFv2tdyg21X1vmVXrMW21"
    st.session_state.api_key = st.text_input("API Key", value=st.session_state.api_key, type="password")
    st.write("----")
    st.write("Data Source")

    # ★ 僅保留兩種模式：Upload / Generate CSV
    source_mode = st.radio(
        "Choose data source",
        options=["Generate CSV", "Upload file"],
        horizontal=True
    )

    data_file = None
    generated_df = None

    if source_mode == "Upload file":
        data_file = st.file_uploader("Import CSV or Excel file", type=["csv", "xlsx", "xls"])

    else:  # "Generate CSV"
        with st.container(border=True):
            st.write("Generate a CSV in-app and load it directly.")
            gen_now = st.button("Generate CSV now", type="primary", use_container_width=False)
            if gen_now:
                df_gen = generate_csv_in_app(st.session_state.api_key or None, limit_stocks=50)

                st.session_state.generated_df = df_gen

                csv_bytes = df_gen.to_csv(index=False).encode("utf-8")
                st.download_button("Download generated.csv",data=csv_bytes,file_name="generated.csv",mime="text/csv")
        generated_df = st.session_state.get("generated_df", None)

# ---------------------- 資料載入邏輯 ----------------------
# 若已生成資料 → 優先使用
if generated_df is not None:
    df = generated_df

else:
    if data_file is not None:
        df = load_dataframe(None, data_file)
    else:
        df = None

# 若沒有資料則停止
if df is None:
    st.info("Please upload a file or generate CSV before proceeding.")
    st.stop()

# 預覽
st.write("### Raw Data Preview")
st.write(f"total {len(df)}")
st.dataframe(df.head(100), use_container_width=True)


# -------------------------
# Column Detection (Flexible names)
# -------------------------
# Try to infer key column names using keyword heuristics.
# -------------------------
# Column Detection (Compact Style)
# -------------------------
div_gt_eps_col = _find_col_by_keywords(df, keywords=[("div","dividend","股利","配息"), (">","above","高於"), ("eps","earning","盈餘")]) \
    or _find_col_by_keywords(df, keywords=[("dividend","股利","配息"), ("eps","earning","盈餘")])

mcap_col = _find_col_by_keywords(df, keywords=[("Market Cap Class","marketcap","class")])

df.columns = [str(c).strip() for c in df.columns]
qv_eq_col = _find_col_by_keywords(df, keywords=[("q_vs_y1_equity_up","q vs y1 equity up","q_vs_equity_up","q vs equity up")])
qv_db_col = _find_col_by_keywords(df, keywords=[("q_vs_y1_debt_down","q vs y1 debt down","q_vs_debt_down","q vs debt down")])

equity_up_col = _find_col_by_keywords(df, keywords=[("annual_equity"), ("y1>y2")])
debt_down_col = _find_col_by_keywords(df, keywords=[("annual_debt"), ("y1<y2")])

sector_col = _find_col_by_keywords(df, keywords=[("industry")])
vol_col = _find_col_by_keywords(df, keywords=[("avgvolume", "交易量"), ("10d", "前一", "昨日")])
status_col = _find_col_by_keywords(df, keywords=[("status", "評級", "等級", "建議")])

net_income_q_col = _find_col_by_keywords(df, keywords=[("net", "淨"), ("income", "利"), ("q", "季"), (">0", "正")])
net_income_y1_col = _find_col_by_keywords(df, keywords=[("net", "淨"), ("income", "利"), ("y1", "年"), (">0", "正")])

cross_col = _find_col_by_keywords(df, keywords=[("cross", "交叉"), ("golden", "death", "金叉", "死叉")])
trend_col = _find_col_by_keywords(df, keywords=[("trend", "趨勢"), ("ema200")])
macd_col = _find_col_by_keywords(df, keywords=[("macd"), ("cond", "條件", ">0", "<0")])
price_ema_col = _find_col_by_keywords(df, keywords=[("price", "股價"), ("ema200"), (">", "<", "vs")])

# Fallback sensible defaults
candidates = {
    "Sector": sector_col, "Market Cap": mcap_col,
    "Average Volume (10 Days)": vol_col, "Div > EPS?": div_gt_eps_col,
    "Annual Equity Up?": equity_up_col, "Annual Debt Down?": debt_down_col,
    "Q vs Y1 Equity Up?": qv_eq_col, "Q vs Y1 Debt Down?": qv_db_col,
    "Net Income Q > 0?": net_income_q_col,"Net Income Y1 > 0?": net_income_y1_col,
    "Status": status_col, "Cross (Technical)": cross_col,
    "Trend EMA200": trend_col, "MACD Condition": macd_col,
    "Price vs EMA200": price_ema_col,}

with st.expander("Column Mapping (manually adjust if auto-detection is incorrect)", expanded=False):
    # 建立一個選項名稱列表，以便於用索引分成兩組
    labels = list(candidates.keys())
    for i in range(0, len(labels), 2):
        label1 = labels[i]
        col_left, col_right = st.columns(2)
        with col_left:
            current1 = candidates[label1]
            candidates[label1] = st.selectbox(
                f"{label1} Column",
                options=["<Auto-detect>"] + list(df.columns),
                index=(["<Auto-detect>"] + list(df.columns)).index(current1) if current1 in df.columns else 0,
                key=f"col_map_{label1.replace(' ', '_').replace('?', '')}" # 確保 key 唯一
            )
        if i + 1 < len(labels):
            label2 = labels[i + 1]
            with col_right:
                current2 = candidates[label2]
                candidates[label2] = st.selectbox(
                    f"{label2} Column",
                    options=["<Auto-detect>"] + list(df.columns),
                    index=(["<Auto-detect>"] + list(df.columns)).index(current2) if current2 in df.columns else 0,
                    key=f"col_map_{label2.replace(' ', '_').replace('?', '')}" # 確保 key 唯一
                )

    # ----------------------------------------------------
    # 變數回寫邏輯 (Read-back Logic) - 保持原本的邏輯但更簡潔
    # ----------------------------------------------------
    div_gt_eps_col = None if candidates["Div > EPS?"] == "<Auto-detect>" else candidates["Div > EPS?"]
    mcap_col = None if candidates["Market Cap"] == "<Auto-detect>" else candidates["Market Cap"]
    equity_up_col = None if candidates["Annual Equity Up?"] == "<Auto-detect>" else candidates["Annual Equity Up?"]
    debt_down_col = None if candidates["Annual Debt Down?"] == "<Auto-detect>" else candidates["Annual Debt Down?"]
    qv_eq_col = None if candidates["Q vs Y1 Equity Up?"] == "<Auto-detect>" else candidates["Q vs Y1 Equity Up?"]
    qv_db_col = None if candidates["Q vs Y1 Debt Down?"] == "<Auto-detect>" else candidates["Q vs Y1 Debt Down?"]
    sector_col = None if candidates["Sector"] == "<Auto-detect>" else candidates["Sector"]
    vol_col = None if candidates["Average Volume (10 Days)"] == "<Auto-detect>" else candidates["Average Volume (10 Days)"]
    status_col = None if candidates["Status"] == "<Auto-detect>" else candidates["Status"]
    net_income_q_col = None if candidates["Net Income Q > 0?"] == "<Auto-detect>" else candidates["Net Income Q > 0?"]
    net_income_y1_col = None if candidates["Net Income Y1 > 0?"] == "<Auto-detect>" else candidates["Net Income Y1 > 0?"]
    cross_col = None if candidates["Cross (Technical)"] == "<Auto-detect>" else candidates["Cross (Technical)"]
    trend_col = None if candidates["Trend EMA200"] == "<Auto-detect>" else candidates["Trend EMA200"]
    macd_col = None if candidates["MACD Condition"] == "<Auto-detect>" else candidates["MACD Condition"]
    price_ema_col = None if candidates["Price vs EMA200"] == "<Auto-detect>" else candidates["Price vs EMA200"]


# -------------------------
# Filters UI
# -------------------------
with st.container(border=True):
    st.subheader("Filters")

    # ---- Apply button ----
    btn1 , btn28 , btn29,btn23, btn12= st.columns([1, 1, 1,1, 1])
    with btn1:
        preset_clicked = st.button("⭐ Use Optimized Filter Set ⭐", type="secondary", key="load_preset_btn")

        
    # ---- 如果上一輪按了「Load Preset」，在畫任何 widget 之前先套用預設 ----
    if st.session_state.get("apply_filter_preset", False):
        # 這裡填你要的一鍵預設組合
        st.session_state["mcap_choice"]        = ["Large", "Mega"]
        st.session_state["status_choice"]      = ["BUY"]

        st.session_state["eq_choice"]          = "Yes"
        st.session_state["debt_choice"]        = "Yes"
        st.session_state["qv_eq_choice"]       = "Yes"
        st.session_state["qv_db_choice"]       = "Yes"

        st.session_state["cross_choice"]       = "Golden Cross"
        st.session_state["trend_choice"]       = "UP"
        st.session_state["macd_choice"]        = "MACD < 0"
        st.session_state["price_ema_choice"]   = "Price > EMA200"

        st.session_state["volume_min"]         = 1000000
        st.session_state["net_income_q_choice"]  = "True"
        st.session_state["net_income_y1_choice"] = "True"

        # 用完旗標就關掉，避免每一輪都一直套用
        st.session_state["apply_filter_preset"] = False
        


    # 0) Stock symbol filter (multi-select, case-insensitive)
    ticker_col = None
    if 'df' in locals():
        ticker_col = _find_col(df, ["ticker", "symbol", "symbols"])

    if ticker_col:
        # Collect all tickers (uppercase for consistency)
        all_tickers = sorted(
            pd.Series(
                df[ticker_col].astype(str).str.strip().str.upper().unique()).dropna())

        _sel = st.multiselect(
            "Type or select stock symbols（Multiple Choice）",
            options=all_tickers,
            default=[],
            key="ticker_multi_input",
            placeholder="Example: AAPL, MSFT, TSLA")

        selected_tickers = sorted(set(map(str.upper, _sel)))
        st.session_state["__selected_tickers__"] = selected_tickers

        if selected_tickers:
            st.caption( f"Selected {len(selected_tickers)} symbols: "
                f"{', '.join(selected_tickers[:15])}"
                + (" ..." if len(selected_tickers) > 15 else ""))
    else:
        st.session_state["__selected_tickers__"] = []

    # ---- 第一排：Sector / Market Cap / Status ----
    col1, col2, col3 = st.columns([1, 1, 1])

    # Sector（多選）
    with col1:
        if 'sector_col' in locals() and sector_col and sector_col in df.columns:
            sector_options = sorted(
                pd.Series(df[sector_col].astype(str).unique()).dropna()
            )
        else:
            sector_options = []
        sector_choice = st.multiselect( "Sector（Multiple Choice）", sector_options, default=[], key="sector_choice")

    # Market Cap（多選）
    with col2:
        mcap_choice = st.multiselect("Market Cap（Multiple Choice）",["Mega", "Large", "Mid", "Small", "Micro","Nano"],
                                     default=[],key="mcap_choice")

    # Volume (prev day)
    with col3:
        vol_col = _find_col_by_keywords(df, ["volume", ("prev day", "prev_day", "previous")])
        if vol_col:
            vol_series = pd.to_numeric(df[vol_col], errors="coerce")
            vol_min_value = max(0, int(np.nanmin(vol_series)))
            vol_max_value = max(vol_min_value, int(np.nanmax(vol_series)))
            volume_min = st.number_input( "Average Volume (10days) Minimum Threshold",
                min_value=0, max_value=vol_max_value, value=st.session_state.get("volume_min", 0),step=1000,key="volume_min",
                help=f"Enter minimum volume threshold (0 = disable filter). Data range: {vol_min_value:,} to {vol_max_value:,}")
        else:
            st.caption("Volume column not found.")
            volume_min = None

    # ---- 第二排：年度與季度比較 ----
    col4, col5, col6, col7 = st.columns([1, 1, 1, 1])
    with col4:
        eq_choice = st.selectbox("Annual Equity Up?", ["All", "Yes", "No"],key="eq_choice")
    with col5:
        debt_choice = st.selectbox("Annual Debt Down?", ["All", "Yes", "No"],key="debt_choice")
    with col6:
        qv_eq_choice = st.selectbox("Q vs Y1 Equity Up?", ["All", "Yes", "No"],key="qv_eq_choice")
    with col7:
        qv_db_choice = st.selectbox("Q vs Y1 Debt Down?", ["All", "Yes", "No"],key="qv_db_choice")

    # ---- Row 5: Volume / Net Income ----
    col15, col16, col23, col24 = st.columns([1,1,1,1])
    with col15:
        net_income_q_choice = st.selectbox( "Net income_Q > 0?", ["All", "True", "False or 0"],key="net_income_q_choice",
            help="Filter based on quarterly net income > 0")
    with col16:
        net_income_y1_choice = st.selectbox( "Net income_Y1 > 0?", ["All", "True", "False or 0"],key="net_income_y1_choice",
            help="Filter based on last year's net income > 0")
        
    st.write("-----")

    
    col33,col12= st.columns([1,1])
    # Status（多選：BUY/SELL/HOLD）
    with col33:
        if "Status" in df.columns:
            status_candidates = (
                df["Status"].astype(str) .str.strip() .str.upper() .replace({"": None}) .dropna() .unique() .tolist() )
            base_status = ["BUY", "SELL", "HOLD"]
            extra = [s for s in status_candidates if s not in base_status]
            status_options = base_status + sorted(extra)
        else:
            status_options = ["BUY", "SELL", "HOLD"]

        status_choice = st.multiselect( "Status（Multiple Choice）", status_options, default=[], key="status_choice")

    # ---- Row 3: Technical (Cross / Trend_EMA200/MACD / EMA200 boolean)----
    col8, col9, col10, col12  = st.columns([1, 1, 1, 1])
    with col8:
        cross_choice = st.selectbox("Cross (goldencross / deathcross)", ["All", "Golden Cross", "Death Cross", "None"],key="cross_choice",
            help="Filter by GoldenCross / DeathCross (True/False)")
    with col9: 
        trend_choice = st.selectbox("Trend_EMA200", ["All", "UP", "DOWN"],key="trend_choice",
            help="Filter by Trend_EMA200 (UP/DOWN)")
    with col10:
        macd_choice = st.selectbox("MACD Condition", ["All", "MACD > 0", "MACD < 0"],key="macd_choice",
        help="Filter using MACD_BelowZero / MACD_AboveZero")
    with col12:
        price_ema_choice = st.selectbox( "Price vs EMA200", ["All", "Price > EMA200", "Price < EMA200"],key="price_ema_choice",
            help="Uses Price>EMA200 / Price<EMA200 columns")

    
    # ---- Apply button ----
    #apply_clicked = st.button("Apply Filter", type="primary")

    btn2 ,btn22= st.columns([1, 1])
    with btn2:
        apply_clicked = st.button("Apply Filter", type="primary", key="apply_filter_btn")
    with btn22:
        st.button("Clear All Filters", type="secondary", key="clear_all_filters_btn",on_click=clear_all_filters_callback)

    # 如果按下「Load My Preset」，設定旗標並重新執行
    if preset_clicked:
        st.session_state["apply_filter_preset"] = True
        st.rerun()
        
# -------------------------
# Apply Filtering
# -------------------------
fdf = df.copy()

if apply_clicked:
    # 股票代號過濾（如有選擇）
    _sel = st.session_state.get("__selected_tickers__", [])
    if _sel:
        _ticker_col = _find_col(fdf, ["ticker", "symbol", "symbols"])
        if _ticker_col:
            fdf = fdf[fdf[_ticker_col].astype(str).str.strip().str.upper().isin(set(map(str.upper, _sel)))]

    # Annual Equity Up?
    if 'equity_up_col' in locals() and equity_up_col and equity_up_col in fdf.columns and eq_choice != "All":
        eq_series = coerce_bool(fdf[equity_up_col])
        want = (eq_choice == "Yes")
        fdf = fdf[eq_series == want]
    elif eq_choice != "All" and (('equity_up_col' not in locals()) or (equity_up_col not in fdf.columns)):
        st.warning("找不到『Equity Up?』欄，無法套用此條件。")

    # Annual Debt Down?
    if 'debt_down_col' in locals() and debt_down_col and debt_down_col in fdf.columns and debt_choice != "All":
        debt_series = coerce_bool(fdf[debt_down_col])
        want = (debt_choice == "Yes")
        fdf = fdf[debt_series == want]
    elif debt_choice != "All" and (('debt_down_col' not in locals()) or (debt_down_col not in fdf.columns)):
        st.warning("找不到『Debt Down?』欄，無法套用此條件。")

    # Q vs Y1 Equity Up?
    if 'qv_eq_col' in locals() and qv_eq_col and qv_eq_col in fdf.columns and qv_eq_choice != "All":
        qv_eq_series = coerce_bool(fdf[qv_eq_col])
        want = (qv_eq_choice == "Yes")
        fdf = fdf[qv_eq_series == want]
    elif qv_eq_choice != "All" and (('qv_eq_col' not in locals()) or (qv_eq_col not in fdf.columns)):
        st.warning("找不到『Q vs Y1 Equity Up?』欄，無法套用此條件。")

    # Q vs Y1 Debt Down?
    if 'qv_db_col' in locals() and qv_db_col and qv_db_col in fdf.columns and qv_db_choice != "All":
        qv_db_series = coerce_bool(fdf[qv_db_col])
        want = (qv_db_choice == "Yes")
        fdf = fdf[qv_db_series == want]
    elif qv_db_choice != "All" and (('qv_db_col' not in locals()) or (qv_db_col not in fdf.columns)):
        st.warning("找不到『Q vs Y1 Debt Down?』欄，無法套用此條件。")

    # Sector 過濾
    if sector_choice:
        if 'sector_col' in locals() and sector_col and sector_col in fdf.columns:
            fdf = fdf[fdf[sector_col].astype(str).isin(sector_choice)]
        else:
            st.warning("找不到 Sector 欄，無法套用此條件。")

    # Market Cap（多選）——只支援文字分類篩選
    if mcap_choice:
        use_class_col = "Market Cap Class" if "Market Cap Class" in fdf.columns else None
        if 'mcap_col' in locals() and mcap_col and mcap_col in fdf.columns:
            series = fdf[mcap_col]
            if series.astype(str).str.contains(r"mega|large|mid|small|micro|nano", case=False, na=False).any():
                use_class_col = mcap_col
        if use_class_col:
            mc_series = fdf[use_class_col].astype(str).str.strip().str.lower()
            selected = [s.lower() for s in mcap_choice]
            fdf = fdf[mc_series.isin(selected)]
        else:
            st.warning("找不到包含 Market Cap 分類文字（Mega/Large/Mid/Small/Micro）的欄位，無法套用市值條件。")

    # Status（多選）
    if status_choice:
        if "Status" in fdf.columns:
            target = [s.upper() for s in status_choice]
            fdf = fdf[fdf["Status"].astype(str).str.strip().str.upper().isin(target)]
        else:
            st.warning("找不到 Status 欄，無法套用此條件。")
   
    # MACD combined filter (MACD_BelowZero / MACD_AboveZero)
    if macd_choice != "All":
        macd_below_col = "MACD_BelowZero" if "MACD_BelowZero" in fdf.columns else None
        macd_above_col = "MACD_AboveZero" if "MACD_AboveZero" in fdf.columns else None
        if macd_below_col is None or macd_above_col is None:
            st.warning("MACD_BelowZero / MACD_AboveZero columns not found. Cannot apply MACD filter.")
        else:
            macd_below = coerce_bool(fdf[macd_below_col])
            macd_above = coerce_bool(fdf[macd_above_col])
            if macd_choice == "MACD > 0":
                # Use MACD_AboveZero == True
                fdf = fdf[macd_above.fillna(False)]
            elif macd_choice == "MACD < 0":
                # Use MACD_BelowZero == True
                fdf = fdf[macd_below.fillna(False)]

    # Price vs EMA200 combined filter
    if price_ema_choice != "All":

        price_gt_col = "Price>EMA200" if "Price>EMA200" in fdf.columns else None
        price_lt_col = "Price<EMA200" if "Price<EMA200" in fdf.columns else None

        if price_gt_col is None or price_lt_col is None:
            st.warning("Price>EMA200 / Price<EMA200 columns not found.")
        else:
            price_gt = coerce_bool(fdf[price_gt_col])
            price_lt = coerce_bool(fdf[price_lt_col])

            if price_ema_choice == "Price > EMA200":
                fdf = fdf[price_gt.fillna(False)]
            elif price_ema_choice == "Price < EMA200":
                fdf = fdf[price_lt.fillna(False)]



    # Cross 過濾（GoldenCross / DeathCross 或 goldencross / deathcross）
    if cross_choice != "All":
        gc_col = _find_col(fdf, ["GoldenCross", "goldencross"])
        dc_col = _find_col(fdf, ["DeathCross", "deathcross"])

        if not (gc_col or dc_col):
            st.warning("找不到 GoldenCross / DeathCross 欄，無法套用 Cross 條件。")
        else:
            gc = coerce_bool(fdf[gc_col]) if gc_col else pd.Series(pd.NA, index=fdf.index, dtype="boolean")
            dc = coerce_bool(fdf[dc_col]) if dc_col else pd.Series(pd.NA, index=fdf.index, dtype="boolean")

            if cross_choice == "Golden Cross":
                fdf = fdf[gc.fillna(False)]
            elif cross_choice == "Death Cross":
                fdf = fdf[dc.fillna(False)]
            elif cross_choice == "None":
                fdf = fdf[~gc.fillna(False) & ~dc.fillna(False)]

    # Trend_EMA200 過濾
    if trend_choice != "All":
        trend_col = _find_col(fdf, ["Trend_EMA200"])
        if trend_col:
            fdf = fdf[fdf[trend_col].astype(str).str.strip().str.upper() == trend_choice]
        else:
            st.warning("找不到 trend_EMA200 欄，無法套用此條件。")
            
    # AvgVolume_10D 過濾
    if volume_min is not None and volume_min > 0:
        vol_col = _find_col_by_keywords(
            fdf,
            ["avgvolume", ("10d")]
        )
        if vol_col:
            vol_series = pd.to_numeric(fdf[vol_col], errors="coerce")
            fdf = fdf[vol_series >= volume_min]
        else:
            st.warning("找不到 AvgVolume_10D 欄，無法套用此條件。")


    #  Net income_Q > 0? 過濾 
    if 'net_income_q_choice' in locals() and net_income_q_choice != "All":
        # 例如欄位名稱可能是 "net_income_Q" 或 "Net income_Q"
        net_q_col = _find_col_by_keywords(
            fdf,
            ["net", "income", ("lastQ", "lastq")]
        )
        #net_income_lastyear
        if net_q_col:
            s = pd.to_numeric(fdf[net_q_col], errors="coerce")
            if net_income_q_choice == ">":
                fdf = fdf[s > 0]
            elif net_income_q_choice == "<= 0":
                fdf = fdf[s <= 0]
        else:
            st.warning("找不到 Net income_Q 欄，無法套用此條件。")

    # Net income last year > 0? 過濾（欄位：net_income_lastyear>0?）
    if 'net_income_lastyear_choice' in locals() and net_income_lastyear_choice is True:
        # 尋找欄位名稱可能的變化
        net_last_col = _find_col_by_keywords(
            fdf,
            ["net", "income", ("lastyear", "last_year", "last year"), ">0"]
        )
        if net_last_col:
            # 用於代表 checkbox 勾選，例如 True / 1 / "Y"
            s = fdf[net_last_col].astype(str).str.lower().isin(["1", "true", "yes", "y"])
            fdf = fdf[s]
        else:
            st.warning("找不到 net_income_lastyear>0? 欄，無法套用此條件。")

    st.session_state["last_filtered_df"] = fdf.copy()
else:
    # 這一輪沒有按 Apply Filter，就沿用上一次的篩選結果（如果有）
    if "last_filtered_df" in st.session_state:
        fdf = st.session_state["last_filtered_df"].copy()

    

# ---------------- 建立 cross 顯示欄位（僅一版） ----------------
gc_col = _find_col(fdf, ["GoldenCross", "goldencross"])
dc_col = _find_col(fdf, ["DeathCross", "deathcross"])

if gc_col or dc_col:
    gc = coerce_bool(fdf[gc_col]) if gc_col else pd.Series(False, index=fdf.index, dtype="boolean")
    dc = coerce_bool(fdf[dc_col]) if dc_col else pd.Series(False, index=fdf.index, dtype="boolean")
    fdf["cross"] = np.where(gc.fillna(False), "goldencross",
                     np.where(dc.fillna(False), "deathcross", "none"))
else:
    fdf["cross"] = "none"

# -------------------------
# Results
# -------------------------
KEEP_COLUMNS = [
    "Symbol", "Company", "Industry", "Market Cap Class", "Price", "AvgVolume_10D",
    "Q vs Y1 Equity Up", "Q vs Y1 Debt Down",
    "Annual_Equity_Up(y1>y2)", "Annual_Debt_Down(y1<y2)",
    "Q_vs_Y1_Equity_Up", "Q_vs_Y1_Debt_Down", "net_income_lastyear","net_income_lastQ",
    "P/E", "PE/G", "Status","cross", "Trend_EMA200",
    "MACD_BelowZero", "MACD_AboveZero", "Price>EMA200", "Price<EMA200"]

keep_cols = [c for c in KEEP_COLUMNS if c in fdf.columns]
fdf_out = fdf[keep_cols].copy() if keep_cols else fdf.copy()

rename_map_output = {
    "Annual_Equity_Up(y1>y2)": "Annual Equity Up",
    "Annual_Debt_Down(y1<y2)": "Annual Debt Down",
    "Q_vs_Y1_Equity_Up": "Q vs Y1 Equity Up",
    "Q_vs_Y1_Debt_Down": "Q vs Y1 Debt Down"
}
rename_map_output = {
    k: v for k, v in rename_map_output.items() if k in fdf_out.columns
}
fdf_out = fdf_out.rename(columns=rename_map_output)

st.write("### Result")
st.caption(f"total {len(fdf_out)}")
st.dataframe(fdf_out, use_container_width=True)

csv_bytes = fdf_out.to_csv(index=False).encode("utf-8-sig")
st.download_button(
    label="Download filtered_stocks.csv",
    data=csv_bytes,
    file_name="filtered_stocks.csv",
    mime="text/csv",
    use_container_width=True
)

# -------------------------
# 將結果表中的股票代號同步到 session_state，供後續模組（MACD 等）使用
# -------------------------
def _detect_ticker_col(df):
    for c in df.columns:
        if str(c).strip().lower() in ("ticker", "symbol", "symbols"):
            return c
    return None

_ticker_col = _detect_ticker_col(fdf_out)
if _t_f := _ticker_col:
    selected_from_result = (
        fdf_out[_t_f].astype(str).str.strip().str.upper().dropna().unique().tolist()
    )
    if selected_from_result:
        st.session_state["selected_tickers"] = selected_from_result
        st.session_state["__selected_tickers__"] = selected_from_result
        st.caption(f"已同步 {len(selected_from_result)} 檔股票到後續分析模組（含 MACD）。")


# =========================================
# MACD Crossover Scanner + Cycle Analysis (Last 30 Crosses)
# Single Excel export with two sheets: crossovers & analysis
# =========================================
# MACD Crossover + Cycle Analysis (button-triggered, single Excel with 2 sheets)
# =========================================
def _pair_golden_to_next_death_within_subset(subset_cross_df: pd.DataFrame) -> list[tuple[pd.Timestamp, pd.Timestamp | None]]:
    """Pair each Golden Cross with the next Death Cross within the provided (last-N) crossover subset."""
    dfc = subset_cross_df.sort_values("date", ascending=True).reset_index(drop=True)
    pairs = []
    for i in range(len(dfc)):
        if dfc.loc[i, "crossover"] != "Golden Cross":
            continue
        golden_date = pd.to_datetime(dfc.loc[i, "date"])
        death_date = None
        for j in range(i + 1, len(dfc)):
            if dfc.loc[j, "crossover"] == "Death Cross":
                death_date = pd.to_datetime(dfc.loc[j, "date"])
                break
        if death_date is not None and death_date >= golden_date:
            pairs.append((golden_date, death_date))
    return pairs

def _max_close_between(px_df: pd.DataFrame, start_dt: pd.Timestamp, end_dt: pd.Timestamp) -> tuple[pd.Timestamp, float, int]:
    """Find max close within [start_dt, end_dt] in a price df(date, close)."""
    s = pd.to_datetime(start_dt).date()
    e = pd.to_datetime(end_dt).date()
    seg = px_df.loc[(px_df["date"] >= s) & (px_df["date"] <= e)]
    if seg.empty:
        return (pd.NaT, float("nan"), None)
    r = seg.loc[seg["close"].idxmax()]
    max_date = pd.to_datetime(r["date"])
    max_close = float(r["close"])
    days_from_start = (max_date - pd.to_datetime(s)).days
    return (max_date, max_close, days_from_start)



# =========================================
# Interactive MACD chart (dynamic loader for macdchart.py)
# - 放在本檔最後面
# - 需求：st.session_state["ticker_multi_input"]、st.session_state["api_key"] 或 POLYGON_API_KEY
# =========================================
import os, types, inspect, importlib.util, traceback
from datetime import datetime
import streamlit as st
import pandas as pd
import numpy as np

def _set_env_api_key_from_ui(api_key: str | None):
    if api_key:
        os.environ["POLYGON_API_KEY"] = api_key

def _read_py_without_runner(py_path: str) -> str:
    """讀取 .py 並嘗試移除底部自動執行區塊（例如 if __name__ == '__main__': 或 # 執行）。"""
    with open(py_path, "r", encoding="utf-8", errors="replace") as f:
        src = f.read()

    # 先嘗試砍掉 # 執行 / # Execute 等註記之後的內容
    cut_markers = ["\n# 執行", "\n#\u57F7\u884C", "\n# Execute", "\n# RUN", "\n# run"]
    cut_pos = -1
    for mk in cut_markers:
        pos = src.find(mk)
        if pos != -1:
            cut_pos = pos
            break

    # 移除常見 main runner
    if cut_pos == -1:
        import re
        m = re.search(r"if\s+__name__\s*==\s*['\"]__main__['\"]\s*:\s*", src)
        if m:
            cut_pos = m.start()

    return src if cut_pos == -1 else src[:cut_pos]

def _load_chart_module(chart_path: str):
    """將檔案載入到一個臨時模組命名空間，回傳模組對象（不執行底部 runner）。"""
    safe_src = _read_py_without_runner(chart_path)
    mod = types.ModuleType("interactive_macd_mod")
    mod.__file__ = chart_path

    # 可選：注入常見名稱（若外部檔直接使用這些已存在物件）
    import math, time, json, datetime, requests, plotly, plotly.graph_objs as go
    mod.__dict__.update({
        "__name__": "interactive_macd_mod",
        "st": st,
        "pd": pd,
        "np": np,
        "os": os,
        "math": math,
        "time": time,
        "json": json,
        "datetime": datetime,
        "requests": requests,
        "plotly": plotly,
        "go": go,
    })

    code = compile(safe_src, chart_path, "exec")
    exec(code, mod.__dict__)
    return mod

def _call_render_flex(mod, tickers: list[str], api_key: str | None):
    """
    盡量相容未知的入口名稱與參數。
    嘗試下列函式名，依序測試不同簽名：
      render_interactive_macd, render_macd, render, app, main, run, plot_macd, plot
    參數嘗試順序（逐一 try）：
      (tickers, api_key), (tickers,), (st, tickers, api_key), (api_key,), (), (st,)
    """
    candidates = [
        "render_interactive_macd", "render_macd", "render",
        "app", "main", "run", "plot_macd", "plot"
    ]
    tried = []
    for name in candidates:
        fn = getattr(mod, name, None)
        if callable(fn):
            for args in [
                (tickers, api_key),
                (tickers,),
                (st, tickers, api_key),
                (api_key,),
                tuple(),         # 無參數
                (st,),
            ]:
                try:
                    return fn(*args)
                except TypeError as te:
                    tried.append(f"{name}{inspect.signature(fn)} with args={tuple(type(a).__name__ for a in args)} -> {te}")
                    continue
                except Exception as e:
                    # 真正執行內部失敗，直接丟出詳細錯誤
                    raise
    # 若沒有任何候選可執行，回傳詳細嘗試紀錄
    raise RuntimeError("找不到可用的渲染入口點。嘗試紀錄：\n" + "\n".join(tried))

with st.expander("Interactive MACD Chart", expanded=False):
    st.write("Load and render the interactive MACD chart using current selected tickers.")

    # 取得 API Key 與已選股票
    api_key = (st.session_state.get("api_key") or os.getenv("POLYGON_API_KEY", "")).strip()

    # 優先用「篩選結果同步過來」的 tickers，沒有才用手動輸入框
    selected = (
        st.session_state.get("selected_tickers")
        or st.session_state.get("__selected_tickers__")
        or st.session_state.get("ticker_multi_input", [])
    )

    nfo = st.empty()


    col1, col2 = st.columns([1,1])
    run_btn   = col1.button("Generate interactive MACD chart", type="primary", key="btn_gen_interactive_macd")
    clear_btn = col2.button("Clear rendered chart state", key="btn_clear_interactive_macd")

    if clear_btn:
        st.session_state.pop("interactive_macd_ts", None)
        st.success("Cleared interactive MACD chart state.")

    if run_btn:
        if not api_key:
            st.error("Polygon API Key not found. Please set it in Settings or POLYGON_API_KEY.")
            st.stop()
        if not selected:
            st.info("No ticker selected. Please select at least one ticker above.")
            st.stop()

        _set_env_api_key_from_ui(api_key)

        # 讀取檔案
        try:
            current_dir = os.path.dirname(os.path.abspath(__file__))
        except NameError:
            # 某些環境可能沒有 __file__
            current_dir = os.getcwd()
        chart_path = os.path.join(current_dir, "macdchart.py")
        if not os.path.exists(chart_path):
            st.error(f"File not found: {chart_path}")
            st.stop()

        try:
            mod = _load_chart_module(chart_path)
            # 嘗試呼叫渲染入口
            _call_render_flex(mod, [str(t).upper().strip() for t in selected], api_key)
            st.session_state["interactive_macd_ts"] = datetime.utcnow().isoformat()
            nfo.info("Interactive MACD chart rendered.")
        except Exception as e:
            st.error("Failed to render interactive MACD chart. See details below.")
            st.exception(e)


