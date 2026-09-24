import re
import html as html_lib
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st
import yfinance as yf
import plotly.graph_objects as go

# =============================
# FUND / PORTFOLIO SETTINGS
# =============================
FSELX_SYMBOL = "FSELX"
DEFAULT_SHARES = 22256
SHARES = DEFAULT_SHARES
DATABASE = "fselx_monitor.db"
TIMEZONE = ZoneInfo("America/New_York")
HOLDINGS_DATE = "07/31/2026"

# Fidelity research page used to retrieve the official FSELX NAV.
FIDELITY_SUMMARY_URL = (
    "https://institutional.fidelity.com/app/tabbed/dailypricing/"
    "FIIS_PP_SP34_DPL6.html"
    "?asOf=Month&assetClassID=0&fundIdentifier=symb"
    "&navId=324&productLineId=6&salesCharge=AtNav&tab=dailypricing"
)

# Fallback Fidelity page if the daily-pricing page changes.
FIDELITY_FALLBACK_URL = (
    "https://fundresearch.fidelity.com/"
    "mutual-funds/summary/316390863"
)

# Complete regulatory portfolio source:
# Fidelity Select Semiconductors Portfolio quarterly holdings report dated 05/31/2026.
# The report contains 70 security/position line items. Fidelity's newer 07/31/2026
# summary reports 74 holdings, but the complete July position-level report is not
# yet used here. This tracker never invents the four newer positions.
REGULATORY_HOLDINGS_DATE = "05/31/2026"
CURRENT_FIDELITY_SUMMARY_DATE = "07/31/2026"
CURRENT_FIDELITY_HOLDING_COUNT = 74
REGULATORY_POSITION_COUNT = 70
REGULATORY_NET_ASSETS = 51393101802

# Exchange-traded positions for which Yahoo Finance symbols are available.
HOLDINGS = {'NVMI': {'name': 'Nova Ltd', 'weight': 0.0188455724998157}, 'ALGM': {'name': 'Allegro MicroSystems Inc', 'weight': 0.01559186793370032}, '285A.T': {'name': 'Kioxia Holdings Corp', 'weight': 0.012679857474075252}, 'ASML': {'name': 'ASML Holding NV', 'weight': 0.045928814650921544}, 'NXPI': {'name': 'NXP Semiconductors NV', 'weight': 0.058032113443752795}, 'SIMO': {'name': 'Silicon Motion Technology Corp ADR', 'weight': 0.002423869675738316}, 'AEVA': {'name': 'Aeva Technologies Inc', 'weight': 0.0001203897173561768}, 'COHR': {'name': 'Coherent Corp', 'weight': 0.013968836396873447}, 'AOSL': {'name': 'Alpha & Omega Semiconductor Ltd', 'weight': 0.00045293965889978876}, 'ARM': {'name': 'ARM Holdings PLC ADR', 'weight': 0.013358367989637148}, 'ALAB': {'name': 'Astera Labs Inc', 'weight': 0.04983006306695308}, 'AVGO': {'name': 'Broadcom Inc', 'weight': 0.129739054371311}, 'CRDO': {'name': 'Credo Technology Group Holding Ltd', 'weight': 0.0020317149255220973}, 'ENTG': {'name': 'Entegris Inc', 'weight': 0.004334982676436354}, 'GFS': {'name': 'GlobalFoundries Inc', 'weight': 0.047336121204992684}, 'PI': {'name': 'Impinj Inc', 'weight': 0.003297665641060892}, 'KLAC': {'name': 'KLA Corp', 'weight': 0.007463517525713402}, 'LRCX': {'name': 'Lam Research Corp', 'weight': 0.03278795283639455}, 'MTSI': {'name': 'MACOM Technology Solutions Holdings Inc', 'weight': 0.027222993747101562}, 'MRVL': {'name': 'Marvell Technology Inc', 'weight': 0.07831219033063647}, 'MU': {'name': 'Micron Technology Inc', 'weight': 0.03085643820662109}, 'MPWR': {'name': 'Monolithic Power Systems Inc', 'weight': 0.04966484645806434}, 'NVDA': {'name': 'NVIDIA Corp', 'weight': 0.22097238490785265}, 'ON': {'name': 'ON Semiconductor Corp', 'weight': 0.04980059311968593}, 'SYNA': {'name': 'Synaptics Inc', 'weight': 0.003651037618295651}, 'SNDK': {'name': 'Sandisk Corp/DE', 'weight': 0.005877159101306582}, 'WDC': {'name': 'Western Digital Corp', 'weight': 0.031884547547122964}}

