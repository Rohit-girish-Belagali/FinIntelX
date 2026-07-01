import os
import re
import configparser
from openai import OpenAI
from typing import List, Dict, Any, AsyncGenerator
from .portfolio_engine import fetch_stock_quote

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "core", "config", "config.ini")

class FinancialChatEngine:
    def __init__(self):
        self.api_key = ""
        self.base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
        self.model = "gemini-2.5-flash"
        
        # Load from config.ini
        config = configparser.ConfigParser()
        if os.path.exists(CONFIG_PATH):
            try:
                config.read(CONFIG_PATH)
                self.api_key = config.get("API_KEYS", "openai_api_key", fallback="")
                self.base_url = config.get("API_KEYS", "openai_base_url", fallback=self.base_url)
                self.model = config.get("API_KEYS", "openai_model", fallback=self.model)
            except Exception as e:
                print(f"Error loading chat config: {e}")
                
        # Initialize client
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def _extract_tickers(self, message: str) -> List[str]:
        """Extract uppercase tickers between 2 and 6 letters"""
        words = re.findall(r'\b[A-Z]{2,6}\b', message.upper())
        exclude = {
            "BUY", "SELL", "HOLD", "NOW", "THE", "FOR", "AND", "ANY", "WHAT", 
            "HOW", "WHY", "WHO", "WHEN", "YOU", "OUR", "ITS", "ARE", "WAS", 
            "HAS", "HAD", "CAN", "OUT", "GET", "NOT", "YES", "INR", "USD",
            "STOCK", "SHOULD", "SUGGEST", "PORTFOLIO", "MARKET", "PRICE", "INDIA"
        }
        return [w for w in words if w not in exclude]

    async def _enrich_context_with_market_data(self, message: str, market: str = "US") -> str:
        """Detect tickers in message and fetch live quotes to attach as context"""
        tickers = self._extract_tickers(message)
        if not tickers:
            return ""
            
        context_parts = []
        for ticker in tickers[:3]:  # Limit to top 3 tickers
            try:
                quote = await fetch_stock_quote(ticker, market)
                if quote and quote.get('price', 0) > 0:
                    cur = quote.get('currency', '$')
                    price = quote.get('price', 0)
                    change = quote.get('change', 0)
                    change_pct = quote.get('changePercent', 0)
                    context_parts.append(
                        f"Live data for {quote.get('name', ticker)} ({ticker}): "
                        f"Current Price: {cur}{price:.2f}, "
                        f"Day Change: {change:+.2f} ({change_pct:+.2f}%), "
                        f"Volume: {quote.get('volume', '--')}, "
                        f"Market Cap: {quote.get('marketCap', '--')}."
                    )
            except Exception:
                pass
                
        if context_parts:
            return "\n\n[System Context: Current Live Market Data]\n" + "\n".join(context_parts)
        return ""

    def _get_system_prompt(self, market: str = "US") -> str:
        cur = "$" if market == "US" else "₹"
        return (
            f"You are the FinIntelX AI Advisor, a world-class financial research and investment assistant. "
            f"Your goal is to provide highly quantitative, insightful, and clear answers to user's questions about stocks, "
            f"portfolio tracking, and financial analysis. "
            f"The user is viewing the platform with market preference set to {market}. "
            f"Format all monetary values cleanly in {cur} by default unless requested otherwise. "
            f"Always include appropriate financial disclaimers (e.g., 'This information is for research purposes only and not professional financial advice.'). "
            f"Be concise, structured, and use markdown tables or bullet points where appropriate."
        )

    async def chat(self, user_message: str, history: List[Dict[str, str]] = None, market: str = "US") -> str:
        if history is None:
            history = []
            
        market_context = await self._enrich_context_with_market_data(user_message, market)
        full_user_message = user_message + market_context
        
        messages = [{"role": "system", "content": self._get_system_prompt(market)}]
        for msg in history:
            messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": full_user_message})
        
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.7
            )
            return resp.choices[0].message.content
        except Exception as e:
            return f"Error communicating with Gemini: {e}"

    async def chat_stream(self, user_message: str, history: List[Dict[str, str]] = None, market: str = "US") -> AsyncGenerator[str, None]:
        if history is None:
            history = []
            
        market_context = await self._enrich_context_with_market_data(user_message, market)
        full_user_message = user_message + market_context
        
        messages = [{"role": "system", "content": self._get_system_prompt(market)}]
        for msg in history:
            messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": full_user_message})
        
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.7,
                stream=True
            )
            for chunk in resp:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            yield f"Error in stream: {e}"
