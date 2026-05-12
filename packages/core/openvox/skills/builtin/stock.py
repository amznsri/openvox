"""Skills for the stock-market analysis template."""

from __future__ import annotations

from typing import Any

from openvox.skills.base import BaseSkill, SkillContext
from openvox.utils.http import make_async_client


class GetQuote(BaseSkill):
    id = "get_quote"
    display_name = "Get stock quote"
    description = "Return the latest price for a ticker. Uses Yahoo Finance public quote API."
    parameters = {
        "type": "object",
        "properties": {
            "ticker": {"type": "string", "description": "e.g. AAPL, GOOG, NVDA"},
        },
        "required": ["ticker"],
    }

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        sym = (args.get("ticker") or "").strip().upper()
        if not sym:
            return {"error": "missing ticker"}
        # Yahoo's v7/quote endpoint started requiring a crumb cookie in 2024.
        # The v8/chart endpoint still works unauthenticated and gives us the
        # `meta` block with price + change data, which is all we need here.
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=5d"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
            ),
        }
        async with make_async_client(timeout=10.0, headers=headers) as c:
            r = await c.get(url)
        if r.status_code != 200:
            return {"error": f"upstream {r.status_code}", "ticker": sym}
        data = r.json()
        results = (data.get("chart") or {}).get("result") or []
        if not results:
            err = ((data.get("chart") or {}).get("error") or {}).get("description")
            return {"error": err or "not found", "ticker": sym}
        meta = results[0].get("meta") or {}
        price = meta.get("regularMarketPrice")
        prev = meta.get("chartPreviousClose") or meta.get("previousClose")
        change = (price - prev) if (isinstance(price, (int, float)) and isinstance(prev, (int, float))) else None
        change_pct = (change / prev * 100.0) if (change is not None and prev) else None
        return {
            "ticker": sym,
            "name": meta.get("longName") or meta.get("shortName") or sym,
            "price": price,
            "currency": meta.get("currency"),
            "previous_close": prev,
            "change": change,
            "change_pct": change_pct,
            "exchange": meta.get("exchangeName"),
            "market_state": meta.get("marketState"),
        }


class TechnicalIndicators(BaseSkill):
    id = "technical_indicators"
    display_name = "Technical indicators"
    description = "Compute simple moving averages and RSI for a ticker."
    parameters = {
        "type": "object",
        "properties": {
            "ticker": {"type": "string"},
            "period": {"type": "string", "enum": ["1mo", "3mo", "6mo", "1y"], "default": "3mo"},
        },
        "required": ["ticker"],
    }

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        sym = (args.get("ticker") or "").strip().upper()
        period = args.get("period") or "3mo"
        # Stub — real implementation would fetch chart data.
        return {
            "ticker": sym,
            "period": period,
            "sma_20": None,
            "sma_50": None,
            "rsi_14": None,
            "note": "Stub. Wire to a chart-history provider (Yahoo, Alpaca, Polygon).",
        }


SKILLS = [GetQuote, TechnicalIndicators]