# Private, restricted, cash, debt, rights, warrants, or otherwise non-live-priced
# positions are retained in the portfolio inventory instead of being discarded.
UNQUOTED_POSITIONS = [{'name': 'Taalas Inc warrants', 'weight': 7.833125183822506e-05, 'value': 4025686}, {'name': 'Cerebras Systems Inc Class A', 'weight': 0.0040637066586215, 'value': 208846490}, {'name': 'Cerebras Systems Inc Class B', 'weight': 0.003422050797353428, 'value': 175869805}, {'name': 'Wolfspeed Inc', 'weight': 6.713138298777945e-06, 'value': 345009}, {'name': 'Celestial AI Inc', 'weight': 4.865244385585412e-07, 'value': 25004}, {'name': 'Celestial AI Inc (Milestone 1) rights', 'weight': 6.195006505476382e-05, 'value': 3183806}, {'name': 'Celestial AI Inc (Milestone 2) rights', 'weight': 3.486718912012167e-05, 'value': 1791933}, {'name': 'Celestial AI Inc (Milestone 3) rights', 'weight': 7.459950587864306e-06, 'value': 383390}, {'name': 'Celestial AI Inc escrow shares', 'weight': 1.556629142724496e-10, 'value': 8}, {'name': 'Tenstorrent Holdings Inc 15% 12/31/2026', 'weight': 6.267687465936618e-05, 'value': 3221159}, {'name': 'Taalas Inc Series B', 'weight': 0.00022773336478290815, 'value': 11703924}, {'name': 'Element Labs Inc Series A', 'weight': 0.0006253188632943996, 'value': 32137076}, {'name': 'Element Labs Inc Series B', 'weight': 0.00027492292748615833, 'value': 14129142}, {'name': 'Element Labs Inc Series C', 'weight': 0.0021773398194783666, 'value': 111900247}, {'name': 'Xsight Labs Ltd Series G', 'weight': 0.00022227916976117292, 'value': 11423616}, {'name': 'Nearfield Instruments Series D', 'weight': 0.001406590757617724, 'value': 72289062}, {'name': 'Akeana Series C', 'weight': 2.345742828764395e-05, 'value': 1205550}, {'name': 'Tenstorrent Holdings Inc Series C1', 'weight': 2.3764434470317787e-05, 'value': 1221328}, {'name': 'Tenstorrent Holdings Inc Series D1', 'weight': 0.00022035124565218005, 'value': 11324534}, {'name': 'Tenstorrent Holdings Inc Series D2', 'weight': 1.2131394645180518e-05, 'value': 623470}, {'name': 'Empower Semiconductor Inc Series D', 'weight': 0.0007241421259876499, 'value': 37215910}, {'name': 'Frore Systems Inc Series C', 'weight': 0.000603400318577253, 'value': 31010614}, {'name': 'Frore Systems Inc Series D', 'weight': 0.000966500118855776, 'value': 49671439}, {'name': 'Menlo Microsystems Inc Series C', 'weight': 7.913999850932756e-06, 'value': 406725}, {'name': 'Menlo Microsystems Inc Series C-1', 'weight': 0.0001611133150106494, 'value': 8280113}, {'name': 'Alif Semiconductor Series C', 'weight': 0.00028640217235199444, 'value': 14719096}, {'name': 'Alif Semiconductor Series D', 'weight': 0.0002173526720188213, 'value': 11170428}, {'name': 'Danger Devices Inc Series B', 'weight': 0.0001408343872273989, 'value': 7237916}, {'name': 'Retym Inc Series C', 'weight': 0.00010876872194903135, 'value': 5589962}, {'name': 'Retym Inc Series D', 'weight': 2.8179326587048718e-05, 'value': 1448223}, {'name': 'SiMa Technologies Inc Series B', 'weight': 4.426014231955698e-05, 'value': 2274666}, {'name': 'SiMa Technologies Inc Series B1', 'weight': 2.6316547407678882e-05, 'value': 1352489}, {'name': 'Density AI Inc', 'weight': 0.00013185146960202152, 'value': 6776256}, {'name': 'Lyte AI Inc Series B', 'weight': 5.509416440573376e-05, 'value': 2831460}, {'name': 'Lightmatter Inc Series C1', 'weight': 0.0003320109392450793, 'value': 17063072}, {'name': 'Lightmatter Inc Series C2', 'weight': 5.326963938754638e-05, 'value': 2737692}, {'name': 'Lightmatter Inc Series D', 'weight': 0.000254425429513405, 'value': 13075712}, {'name': 'Diamond Foundry Inc Series C', 'weight': 0.0001252535024019409, 'value': 6437166}, {'name': 'Taalas Inc 0% preferred security', 'weight': 0.000243393151248038, 'value': 12508729}, {'name': 'SiMa Technologies Inc 10% 12/31/2027', 'weight': 1.6720998925317988e-05, 'value': 859344}, {'name': 'SiMa Technologies Inc 7.5% 12/31/2027', 'weight': 0.00010861774838005136, 'value': 5582203}, {'name': 'Fidelity Cash Central Fund', 'weight': 0.024180991425421964, 'value': 1242736154}, {'name': 'Fidelity Securities Lending Cash Central Fund', 'weight': 0.003506389859367998, 'value': 180204251}]

