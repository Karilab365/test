# app.py
import streamlit as st
# This must be the first Streamlit command
st.set_page_config(page_title="Xiaomi Stock Sentiment Analysis Platform", layout="wide")

import pandas as pd
import numpy as np
import yfinance as yf
import feedparser
import requests
import re
import time
import json
import matplotlib.pyplot as plt
import seaborn as sns
import plotly.graph_objects as go
from datetime import datetime, timedelta
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import PolynomialFeatures
from langdetect import detect
from transformers import pipeline
from openai import OpenAI
import warnings

# Ignore warnings
warnings.filterwarnings("ignore")

# --- Global Configuration ---

# Load OpenAI API key
openai_api_key = st.secrets["openai_api_key"]
client = OpenAI(api_key=openai_api_key)

# Initialize HuggingFace sentiment models
try:
    sentiment_en = pipeline("sentiment-analysis", model="distilbert-base-uncased-finetuned-sst-2-english")
    sentiment_zh = pipeline("sentiment-analysis", model="uer/roberta-base-finetuned-chinanews-chinese")
except Exception:
    sentiment_en = None
    sentiment_zh = None

# Supported languages
SUPPORTED_LANGUAGES = {
    "English": ("en-US", "US:en"),
    "Simplified Chinese": ("zh-CN", "CN:zh-Hans"),
    "Traditional Chinese": ("zh-TW", "TW:zh-Hant"),
    "Spanish": ("es-ES", "ES:es")
}

# Default stock symbols
STOCK_SYMBOLS = {
    "Hong Kong Stock Exchange": "1810.HK",
    "OTC Market": "XIACF"
}

# --- Session State Initialization ---

if "news_cache" not in st.session_state:
    st.session_state.news_cache = {}
if "stock_cache" not in st.session_state:
    st.session_state.stock_cache = {}
if "current_news_df" not in st.session_state:
    st.session_state.current_news_df = pd.DataFrame()
if "historical_news_df" not in st.session_state:
    st.session_state.historical_news_df = pd.DataFrame()
if "stock_data_df" not in st.session_state:
    st.session_state.stock_data_df = pd.DataFrame()
if "detailed_sentiment_df" not in st.session_state:
    st.session_state.detailed_sentiment_df = pd.DataFrame()
if "prediction_df" not in st.session_state:
    st.session_state.prediction_df = pd.DataFrame()
if "api_valid" not in st.session_state:
    st.session_state.api_valid = False
if "preferred_language" not in st.session_state:
    st.session_state.preferred_language = "English"
if "preferred_stock" not in st.session_state:
    st.session_state.preferred_stock = "1810.HK"

# --- Function Definitions ---

@st.cache_data(show_spinner=True)
def fetch_google_news(keyword="Xiaomi", language_code="en-US", region_code="US:en", max_articles=100):
    """Fetch Google News RSS feed results."""
    query = keyword.replace(" ", "+")
    feed_url = f"https://news.google.com/rss/search?q={query}&hl={language_code}&gl={region_code.split(':')[0]}&ceid={region_code}"
    try:
        feed = feedparser.parse(feed_url)
        entries = []
        for entry in feed.entries[:max_articles]:
            try:
                published_date = datetime(*entry.published_parsed[:6]) if hasattr(entry, 'published_parsed') else datetime.now()
                title = re.sub(r'<[^>]+>', '', entry.title)
                entries.append({
                    "title": title,
                    "link": entry.link,
                    "published": published_date
                })
            except Exception:
                continue
        return pd.DataFrame(entries)
    except Exception as e:
        st.error(f"Failed to fetch news: {str(e)}")
        return pd.DataFrame()

@st.cache_data(show_spinner=True)
def fetch_stock_data(symbol="1810.HK", period="1y"):
    """Fetch stock historical data using Yahoo Finance."""
    try:
        stock = yf.Ticker(symbol)
        hist = stock.history(period=period)
        if hist.empty:
            return pd.DataFrame()
        hist['MA5'] = hist['Close'].rolling(window=5).mean()
        hist['MA20'] = hist['Close'].rolling(window=20).mean()
        return hist
    except Exception as e:
        st.error(f"Failed to fetch stock data: {str(e)}")
        return pd.DataFrame()

