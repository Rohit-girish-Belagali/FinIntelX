import os
import requests
import configparser
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

# Read FMP API key
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "core", "config", "config.ini")

def get_fmp_api_key() -> str:
    config = configparser.ConfigParser()
    if os.path.exists(CONFIG_PATH):
        try:
            config.read(CONFIG_PATH)
            return config.get("API_KEYS", "fmp_api_key", fallback="")
        except Exception:
            pass
    return ""

def format_large_number(num: Optional[float]) -> str:
    """Format large numbers like Market Cap or Volume (e.g. 1.23tn, 45.6m, 789k)"""
    if num is None:
        return "--"
    try:
        val = float(num)
        if val >= 1e12:
            return f"{val / 1e12:.2f}tn"
        elif val >= 1e9:
            return f"{val / 1e9:.2f}bn"
        elif val >= 1e6:
            return f"{val / 1e6:.2f}m"
        elif val >= 1e3:
            return f"{val / 1e3:.2f}k"
        else:
            return f"{val:.2f}"
    except Exception:
        return "--"

async def fetch_stock_quote(symbol: str, market: str = "US") -> Dict[str, Any]:
    """
    Fetch a unified quote for a given symbol.
    If market == 'US', first tries FMP, falls back to yfinance.
    If market == 'IN' or anything else, uses yfinance.
    """
    symbol = symbol.strip().upper()
    api_key = get_fmp_api_key()
    
    # Try FMP for US market
    if market == "US" and api_key:
        try:
            # 1. Fetch quote
            url = f"https://financialmodelingprep.com/stable/quote/{symbol}?apikey={api_key}"
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                if data and isinstance(data, list) and len(data) > 0:
                    q = data[0]
                    # Fetch sparkline history (last 30 days)
                    spark_url = f"https://financialmodelingprep.com/stable/historical-price-eod/full?symbol={symbol}&apikey={api_key}"
                    s_resp = requests.get(spark_url, timeout=5)
                    spark_data = []
                    if s_resp.status_code == 200:
                        s_json = s_resp.json()
                        hist = s_json if isinstance(s_json, list) else s_json.get('historical', [])
                        # Extract close prices in chronological order
                        hist_closes = [float(day.get('close', 0)) for day in hist[:30]]
                        spark_data = list(reversed(hist_closes)) if hist_closes else []
                    
                    price = float(q.get('price') or 0)
                    prev_close = float(q.get('previousClose') or price)
                    change = float(q.get('change') or (price - prev_close))
                    change_pct = float(q.get('changePercent') or 0)
                    
                    raw_mktcap = q.get('marketCap')
                    
                    return {
                        "symbol": symbol,
                        "name": q.get('name') or symbol,
                        "price": price,
                        "change": change,
                        "changePercent": change_pct,
                        "prevClose": prev_close,
                        "open": float(q.get('open')) if q.get('open') is not None else None,
                        "high": float(q.get('dayHigh')) if q.get('dayHigh') is not None else None,
                        "low": float(q.get('dayLow')) if q.get('dayLow') is not None else None,
                        "volume": format_large_number(q.get('volume')),
                        "marketCap": format_large_number(raw_mktcap),
                        "currency": "$",
                        "sparkline": spark_data,
                        "marketStatus": "open" if q.get('isActivelyTrading') else "closed"
                    }
        except Exception as e:
            print(f"FMP Quote fetch failed for {symbol}: {e}. Falling back to yfinance.")
            
    # Fallback/Default: yfinance (handles US, India, Crypto, etc.)
    try:
        yf_symbol = symbol
        
        if market == "IN" and not symbol.endswith(".NS") and not symbol.endswith(".BO"):
            # Try NSE first
            yf_symbol = f"{symbol}.NS"
            
        ticker_obj = yf.Ticker(yf_symbol)
        
        # Get history for sparkline (last 1 month)
        hist = ticker_obj.history(period="1mo")
        spark_data = [float(x) for x in hist['Close'].tolist()] if not hist.empty else []
        
        info = ticker_obj.info
        if not info and hist.empty:
            raise Exception("No data returned by yfinance")
            
        price = info.get('currentPrice') or info.get('regularMarketPrice')
        if price is None and not hist.empty:
            price = float(hist['Close'].iloc[-1])
            
        prev_close = info.get('previousClose') or info.get('regularMarketPreviousClose')
        if prev_close is None and not hist.empty:
            prev_close = float(hist['Close'].iloc[0]) if len(hist) > 1 else price
            
        if price is not None:
            change = price - prev_close
            change_pct = (change / prev_close) * 100 if prev_close else 0
        else:
            price, change, change_pct = 0.0, 0.0, 0.0
            
        currency = "$"
        currency_code = info.get('currency', 'USD')
        if currency_code == "INR" or market == "IN" or yf_symbol.endswith(".NS") or yf_symbol.endswith(".BO"):
            currency = "₹"
        elif currency_code == "EUR":
            currency = "€"
        elif currency_code == "KRW":
            currency = "₩"
            
        return {
            "symbol": symbol,
            "name": info.get('longName') or info.get('shortName') or symbol,
            "price": price,
            "change": change,
            "changePercent": change_pct,
            "prevClose": prev_close,
            "open": info.get('open') or info.get('regularMarketOpen'),
            "high": info.get('dayHigh') or info.get('regularMarketDayHigh'),
            "low": info.get('dayLow') or info.get('regularMarketDayLow'),
            "volume": format_large_number(info.get('volume') or info.get('regularMarketVolume')),
            "marketCap": format_large_number(info.get('marketCap')),
            "currency": currency,
            "sparkline": spark_data,
            "marketStatus": "open"
        }
    except Exception as e:
        print(f"yfinance fetch failed for {symbol}: {e}")
        return {
            "symbol": symbol,
            "name": symbol,
            "price": 0.0,
            "change": 0.0,
            "changePercent": 0.0,
            "prevClose": 0.0,
            "open": None,
            "high": None,
            "low": None,
            "volume": "--",
            "marketCap": "--",
            "currency": "$" if market == "US" else "₹",
            "sparkline": [],
            "marketStatus": "closed"
        }