TRACKED_WEIGHT = sum(x["weight"] for x in HOLDINGS.values())
UNQUOTED_WEIGHT = sum(x["weight"] for x in UNQUOTED_POSITIONS)
TOTAL_REPORTED_WEIGHT = TRACKED_WEIGHT + UNQUOTED_WEIGHT

st.set_page_config(
    page_title="FSELX Live Monitor",
    page_icon="📈",
    layout="wide",
)


# =============================
# SQLITE HISTORY DATABASE
# =============================
def get_db():
    connection = sqlite3.connect(DATABASE)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            official_nav REAL,
            estimated_nav REAL,
            conservative_nav REAL,
            normalized_nav REAL,
            normalized_change REAL,
            conservative_change REAL,
            position_value REAL,
            estimated_gain REAL
        )
        """
    )
    connection.commit()
    return connection


# Save each estimate so the app can build an intraday history chart.
def save_observation(official_nav, model):
    try:
        connection = get_db()
        connection.execute(
            """
            INSERT INTO observations (
                timestamp,
                official_nav,
                estimated_nav,
                conservative_nav,
                normalized_nav,
                normalized_change,
                conservative_change,
                position_value,
                estimated_gain
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(TIMEZONE).isoformat(),
                official_nav,
                model["estimated_nav"],
                model["conservative_nav"],
                model["normalized_nav"],
                model["normalized_change"],
                model["conservative_change"],
                model["estimated_value"],
                model["estimated_gain"],
            ),
        )
        connection.commit()
        connection.close()
    except Exception:
        pass


# Load saved estimates from the local SQLite database.
def get_history():
    try:
        connection = get_db()
        data = pd.read_sql_query(
            """
            SELECT *
            FROM observations
            ORDER BY timestamp
            """,
            connection,
        )
        connection.close()
        return data
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=300, show_spinner=False)
# Download the Fidelity fund page; the decorator above caches the response.
def get_fidelity_page(url):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/140.0 Safari/537.36"
        )
    }
    response = requests.get(url, headers=headers, timeout=20)
    response.raise_for_status()
    return response.text


# Retrieve the latest published FSELX NAV. Fidelity is authoritative; Yahoo is fallback only.
@st.cache_data(ttl=300, show_spinner=False)
def get_official_nav():
    """Return (nav, source, as_of_date, error). Prefer Fidelity's published NAV."""
    fidelity_errors = []

    # Fidelity Institutional daily-pricing page is the primary source.
    try:
        page = get_fidelity_page(FIDELITY_SUMMARY_URL)
        text = html_lib.unescape(re.sub(r"<[^>]+>", " ", page))
        text = re.sub(r"\s+", " ", text)

        date_match = re.search(
            r"AS\s+OF\s+(\d{1,2}/\d{1,2}/\d{4})",
            text,
            flags=re.IGNORECASE,
        )
        as_of_date = date_match.group(1) if date_match else None

        symbol_pos = text.upper().find("FSELX")
        if symbol_pos >= 0:
            # On Fidelity's pricing table, the first dollar value following FSELX is NAV.
            window = text[symbol_pos:symbol_pos + 1800]
            nav_match = re.search(r"\$\s*([0-9]{1,4}(?:\.[0-9]{2,4})?)", window)
            if nav_match:
                value = float(nav_match.group(1))
                if 1 < value < 1000:
                    return value, "Fidelity", as_of_date, None

        fidelity_errors.append("FSELX NAV was not parsed from Fidelity daily pricing.")
    except Exception as exc:
        fidelity_errors.append(f"Fidelity daily-pricing lookup failed: {exc}")

    # Yahoo Finance is a fallback only when Fidelity cannot be read.
    try:
        fund = yf.Ticker(FSELX_SYMBOL)
        history = fund.history(period="10d", interval="1d", auto_adjust=False)
        if history is not None and not history.empty and "Close" in history.columns:
            closes = history["Close"].dropna()
            if not closes.empty:
                value = float(closes.iloc[-1])
                if 1 < value < 1000:
                    idx = closes.index[-1]
                    try:
                        as_of_date = idx.strftime("%m/%d/%Y")
                    except Exception:
                        as_of_date = None
                    warning = " | ".join(fidelity_errors) if fidelity_errors else None
                    return value, "Yahoo Finance fallback", as_of_date, warning
    except Exception as exc:
        fidelity_errors.append(f"Yahoo fallback failed: {exc}")

    return None, None, None, " | ".join(fidelity_errors)


