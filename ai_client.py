import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from textblob import TextBlob
import datetime
import nltk

# --- SERVER CACHE SETUP FOR NLP ---
@st.cache_resource
def download_nltk_data():
    try:
        nltk.download('punkt', quiet=True)
        nltk.download('punkt_tab', quiet=True)
    except Exception:
        pass
download_nltk_data()

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import NewsClient
from alpaca.data.requests import NewsRequest

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="Quant Day Trader", layout="wide", initial_sidebar_state="collapsed")

# --- INITIALIZE ALPACA ---
try:
    API_KEY = st.secrets["ALPACA_API_KEY"]
    SECRET_KEY = st.secrets["ALPACA_SECRET_KEY"]
    trading_client = TradingClient(API_KEY, SECRET_KEY, paper=True)
    news_client = NewsClient(API_KEY, SECRET_KEY)
except Exception:
    st.error("Alpaca API keys not found in Streamlit Secrets. Cannot initialize terminal.")
    st.stop()

# --- SESSION STATE (ROUTER) ---
if "selected_ticker" not in st.session_state:
    st.session_state.selected_ticker = None

def go_back():
    st.session_state.selected_ticker = None

# --- DATA & MATH ENGINES ---
def fetch_1min_data(ticker: str) -> pd.DataFrame:
    """Fetches the latest 1-minute candles for the day trading view."""
    df = yf.download(ticker, period="1d", interval="1m", progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df

def calculate_technicals(df: pd.DataFrame):
    """Calculates RSI, MACD, and ATR on the 1-minute chart."""
    close = df["Close"].squeeze()
    
    # RSI (14)
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss
    df["RSI"] = 100 - (100 / (1 + rs))

    # MACD (12, 26, 9)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["MACD"] = ema12 - ema26
    df["Signal"] = df["MACD"].ewm(span=9, adjust=False).mean()

    # ATR (14)
    high, low, prev_close = df["High"].squeeze(), df["Low"].squeeze(), close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    df["ATR"] = tr.rolling(14).mean()
    
    return df

def fetch_alpaca_news(ticker: str):
    """Pulls the latest 3 headlines from Alpaca and scores the sentiment."""
    try:
        req = NewsRequest(symbols=ticker, limit=3)
        news = news_client.get_news(req)
        
        articles = []
        for article in news.news:
            polarity = TextBlob(article.headline).sentiment.polarity
            articles.append({
                "Headline": article.headline,
                "Sentiment": "Positive" if polarity > 0.05 else "Negative" if polarity < -0.05 else "Neutral"
            })
        return pd.DataFrame(articles)
    except Exception:
        return pd.DataFrame()

# --- VISUAL ENGINES ---
def generate_coach_translation(rsi, macd, signal, atr):
    """Translates the math into actionable Day Trading English."""
    insights = []
    
    # RSI Logic
    if rsi >= 70:
        insights.append(f"🔥 **RSI ({rsi:.1f}):** Running incredibly hot. Buyers are exhausted. Don't buy the top; watch for a pullback.")
    elif rsi <= 30:
        insights.append(f"🧊 **RSI ({rsi:.1f}):** Heavily oversold. Panic selling might be ending. Watch for a bounce.")
    else:
        insights.append(f"⚖️ **RSI ({rsi:.1f}):** Neutral territory. Market is deciding the next move.")

    # MACD Logic
    if macd > signal:
        insights.append("📈 **MACD:** Momentum is Bullish. Buyers control the 1-minute trend.")
    else:
        insights.append("📉 **MACD:** Momentum is Bearish. Sellers control the 1-minute trend.")

    # ATR Logic
    insights.append(f"📏 **ATR:** Moving ${atr:.2f} per minute. Keep stops wider than this to avoid getting chopped out.")

    return "\n\n".join(insights)

def draw_annotated_chart(df, ticker):
    """Draws the live dark-mode chart with visual tripwires."""
    current_price = float(df["Close"].iloc[-1])
    current_atr = float(df["ATR"].iloc[-1])
    stop_level = current_price - (current_atr * 1.5)

    fig = go.Figure(data=[go.Candlestick(
        x=df.index, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"], name="Price"
    )])

    # Draw ATR Stop Loss Line
    fig.add_hline(
        y=stop_level, line_dash="dot", line_color="red", 
        annotation_text=f"ATR Stop (${stop_level:.2f})", annotation_position="bottom right"
    )

    # Highlight Oversold RSI Bounces visually
    if len(df) >= 3 and df["RSI"].iloc[-3] < 30 and df["RSI"].iloc[-1] >= 30:
        fig.add_annotation(
            x=df.index[-1], y=df["Low"].iloc[-1],
            text="Oversold Bounce", showarrow=True, arrowhead=1, arrowcolor="green", ax=0, ay=40
        )

    fig.update_layout(
        title=f"{ticker} Live 1-Minute Action",
        xaxis_rangeslider_visible=False, height=450, template="plotly_dark",
        margin=dict(l=0, r=0, t=40, b=0)
    )
    return fig

# ==========================================
# SCREEN 1: THE RADAR SCANNER
# ==========================================
def render_scanner_screen():
    st.title("📡 Tactical Market Scanner")
    st.caption("Active anomalies detected. Click a ticker to enter the Control Center.")
    st.markdown("---")

    # Dynamic candle countdown timer
    seconds_to_close = 60 - datetime.datetime.now().second

    # Mock Scanner Results (In a full build, this loops through the S&P 500 automatically)
    opportunities = [
        {"ticker": "NVDA", "reason": "RSI dropped below 30. Extreme oversold bounce setup."},
        {"ticker": "AAPL", "reason": "MACD crossed the signal line. Bullish intraday shift."},
        {"ticker": "TSLA", "reason": "Price compressing at the bottom of the ATR channel."},
    ]

    for opp in opportunities:
        c1, c2, c3, c4 = st.columns([1, 3, 1, 1])
        with c1: st.subheader(opp["ticker"])
        with c2: st.markdown(f"**Why to look:** {opp['reason']}")
        with c3: st.error(f"⏱ {seconds_to_close}s to close")
        with c4:
            if st.button(f"Analyze {opp['ticker']}", key=opp["ticker"], use_container_width=True):
                st.session_state.selected_ticker = opp["ticker"]
                st.rerun()
        st.markdown("---")

# ==========================================
# SCREEN 2: THE DAY TRADER COCKPIT
# ==========================================
def render_tactical_screen(ticker):
    st.button("⬅ Back to Radar", on_click=go_back)
    
    with st.spinner(f"Connecting to {ticker} Live Feed..."):
        df = fetch_1min_data(ticker)
        if df.empty:
            st.error("Market data unavailable. (Is the market open?)")
            st.stop()
            
        df = calculate_technicals(df)
        news_df = fetch_alpaca_news(ticker)
        
        # Extract current live values
        latest = df.iloc[-1]
        price = float(latest["Close"])
        rsi = float(latest["RSI"])
        macd = float(latest["MACD"])
        signal = float(latest["Signal"])
        atr = float(latest["ATR"])

    # Top Metrics Bar
    m1, m2, m3 = st.columns(3)
    m1.metric("Live Price", f"${price:,.2f}")
    m2.metric("1m RSI", f"{rsi:.1f}")
    m3.metric("Minute Volatility (ATR)", f"${atr:.2f}")

    # Main Dashboard Split
    col_chart, col_data = st.columns([2, 1])

    with col_chart:
        st.plotly_chart(draw_annotated_chart(df, ticker), use_container_width=True)

    with col_data:
        st.subheader("🤖 The Coach's Read")
        st.info(generate_coach_translation(rsi, macd, signal, atr))
        
        st.subheader("📰 Alpaca Live News")
        if not news_df.empty:
            def color_sentiment(val):
                return 'color: #22c55e' if val == "Positive" else 'color: #ef4444' if val == "Negative" else 'color: #a3a3a3'
            styled = news_df.style.map(color_sentiment, subset=["Sentiment"])
            st.dataframe(styled, use_container_width=True, hide_index=True)
        else:
            st.caption("No breaking news in the last hour.")

    # Execution Deck
    st.markdown("---")
    st.subheader("⚡ Execution Deck")
    
    # Risk calculation
    account_size = 10000.0  # Assumed paper balance
    risk_pct = 0.01
    stop_distance = atr * 1.5
    shares = int((account_size * risk_pct) // stop_distance) if stop_distance > 0 else 0
    
    st.caption(f"Calculated optimal sizing: **{shares} shares** (Based on $10k account & 1% risk)")
    
    c1, c2 = st.columns(2)
    with c1:
        if st.button(f"🟢 MARKET BUY {shares} SHARES", use_container_width=True, type="primary"):
            try:
                req = MarketOrderRequest(symbol=ticker, qty=shares, side=OrderSide.BUY, time_in_force=TimeInForce.GTC)
                trading_client.submit_order(order_data=req)
                st.success("Order Routed to Alpaca successfully.")
            except Exception as e:
                st.error(f"Execution Failed: {e}")
                
    with c2:
        if st.button(f"🔴 MARKET SELL {shares} SHARES", use_container_width=True):
            try:
                req = MarketOrderRequest(symbol=ticker, qty=shares, side=OrderSide.SELL, time_in_force=TimeInForce.GTC)
                trading_client.submit_order(order_data=req)
                st.success("Order Routed to Alpaca successfully.")
            except Exception as e:
                st.error(f"Execution Failed: {e}")

# ==========================================
# MAIN ROUTER
# ==========================================
if st.session_state.selected_ticker is None:
    render_scanner_screen()
else:
    render_tactical_screen(st.session_state.selected_ticker)