@st.cache_data(show_spinner=False)
def analyze_sentiment(text, detailed=False):
    """Analyze sentiment using HuggingFace or OpenAI."""
    cleaned_text = re.sub(r'<[^>]+>', '', text)
    try:
        lang = detect(cleaned_text)
    except:
        lang = "en"

    # Try HuggingFace model first
    if not detailed:
        try:
            if lang == "en" and sentiment_en is not None:
                result = sentiment_en(cleaned_text)[0]
                return 1 if result["label"] == "POSITIVE" else -1
            elif lang == "zh" and sentiment_zh is not None:
                result = sentiment_zh(cleaned_text)[0]
                return 1 if result["label"] == "positive" else -1
        except Exception:
            pass

    # Fallback: OpenAI
    try:
        if not detailed:
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "Analyze sentiment as POSITIVE, NEUTRAL, or NEGATIVE."},
                    {"role": "user", "content": cleaned_text}
                ],
                max_tokens=10,
                temperature=0.3
            )
            result = response.choices[0].message.content.strip().upper()
            if "POSITIVE" in result:
                return 1
            elif "NEGATIVE" in result:
                return -1
            else:
                return 0
    except Exception:
        pass

    return 0

def batch_sentiment_analysis(df, max_count=30):
    """Batch analyze sentiment for a dataframe."""
    results = []
    df_subset = df.head(max_count)
    for _, row in df_subset.iterrows():
        try:
            text = row['title']
            score = analyze_sentiment(text)
            label = "Positive" if score > 0 else "Negative" if score < 0 else "Neutral"
            results.append({
                "title": text,
                "score": score,
                "label": label,
                "time": row.get('published', datetime.now())
            })
            time.sleep(0.2)  # prevent API rate limit
        except Exception:
            continue
    return pd.DataFrame(results)

@st.cache_data(show_spinner=True)
def predict_stock_trend(stock_data, days_ahead=7):
    """Predict future stock prices based on historical data."""
    if stock_data.empty or 'Close' not in stock_data.columns:
        return pd.DataFrame()

    df = stock_data.copy()
    df['day_of_week'] = df.index.dayofweek
    df['month'] = df.index.month

    X = df[['day_of_week', 'month']].values
    y = df['Close'].values

    poly = PolynomialFeatures(degree=2)
    X_poly = poly.fit_transform(X)

    model = LinearRegression()
    model.fit(X_poly, y)

    last_date = df.index[-1]
    future_dates = [last_date + timedelta(days=i+1) for i in range(days_ahead)]
    
    # Fixed: proper access of dayofweek attribute
    future_features = [[d.dayofweek, d.month] for d in future_dates]
    future_features_poly = poly.transform(future_features)

    preds = model.predict(future_features_poly)

    return pd.DataFrame({
        "Date": future_dates,
        "Predicted_Close": preds
    }).set_index("Date")

def check_openai_api_validity():
    """Check if the OpenAI API key is valid."""
    try:
        url = "https://api.openai.com/v1/models"
        headers = {
            "Authorization": f"Bearer {openai_api_key}",
            "Content-Type": "application/json"
        }
        response = requests.get(url, headers=headers)
        return response.status_code == 200
    except Exception:
        return False

# --- Streamlit UI Layout ---

st.title("📊 Xiaomi Stock Sentiment Analysis Platform v2.0")

# Sidebar
st.sidebar.header("Control Panel")
tab_choice = st.sidebar.radio(
    "Select a module:",
    ("News", "Stock Data", "Sentiment Analysis", "Batch Analysis", "Prediction", "Settings")
)

# --- Module Implementation ---

# News Module
if tab_choice == "News":
    st.subheader("📰 News Search")

    keyword = st.text_input("Enter Keyword", value="Xiaomi")
    lang_display = st.selectbox("Select News Language", list(SUPPORTED_LANGUAGES.keys()))
    max_articles = st.slider("Number of Articles", min_value=10, max_value=200, step=10, value=50)

    if st.button("Fetch News"):
        lang_code, region_code = SUPPORTED_LANGUAGES[lang_display]
        news_df = fetch_google_news(keyword=keyword, language_code=lang_code, region_code=region_code, max_articles=max_articles)

        if not news_df.empty:
            st.success(f"Fetched {len(news_df)} articles successfully.")
            st.session_state.current_news_df = news_df
        else:
            st.warning("No articles found.")

    if not st.session_state.current_news_df.empty:
        st.dataframe(st.session_state.current_news_df[['published', 'title', 'link']])