@st.cache_data(ttl=45, show_spinner=False)
def get_intraday_data():
    """Fetch each quote-enabled holding independently so one bad ticker cannot hide NVDA or others."""
    results = {}
    for ticker in HOLDINGS:
        try:
            df = yf.Ticker(ticker).history(
                period="2d",
                interval="5m",
                auto_adjust=False,
                prepost=False,
            )
            results[ticker] = df
        except Exception as exc:
            results[ticker] = exc
    return results


@st.cache_data(ttl=45, show_spinner=False)
def get_extended_hours_data():
    """Fetch pre-market/after-hours quotes for a next-session indication only."""
    results = {}
    for ticker in HOLDINGS:
        try:
            df = yf.Ticker(ticker).history(
                period="2d",
                interval="5m",
                auto_adjust=False,
                prepost=True,
            )
            results[ticker] = df
        except Exception as exc:
            results[ticker] = exc
    return results


def extract_extended_prices(extended, daily):
    """Compare latest extended-hours quote with the latest completed regular close."""
    current = {}
    regular_close = {}
    for ticker in HOLDINGS:
        ext = extended.get(ticker)
        day = daily.get(ticker)
        if not isinstance(ext, Exception) and ext is not None and not ext.empty and "Close" in ext.columns:
            ser = ext["Close"].dropna()
            if not ser.empty:
                current[ticker] = float(ser.iloc[-1])
        if not isinstance(day, Exception) and day is not None and not day.empty and "Close" in day.columns:
            ser = day["Close"].dropna()
            if not ser.empty:
                regular_close[ticker] = float(ser.iloc[-1])
    return current, regular_close


@st.cache_data(ttl=300, show_spinner=False)
def get_daily_data():
    """Fetch daily history independently for robust previous-close lookup."""
    results = {}
    for ticker in HOLDINGS:
        try:
            df = yf.Ticker(ticker).history(
                period="10d",
                interval="1d",
                auto_adjust=False,
            )
            results[ticker] = df
        except Exception as exc:
            results[ticker] = exc
    return results


# Pull the latest price and previous close for each available holding.
def extract_prices(intraday, daily):
    current = {}
    previous = {}
    errors = []

    for ticker in HOLDINGS:
        intraday_result = intraday.get(ticker)
        daily_result = daily.get(ticker)

        try:
            if isinstance(intraday_result, Exception):
                raise intraday_result
            if intraday_result is not None and not intraday_result.empty and "Close" in intraday_result.columns:
                series = intraday_result["Close"].dropna()
                if not series.empty:
                    current[ticker] = float(series.iloc[-1])
        except Exception as exc:
            errors.append(f"{ticker} intraday: {exc}")

        try:
            if isinstance(daily_result, Exception):
                raise daily_result
            if daily_result is not None and not daily_result.empty and "Close" in daily_result.columns:
                series = daily_result["Close"].dropna()
                # Last completed daily bar is the previous close during the session.
                # If Yahoo already has today's partial/current daily bar, use the prior row.
                if len(series) >= 2:
                    previous[ticker] = float(series.iloc[-2])
                elif len(series) == 1:
                    previous[ticker] = float(series.iloc[-1])
        except Exception as exc:
            errors.append(f"{ticker} daily: {exc}")

        if ticker not in current:
            errors.append(f"{ticker}: no usable current quote")
        if ticker not in previous:
            errors.append(f"{ticker}: no usable previous close")

    return current, previous, errors