async def fetch_batch_quotes(symbols: List[str], market: str = "US") -> List[Dict[str, Any]]:
    """Fetch quotes in batch for efficiency"""
    import asyncio
    tasks = [fetch_stock_quote(sym, market) for sym in symbols]
    return await asyncio.gather(*tasks)

async def fetch_market_indices(market: str = "US") -> List[Dict[str, Any]]:
    """Fetch major market indices (e.g. S&P 500, Dow Jones, NIFTY 50, SENSEX)"""
    if market == "US":
        symbols = ["^GSPC", "^IXIC", "^DJI"]
        names = {"^GSPC": "S&P 500", "^IXIC": "NASDAQ", "^DJI": "Dow Jones"}
    else:
        symbols = ["^NSEI", "^BSESN"]
        names = {"^NSEI": "NIFTY 50", "^BSESN": "SENSEX"}
        
    quotes = []
    for sym in symbols:
        try:
            ticker = yf.Ticker(sym)
            hist = ticker.history(period="1mo")
            spark_data = [float(x) for x in hist['Close'].tolist()] if not hist.empty else []
            info = ticker.info
            price = info.get('regularMarketPrice')
            if price is None and not hist.empty:
                price = float(hist['Close'].iloc[-1])
            prev_close = info.get('previousClose') or info.get('regularMarketPreviousClose')
            if prev_close is None and not hist.empty:
                prev_close = float(hist['Close'].iloc[0]) if len(hist) > 1 else price
                
            if price is not None:
                change = price - prev_close
                change_pct = (change / prev_close) * 100 if prev_close else 0
            else:
                price, change, change_pct = 0.0, 0.0, 0.0
                
            quotes.append({
                "symbol": sym.replace("^", "."),
                "name": names.get(sym, sym),
                "price": price,
                "change": change,
                "changePercent": change_pct,
                "currency": "$" if market == "US" else "₹",
                "sparkline": spark_data
            })
        except Exception as e:
            print(f"Failed to fetch index {sym}: {e}")
    return quotes

async def search_ticker(query: str, market: str = "US") -> List[Dict[str, Any]]:
    """Search for matching symbols"""
    query = query.strip().upper()
    if not query:
        return []
        
    us_stocks = [
        {"symbol": "AAPL", "name": "Apple Inc."},
        {"symbol": "TSLA", "name": "Tesla, Inc."},
        {"symbol": "MSFT", "name": "Microsoft Corporation"},
        {"symbol": "GOOGL", "name": "Alphabet Inc."},
        {"symbol": "AMZN", "name": "Amazon.com, Inc."},
        {"symbol": "NVDA", "name": "NVIDIA Corporation"},
        {"symbol": "META", "name": "Meta Platforms, Inc."},
        {"symbol": "JPM", "name": "JPMorgan Chase & Co."},
        {"symbol": "NFLX", "name": "Netflix, Inc."},
        {"symbol": "DIS", "name": "The Walt Disney Company"},
    ]
    
    in_stocks = [
        {"symbol": "RELIANCE", "name": "Reliance Industries Ltd"},
        {"symbol": "TCS", "name": "Tata Consultancy Services Ltd"},
        {"symbol": "INFY", "name": "Infosys Ltd"},
        {"symbol": "HDFCBANK", "name": "HDFC Bank Ltd"},
        {"symbol": "ADANIENT", "name": "Adani Enterprises Ltd"},
        {"symbol": "ADANIGREEN", "name": "Adani Green Energy Ltd"},
        {"symbol": "ADANIPORTS", "name": "Adani Ports & SEZ Ltd"},
        {"symbol": "TATASTEEL", "name": "Tata Steel Ltd"},
        {"symbol": "WIPRO", "name": "Wipro Ltd"},
        {"symbol": "ITC", "name": "ITC Ltd"},
        {"symbol": "SBIN", "name": "State Bank of India"},
    ]
    
    stock_universe = us_stocks if market == "US" else in_stocks
    results = [s for s in stock_universe if query in s["symbol"] or query in s["name"].upper()]
    
    if len(results) < 5:
        try:
            import urllib.parse
            url = f"https://query2.finance.yahoo.com/v1/finance/search?q={urllib.parse.quote(query)}"
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            r = requests.get(url, headers=headers, timeout=5)
            if r.status_code == 200:
                data = r.json()
                for item in data.get("quotes", []):
                    symbol = item.get("symbol", "")
                    name = item.get("shortname") or item.get("longname") or symbol
                    # Filter NSE/BSE for India, others for US
                    if market == "IN" and (symbol.endswith(".NS") or symbol.endswith(".BO")):
                        results.append({"symbol": symbol.split(".")[0], "name": name})
                    elif market == "US" and not ("." in symbol):
                        results.append({"symbol": symbol, "name": name})
        except Exception as e:
            print(f"Yahoo Search failed: {e}")
            
    # Deduplicate results
    seen = set()
    deduped = []
    for r in results:
        if r["symbol"] not in seen:
            seen.add(r["symbol"])
            deduped.append(r)
            
    return deduped[:10]