# Stock Data Module
elif tab_choice == "Stock Data":
    st.subheader("💹 Xiaomi Stock Data Overview")

    stock_symbol = st.selectbox(
        "Select Stock Symbol",
        list(STOCK_SYMBOLS.values()),
        index=list(STOCK_SYMBOLS.values()).index(st.session_state.preferred_stock)
    )

    stock_period = st.selectbox(
        "Select Historical Period",
        options=["1mo", "3mo", "6mo", "1y", "5y"],
        index=3
    )

    if st.button("Fetch Stock Data"):
        stock_data = fetch_stock_data(symbol=stock_symbol, period=stock_period)

        if not stock_data.empty:
            st.success(f"Fetched {len(stock_data)} stock records successfully.")
            st.session_state.stock_data_df = stock_data

            # Price Line Chart
            st.line_chart(stock_data[['Close', 'MA5', 'MA20']])

            # Volume Bar Chart
            fig, ax = plt.subplots(figsize=(12, 4))
            colors = ['green' if stock_data['Close'].iloc[i] > stock_data['Open'].iloc[i] else 'red'
                      for i in range(len(stock_data))]
            ax.bar(stock_data.index, stock_data['Volume'], color=colors, alpha=0.6)
            ax.set_ylabel('Volume')
            ax.set_title('Daily Trading Volume')
            st.pyplot(fig)

            # Latest Stats
            latest_close = stock_data['Close'].iloc[-1]
            st.metric(label="Latest Close Price", value=f"${latest_close:.2f}")

        else:
            st.warning("No stock data retrieved. Please try again.")

# Sentiment Analysis Module
elif tab_choice == "Sentiment Analysis":
    st.subheader("🧠 Single Article Sentiment Analysis")

    input_text = st.text_area("Enter news title or short text")

    if st.button("Analyze Sentiment"):
        if input_text.strip() == "":
            st.warning("Please input some text first.")
        else:
            sentiment = analyze_sentiment(input_text)

            if sentiment == 1:
                st.success("Sentiment: Positive 😊")
            elif sentiment == -1:
                st.error("Sentiment: Negative 😞")
            else:
                st.info("Sentiment: Neutral 😐")

# Batch Analysis Module
elif tab_choice == "Batch Analysis":
    st.subheader("📊 Batch News Sentiment Analysis")

    source_choice = st.selectbox("Select Source", ["Current News", "Historical News"])
    article_count = st.slider("Number of Articles to Analyze", min_value=5, max_value=50, step=5, value=20)

    if st.button("Run Batch Analysis"):
        if source_choice == "Current News":
            if st.session_state.current_news_df.empty:
                st.warning("No current news available. Please fetch news first.")
            else:
                target_df = st.session_state.current_news_df
        else:
            if st.session_state.historical_news_df.empty:
                st.warning("No historical news available. Please fetch historical news.")
            else:
                target_df = st.session_state.historical_news_df

        results_df = batch_sentiment_analysis(target_df, article_count)
        if not results_df.empty:
            st.session_state.detailed_sentiment_df = results_df
            st.success(f"Analyzed {len(results_df)} articles.")

            # Pie Chart
            sentiment_counts = results_df['label'].value_counts()
            fig1, ax1 = plt.subplots()
            ax1.pie(sentiment_counts, labels=sentiment_counts.index, autopct='%1.1f%%', startangle=90,
                    colors=['green', 'grey', 'red'])
            ax1.axis('equal')
            st.pyplot(fig1)

            # Histogram
            fig2, ax2 = plt.subplots()
            ax2.hist(results_df['score'], bins=10, color='skyblue', edgecolor='black')
            ax2.set_title('Sentiment Score Distribution')
            ax2.set_xlabel('Sentiment Score')
            ax2.set_ylabel('Frequency')
            st.pyplot(fig2)

            # Time Trend
            if 'time' in results_df.columns:
                results_df['time'] = pd.to_datetime(results_df['time'])
                results_df = results_df.sort_values('time')
                fig3, ax3 = plt.subplots()
                ax3.plot(results_df['time'], results_df['score'], marker='o', linestyle='-')
                ax3.axhline(0, color='gray', linestyle='--')
                ax3.set_title('Sentiment Trend Over Time')
                ax3.set_xlabel('Date')
                ax3.set_ylabel('Sentiment Score')
                st.pyplot(fig3)

            # Display Table
            st.dataframe(results_df[['time', 'label', 'score', 'title']])

            # Download option
            csv = results_df.to_csv(index=False).encode('utf-8')
            st.download_button("Download Results as CSV", data=csv, file_name="sentiment_analysis_results.csv", mime='text/csv')

        else:
            st.warning("No results to display.")