# =============================
# INTRADAY FSELX ESTIMATION MODEL
# =============================
# Conservative estimate uses actual regulatory weights for live-priced securities.
# Normalized estimate scales only the live-priced portion to estimate the day's move.
# Central estimate is the midpoint, providing a range for non-live-priced positions.
def calculate_model(official_nav, current_prices, previous_prices):
    rows = []
    actual_change = 0.0

    for ticker, info in HOLDINGS.items():
        if ticker not in current_prices or ticker not in previous_prices:
            continue

        current = current_prices[ticker]
        previous = previous_prices[ticker]
        if previous <= 0:
            continue

        change = (current / previous) - 1.0
        weight = info["weight"]
        actual_contribution = weight * change
        normalized_weight = weight / TRACKED_WEIGHT
        normalized_contribution = normalized_weight * change
        actual_change += actual_contribution

        rows.append(
            {
                "Ticker": ticker,
                "Company": info["name"],
                "Weight": weight,
                "Price": current,
                "Previous Close": previous,
                "Change": change,
                "Actual Contribution": actual_contribution,
                "Normalized Contribution": normalized_contribution,
            }
        )

    if not rows:
        return None

    conservative_change = actual_change
    normalized_change = sum(
        row["Normalized Contribution"] for row in rows
    )

    conservative_nav = official_nav * (1 + conservative_change)
    normalized_nav = official_nav * (1 + normalized_change)
    estimated_nav = (conservative_nav + normalized_nav) / 2

    baseline_value = SHARES * official_nav
    estimated_value = SHARES * estimated_nav
    conservative_value = SHARES * conservative_nav
    normalized_value = SHARES * normalized_nav

    return {
        "rows": rows,
        "conservative_change": conservative_change,
        "normalized_change": normalized_change,
        "conservative_nav": conservative_nav,
        "normalized_nav": normalized_nav,
        "estimated_nav": estimated_nav,
        "estimated_value": estimated_value,
        "conservative_value": conservative_value,
        "normalized_value": normalized_value,
        "estimated_gain": estimated_value - baseline_value,
        "conservative_gain": conservative_value - baseline_value,
        "normalized_gain": normalized_value - baseline_value,
    }


# =============================
# MARKET STATUS
# =============================
def get_market_status():
    """Simple U.S. regular-session indicator (weekdays, 9:30 AM-4:00 PM ET)."""
    now = datetime.now(TIMEZONE)

    if now.weekday() >= 5:
        return "Closed", now

    minutes = now.hour * 60 + now.minute
    market_open = 9 * 60 + 30
    market_close = 16 * 60

    if market_open <= minutes < market_close:
        return "Open", now

    if minutes < market_open:
        return "Pre-market", now

    return "Closed", now


# =============================
# DISPLAY HELPERS
# =============================
def money(value):
    return f"${value:,.2f}"


def percent(value):
    return f"{value * 100:+.2f}%"


def share_count(value):
    """Format whole shares without decimals and fractional shares cleanly."""
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.3f}".rstrip("0").rstrip(".")


# =============================
# STREAMLIT DASHBOARD
# =============================
st.title("📈 FSELX Live Intraday Monitor")

market_status, market_now = get_market_status()
st.caption(
    f"FSELX • {share_count(SHARES)} shares • "
    f"U.S. market: {market_status} • "
    "Full regulatory portfolio inventory; live estimate uses quoted holdings"
)

with st.sidebar:
    st.header("Settings")
    SHARES = st.number_input(
        "FSELX Shares Owned",
        min_value=0.0,
        value=float(DEFAULT_SHARES),
        step=1.0,
        format="%.3f",
        help="Enter the number of FSELX shares you currently own.",
    )
    st.divider()
    st.subheader("Official NAV Override")
    use_nav_override = st.checkbox(
        "Use Fidelity account NAV",
        value=True,
        help="Use the official NAV shown in your Fidelity account when public feeds have not caught up yet.",
    )
    override_current_nav = st.number_input(
        "Current official FSELX NAV",
        min_value=0.0,
        value=63.99,
        step=0.01,
        format="%.2f",
    )
    override_previous_nav = st.number_input(
        "Previous official FSELX NAV",
        min_value=0.0,
        value=64.84,
        step=0.01,
        format="%.2f",
    )

    refresh = st.slider(
        "Refresh interval",
        30,
        300,
        60,
        30,
    )
    st.divider()
    st.write(f"Live-priced portfolio weight: {TRACKED_WEIGHT * 100:.2f}%")
    st.write(f"Regulatory holdings date: {REGULATORY_HOLDINGS_DATE}")
    st.divider()

    if st.button("Clear cached data"):
        get_intraday_data.clear()
        get_extended_hours_data.clear()
        get_daily_data.clear()
        get_official_nav.clear()
        get_fidelity_page.clear()
        st.rerun()

# Verify the published FSELX NAV and holdings market data before calculating the estimate.
st.subheader("Data Status")
status1, status2 = st.columns(2)

with status1:
    st.write("FSELX Published NAV")

official_nav, nav_source, nav_date, fidelity_error = get_official_nav()

# Fidelity-account override: useful when the public Fidelity/Yahoo feeds lag the
# NAV already visible in the shareholder account. The override is authoritative
# for the completed-day actual result and the next session's baseline.
if use_nav_override and override_current_nav > 0:
    official_nav = float(override_current_nav)
    nav_source = "Fidelity account override"
    nav_date = market_now.strftime("%m/%d/%Y")

if official_nav is None:
    with status1:
        st.error("FSELX NAV unavailable")
    st.error("Could not retrieve the latest published FSELX NAV from Fidelity or the Yahoo fallback.")
    if fidelity_error:
        st.code(fidelity_error)
    st.stop()

