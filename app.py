import re
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

# Published top-10 holdings used by the intraday estimation model.
# Update these holdings and weights when Fidelity publishes newer data.
HOLDINGS = {
    "NVDA": {"name": "NVIDIA", "weight": 0.2464},
    "AVGO": {"name": "Broadcom", "weight": 0.1588},
    "MRVL": {"name": "Marvell Technology", "weight": 0.0651},
    "MPWR": {"name": "Monolithic Power Systems", "weight": 0.0593},
    "ASML": {"name": "ASML", "weight": 0.0590},
    "ALAB": {"name": "Astera Labs", "weight": 0.0551},
    "NXPI": {"name": "NXP Semiconductors", "weight": 0.0434},
    "ON": {"name": "ON Semiconductor", "weight": 0.0422},
    "GFS": {"name": "GlobalFoundries", "weight": 0.0355},
    "WDC": {"name": "Western Digital", "weight": 0.0350},
}

TOP10_WEIGHT = sum(x["weight"] for x in HOLDINGS.values())

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


# Extract a plausible official NAV from the Fidelity page.
def get_official_nav():
    """Return the latest published FSELX mutual-fund NAV from Yahoo Finance."""
    try:
        fund = yf.Ticker(FSELX_SYMBOL)
        history = fund.history(
            period="10d",
            interval="1d",
            auto_adjust=False,
        )

        if history is None or history.empty or "Close" not in history.columns:
            return None, "Yahoo Finance returned no FSELX daily NAV history."

        closes = history["Close"].dropna()
        if closes.empty:
            return None, "Yahoo Finance returned no usable FSELX closing NAV."

        value = float(closes.iloc[-1])

        if not 1 < value < 1000:
            return None, f"Yahoo Finance returned an unexpected FSELX NAV: {value}"

        return value, None

    except Exception as exc:
        return None, f"FSELX NAV lookup failed: {exc}"


@st.cache_data(ttl=45, show_spinner=False)
# Download 5-minute market prices for the modeled holdings.
def get_intraday_data():
    return yf.download(
        tickers=list(HOLDINGS.keys()),
        period="2d",
        interval="5m",
        auto_adjust=False,
        prepost=False,
        progress=False,
        threads=True,
        group_by="column",
    )


@st.cache_data(ttl=300, show_spinner=False)
# Download daily prices used to determine the previous close.
def get_daily_data():
    return yf.download(
        tickers=list(HOLDINGS.keys()),
        period="10d",
        interval="1d",
        auto_adjust=False,
        progress=False,
        threads=True,
        group_by="column",
    )


# Pull the latest price and previous close for each available holding.
def extract_prices(intraday, daily):
    current = {}
    previous = {}
    errors = []

    try:
        if (
            intraday is not None
            and not intraday.empty
            and isinstance(intraday.columns, pd.MultiIndex)
            and "Close" in intraday.columns.get_level_values(0)
        ):
            closes = intraday["Close"]
            for ticker in HOLDINGS:
                if ticker in closes.columns:
                    series = closes[ticker].dropna()
                    if not series.empty:
                        current[ticker] = float(series.iloc[-1])
        else:
            errors.append("No usable Yahoo intraday data.")
    except Exception as exc:
        errors.append(f"Intraday error: {exc}")

    try:
        if (
            daily is not None
            and not daily.empty
            and isinstance(daily.columns, pd.MultiIndex)
            and "Close" in daily.columns.get_level_values(0)
        ):
            closes = daily["Close"]
            for ticker in HOLDINGS:
                if ticker in closes.columns:
                    series = closes[ticker].dropna()
                    if len(series) >= 2:
                        previous[ticker] = float(series.iloc[-2])
        else:
            errors.append("No usable Yahoo daily data.")
    except Exception as exc:
        errors.append(f"Daily data error: {exc}")

    return current, previous, errors


# =============================
# INTRADAY FSELX ESTIMATION MODEL
# =============================
# Conservative estimate uses actual published fund weights.
# Normalized estimate scales the available top-10 holdings to the modeled basket.
# Central estimate is the midpoint of the conservative and normalized estimates.
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
        normalized_weight = weight / TOP10_WEIGHT
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
    "Intraday estimate based on published top-10 holdings"
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
    refresh = st.slider(
        "Refresh interval",
        30,
        300,
        60,
        30,
    )
    st.divider()
    st.write(f"Top-10 coverage: {TOP10_WEIGHT * 100:.2f}%")
    st.write(f"Holdings date: {HOLDINGS_DATE}")
    st.divider()

    if st.button("Clear cached data"):
        get_intraday_data.clear()
        get_daily_data.clear()
        st.rerun()

# Verify the published FSELX NAV and holdings market data before calculating the estimate.
st.subheader("Data Status")
status1, status2 = st.columns(2)

with status1:
    st.write("FSELX Published NAV")

official_nav, fidelity_error = get_official_nav()

if official_nav is None:
    with status1:
        st.error("FSELX NAV unavailable")
    st.error("Could not retrieve the latest published FSELX NAV from Yahoo Finance.")
    if fidelity_error:
        st.code(fidelity_error)
    st.stop()

with status1:
    st.success(f"Published FSELX NAV: {money(official_nav)}")

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
        f"{len(HOLDINGS)} holdings"
    )

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

# Prominent daily estimated gain/loss summary.
st.subheader("Today's Estimated Move")

move1, move2, move3 = st.columns(3)

with move1:
    st.metric(
        "Estimated FSELX Change",
        percent((model["estimated_nav"] / official_nav) - 1),
    )

with move2:
    st.metric(
        "Estimated Dollar Gain / Loss",
        money(model["estimated_gain"]),
    )

with move3:
    st.metric(
        "Estimated Position Value",
        money(model["estimated_value"]),
        delta=money(model["estimated_gain"]),
    )

# Main NAV and portfolio-value estimates.
st.subheader("Current Estimate")
c1, c2, c3, c4 = st.columns(4)

with c1:
    st.metric(
        "Published FSELX NAV",
        money(official_nav),
    )

with c2:
    st.metric(
        "Estimated Intraday NAV",
        money(model["estimated_nav"]),
        delta=percent(model["normalized_change"]),
    )

with c3:
    st.metric(
        "Estimated Position",
        money(model["estimated_value"]),
    )

with c4:
    st.metric(
        "Estimated Position Change",
        money(model["estimated_gain"]),
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
st.subheader("Top 10 Holdings")
table = pd.DataFrame(model["rows"])

if not table.empty:
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
        f"Top-10 coverage: {TOP10_WEIGHT * 100:.2f}%"
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

try:
    @st.fragment(run_every=refresh)
    def refresh_status():
        st.caption("Automatic refresh is active.")

    refresh_status()
except Exception:
    st.caption(
        "Automatic refresh is unavailable. "
        "Refresh the browser manually."
    )