# Prediction Module
elif tab_choice == "Prediction":
    st.subheader("📈 Stock Price Forecast")

    forecast_days = st.selectbox("Forecast Days", [7, 14, 30], index=0)

    if st.button("Generate Forecast"):
        if st.session_state.stock_data_df.empty:
            st.warning("No stock data available. Please fetch stock data first.")
        else:
            prediction_df = predict_stock_trend(st.session_state.stock_data_df, forecast_days)
            if not prediction_df.empty:
                st.session_state.prediction_df = prediction_df

                st.success(f"Successfully generated {forecast_days}-day prediction.")

                # Plotting
                fig, ax = plt.subplots(figsize=(12, 6))
                ax.plot(st.session_state.stock_data_df.index, st.session_state.stock_data_df['Close'], label="Historical Close", color='blue')
                ax.plot(prediction_df.index, prediction_df['Predicted_Close'], linestyle='--', marker='o', color='orange', label="Predicted Close")
                ax.axvline(x=st.session_state.stock_data_df.index[-1], color='green', linestyle='--')
                ax.set_title('Stock Price Forecast')
                ax.set_xlabel('Date')
                ax.set_ylabel('Price')
                ax.legend()
                st.pyplot(fig)

                # Display Table
                st.dataframe(prediction_df)

                # Analysis
                start_price = prediction_df['Predicted_Close'].iloc[0]
                end_price = prediction_df['Predicted_Close'].iloc[-1]
                change = end_price - start_price
                pct_change = (change / start_price) * 100

                st.metric(label="Price Change (%)", value=f"{pct_change:.2f}%")
                if change > 0:
                    st.success("Uptrend expected 📈")
                elif change < 0:
                    st.error("Downtrend expected 📉")
                else:
                    st.info("Stable trend expected 🟰")
            else:
                st.warning("Failed to generate prediction.")

# Settings Module
elif tab_choice == "Settings":
    st.subheader("⚙️ Application Settings")

    # Preferred Language
    new_lang = st.selectbox(
        "Preferred News Language",
        options=list(SUPPORTED_LANGUAGES.keys()),
        index=list(SUPPORTED_LANGUAGES.keys()).index(st.session_state.preferred_language)
    )

    # Preferred Stock
    new_stock = st.selectbox(
        "Preferred Stock Symbol",
        options=list(STOCK_SYMBOLS.values()),
        index=list(STOCK_SYMBOLS.values()).index(st.session_state.preferred_stock)
    )

    if st.button("Save Preferences"):
        st.session_state.preferred_language = new_lang
        st.session_state.preferred_stock = new_stock
        st.success("Preferences updated successfully!")

    st.markdown("---")

    # API Key Test
    if st.button("Verify OpenAI API Key"):
        valid = check_openai_api_validity()
        if valid:
            st.success("✅ OpenAI API Key is valid and working!")
            st.session_state.api_valid = True
        else:
            st.error("❌ Invalid OpenAI API Key or API service error.")

    st.markdown("---")

    # Clear Cache
    if st.button("Clear Cached Data"):
        st.session_state.news_cache.clear()
        st.session_state.stock_cache.clear()
        st.session_state.current_news_df = pd.DataFrame()
        st.session_state.historical_news_df = pd.DataFrame()
        st.session_state.stock_data_df = pd.DataFrame()
        st.session_state.detailed_sentiment_df = pd.DataFrame()
        st.session_state.prediction_df = pd.DataFrame()
        st.success("All cached data cleared successfully.")