with status1:
    nav_date_text = f" as of {nav_date}" if nav_date else ""
    st.success(f"Published FSELX NAV: {money(official_nav)}{nav_date_text}")
    st.caption(f"NAV source: {nav_source}")
    if fidelity_error and nav_source != "Fidelity":
        st.warning(f"Using fallback NAV. {fidelity_error}")

with status2:
    st.write("Yahoo Finance")

try:
    with st.spinner("Loading market prices..."):
        intraday = get_intraday_data()
        daily = get_daily_data()
        current_prices, previous_prices, errors = extract_prices(
            intraday,
            daily,
        )
except Exception as exc:
    current_prices = {}
    previous_prices = {}
    errors = [str(exc)]

if not current_prices:
    with status2:
        st.error("Yahoo Finance unavailable")
    for error in errors:
        st.warning(error)
    st.stop()

with status2:
    st.success(
        f"Yahoo Finance: {len(current_prices)} / "
        f"{len(HOLDINGS)} quote-enabled holdings"
    )

# NAV rollover protection:
# Mutual funds publish one official NAV after the market closes. If Fidelity has
# already published an NAV dated today, that value is the FINAL result for this
# trading day. Do not apply today's stock moves to it a second time. On the next
# trading day, the same published NAV automatically becomes the starting baseline.
nav_is_today = False
if nav_date:
    try:
        nav_day = datetime.strptime(nav_date, "%m/%d/%Y").date()
        nav_is_today = nav_day == market_now.date()
    except ValueError:
        nav_is_today = False

final_nav_published = market_status == "Closed" and nav_is_today

if final_nav_published:
    st.success(
        f"Final FSELX NAV for {nav_date} has been published: {money(official_nav)}. "
        "The tracker will not apply today's holdings move to this NAV again. "
        "This NAV becomes the baseline for the next trading session."
    )
    # Neutralize the already-completed day's holdings move so the model remains
    # anchored exactly to the newly published official NAV after the rollover.
    current_prices = previous_prices.copy()

model = calculate_model(
    official_nav,
    current_prices,
    previous_prices,
)

if model is None:
    st.warning(
        "Not enough market data is available "
        "to calculate the estimate."
    )
    st.write(
        f"Current prices: {len(current_prices)} / "
        f"{len(HOLDINGS)}"
    )
    st.write(
        f"Previous closes: {len(previous_prices)} / "
        f"{len(HOLDINGS)}"
    )
    st.stop()

save_observation(official_nav, model)

# Session-aware summary:
# - Regular market hours: show the live model move from the latest official NAV.
# - After the close, once today's official NAV is available: show the actual daily result.
# - Before the next session opens: keep the completed-day actual result visible, while
#   the separate extended-hours section provides a next-session indication.
st.subheader("Today's Move")

show_actual_move = (
    market_status != "Open"
    and use_nav_override
    and override_current_nav > 0
    and override_previous_nav > 0
)

if show_actual_move:
    actual_change_dollars_per_share = official_nav - float(override_previous_nav)
    actual_change_pct = (official_nav / float(override_previous_nav)) - 1.0
    actual_gain = SHARES * actual_change_dollars_per_share
    actual_value = SHARES * official_nav

    st.caption("Official completed-day result from Fidelity")
    move1, move2, move3, move4 = st.columns(4)
    with move1:
        st.metric("Prior Official NAV", money(float(override_previous_nav)))
    with move2:
        st.metric(
            "Official Closing NAV",
            money(official_nav),
            delta=money(actual_change_dollars_per_share),
        )
    with move3:
        st.metric("Actual FSELX Change", percent(actual_change_pct))
    with move4:
        st.metric("Actual Dollar Gain / Loss", money(actual_gain))
    st.caption(
        f"Actual Fidelity NAV move: {money(float(override_previous_nav))} → "
        f"{money(official_nav)} across {share_count(SHARES)} shares. "
        f"Current position value: {money(actual_value)}."
    )
else:
    # During regular trading, the latest published NAV is yesterday's official
    # close and is therefore the correct baseline for today's live estimate.
    live_change_per_share = model["estimated_nav"] - official_nav
    live_change_pct = (model["estimated_nav"] / official_nav) - 1.0
    st.caption("Live estimate versus the prior official FSELX close")
    move1, move2, move3, move4 = st.columns(4)
    with move1:
        st.metric("Prior Official Close", money(official_nav))
    with move2:
        st.metric(
            "Estimated Current NAV",
            money(model["estimated_nav"]),
            delta=money(live_change_per_share),
        )
    with move3:
        st.metric("Estimated Change", percent(live_change_pct))
    with move4:
        st.metric("Estimated Dollar Gain / Loss", money(model["estimated_gain"]))

# Main NAV/model detail. Keep this visible during the regular session; after the
# close the completed-day actual result above is authoritative.
if market_status == "Open":
    st.subheader("Live Intraday Estimate")
    c1, c2, c3, c4 = st.columns(4)

    with c1:
        st.metric("Prior Official NAV", money(official_nav))
    with c2:
        st.metric(
            "Estimated Intraday NAV",
            money(model["estimated_nav"]),
            delta=percent(model["normalized_change"]),
        )
    with c3:
        st.metric("Estimated Position", money(model["estimated_value"]))
    with c4:
        st.metric("Estimated Position Change", money(model["estimated_gain"]))

# Separate extended-hours indication. This NEVER changes the official NAV or
# completed-day actual result; it is only a directional look at the next session.
if market_status != "Open":
    try:
        extended_data = get_extended_hours_data()
        extended_prices, regular_closes = extract_extended_prices(extended_data, daily)
        extended_model = calculate_model(official_nav, extended_prices, regular_closes)
    except Exception:
        extended_model = None

    st.subheader("After-Hours / Next Session Indication")
    if extended_model is not None:
        ah1, ah2, ah3 = st.columns(3)
        with ah1:
            st.metric("Official NAV Baseline", money(official_nav))
        with ah2:
            st.metric(
                "Next-Session Indicated NAV",
                money(extended_model["estimated_nav"]),
                delta=percent(extended_model["normalized_change"]),
            )
        with ah3:
            st.metric("Indicated Position Change", money(extended_model["estimated_gain"]))
        st.caption(
            "Uses available pre-market/after-hours quotes versus each holding's latest "
            "regular-session close. It is a directional next-session indication only and "
            "does not alter the published FSELX NAV or the completed day's result."
        )
    else:
        st.info(
            "Extended-hours quotes are not currently available. The official FSELX NAV "
            "remains the baseline for the next regular trading session."
        )

st.subheader("Estimated FSELX Range")
r1, r2, r3 = st.columns(3)

with r1:
    st.metric(
        "Conservative NAV",
        money(model["conservative_nav"]),
        delta=percent(model["conservative_change"]),
    )

with r2:
    st.metric(
        "Central Estimate",
        money(model["estimated_nav"]),
    )

with r3:
    st.metric(
        "Normalized NAV",
        money(model["normalized_nav"]),
        delta=percent(model["normalized_change"]),
    )

st.write(
    f"Estimated range: {money(model['conservative_nav'])} "
    f"to {money(model['normalized_nav'])}"
)

st.subheader(f"Your {share_count(SHARES)}-Share Position")
baseline_value = SHARES * official_nav
p1, p2, p3 = st.columns(3)

with p1:
    st.metric(
        "NAV-Based Position",
        money(baseline_value),
    )

with p2:
    st.metric(
        "Conservative Intraday Move",
        money(model["conservative_gain"]),
    )

with p3:
    st.metric(
        "Normalized Intraday Move",
        money(model["normalized_gain"]),
    )

# Individual holding moves and their modeled NAV contributions.
st.subheader("Live-Priced Holdings")
table = pd.DataFrame(model["rows"])

if not table.empty:
    # IMPORTANT: calculate_model() builds rows in HOLDINGS dictionary order.
    # Explicitly sort THIS dataframe by portfolio weight before displaying it.
    table = table.sort_values(
        by="Weight",
        ascending=False,
        kind="stable",
    ).reset_index(drop=True)

    table["Weight"] = table["Weight"] * 100
    table["Change"] = table["Change"] * 100
    table["Contribution"] = (
        table["Normalized Contribution"] * 100
    )

    display_table = table[
        [
            "Ticker",
            "Company",
            "Weight",
            "Price",
            "Change",
            "Contribution",
        ]
    ].copy()

    display_table.columns = [
        "Ticker",
        "Company",
        "Weight %",
        "Price",
        "Stock Change %",
        "NAV Contribution %",
    ]

    st.dataframe(
        display_table,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Weight %": st.column_config.NumberColumn(
                format="%.2f%%"
            ),
            "Price": st.column_config.NumberColumn(
                format="$%.2f"
            ),
            "Stock Change %": st.column_config.NumberColumn(
                format="%+.2f%%"
            ),
            "NAV Contribution %": st.column_config.NumberColumn(
                format="%+.3f%%"
            ),
        },
    )


# Build pricing status before rendering the holdings table.
# This is deliberately defined here so the table can never reference an
# undefined successfully_priced variable.
successfully_priced = sorted(
    set(current_prices.keys()) & set(previous_prices.keys())
)
failed_pricing = sorted(set(HOLDINGS.keys()) - set(successfully_priced))

st.subheader("FSELX Holdings — Highest Weight to Lowest")
st.caption(
    f"Verified Fidelity/SEC regulatory portfolio dated {REGULATORY_HOLDINGS_DATE}. "
    "Weights are each position's reported value divided by reported net assets."
)

inventory_rows = []
for ticker, info in HOLDINGS.items():
    inventory_rows.append({
        "Security / Position": info["name"],
        "Ticker": ticker,
        "Weight %": info["weight"] * 100,
        "Pricing": "Yahoo live" if ticker in successfully_priced else "Yahoo failed",
    })

for info in UNQUOTED_POSITIONS:
    inventory_rows.append({
        "Security / Position": info["name"],
        "Ticker": "",
        "Weight %": info["weight"] * 100,
        "Pricing": "Reported / fair value",
    })

inventory_df = pd.DataFrame(inventory_rows).sort_values(
    "Weight %", ascending=False
).reset_index(drop=True)
inventory_df.insert(0, "Rank", range(1, len(inventory_df) + 1))

st.dataframe(
    inventory_df,
    use_container_width=True,
    hide_index=True,
    column_config={
        "Rank": st.column_config.NumberColumn(format="%d"),
        "Weight %": st.column_config.NumberColumn(format="%.4f%%"),
    },
)

q1, q2, q3, q4 = st.columns(4)
with q1:
    st.metric("Regulatory Positions", REGULATORY_POSITION_COUNT)
with q2:
    st.metric("Quote-Enabled", len(HOLDINGS))
with q3:
    st.metric("Successfully Priced", len(successfully_priced))
with q4:
    st.metric("Live Weight Coverage", f"{sum(HOLDINGS[t]['weight'] for t in successfully_priced) * 100:.2f}%")

# Historical chart built from saved observations.
st.subheader("Estimated Intraday NAV")
history = get_history()

if not history.empty:
    history["timestamp"] = pd.to_datetime(history["timestamp"])
    recent = history.tail(500)

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=recent["timestamp"],
            y=recent["estimated_nav"],
            mode="lines",
            name="Central Estimate",
            line=dict(width=3),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=recent["timestamp"],
            y=recent["conservative_nav"],
            mode="lines",
            name="Conservative",
            line=dict(dash="dot"),
        )
    )
    fig.add_hline(
        y=official_nav,
        line_dash="dash",
        annotation_text="Official NAV",
    )
    fig.update_layout(
        height=450,
        margin=dict(
            l=20,
            r=20,
            t=20,
            b=20,
        ),
        xaxis_title="Time",
        yaxis_title="Estimated NAV",
        hovermode="x unified",
    )
    st.plotly_chart(
        fig,
        use_container_width=True,
    )
else:
    st.info(
        "Intraday history will appear here "
        "after observations are collected."
    )

# Compare the latest estimate with the published NAV baseline.
st.subheader("Estimate vs. Published NAV")

comparison1, comparison2, comparison3 = st.columns(3)

with comparison1:
    st.metric(
        "Published NAV Baseline",
        money(official_nav),
    )

with comparison2:
    st.metric(
        "Current Estimated NAV",
        money(model["estimated_nav"]),
        delta=money(model["estimated_nav"] - official_nav),
    )

with comparison3:
    estimated_pct = (model["estimated_nav"] / official_nav) - 1
    st.metric(
        "Estimated NAV Move",
        percent(estimated_pct),
    )

st.caption(
    "After the next mutual-fund NAV is published, the new published NAV "
    "becomes the baseline for the following trading session."
)

# Model coverage and most recent update time.
st.subheader("Model Information")
m1, m2, m3 = st.columns(3)

with m1:
    st.write(
        f"Live-priced weight: {TRACKED_WEIGHT * 100:.2f}%"
    )

with m2:
    st.write(
        f"Stocks available: {len(current_prices)} / "
        f"{len(HOLDINGS)}"
    )

with m3:
    now = datetime.now(TIMEZONE)
    st.write(
        "Last update: "
        f"{now.strftime('%I:%M:%S %p ET')}"
    )

st.warning(
    "This is an analytical estimate, not an official "
    "FSELX NAV and not a price at which FSELX can be "
    "bought or sold. The remaining portfolio, cash, "
    "foreign-market timing, fund expenses, portfolio "
    "changes, dividends and other factors can cause "
    "the actual NAV to differ."
)

st.divider()
st.caption(f"Refresh interval: {refresh} seconds.")

# Automatic refresh without requiring an extra package.
import time
time.sleep(refresh)
st.rerun()
