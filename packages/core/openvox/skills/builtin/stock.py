"""Skills for the stock-market analysis template."""

from __future__ import annotations

from typing import Any

import httpx

from openvox.skills.base import BaseSkill, SkillContext


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
        url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={sym}"
        async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": "OpenVox/0.1"}) as c:
            r = await c.get(url)
        if r.status_code != 200:
            return {"error": f"upstream {r.status_code}", "ticker": sym}
        data = r.json()
        results = (data.get("quoteResponse") or {}).get("result") or []
        if not results:
            return {"error": "not found", "ticker": sym}
        q = results[0]
        return {
            "ticker": sym,
            "name": q.get("longName") or q.get("shortName"),
            "price": q.get("regularMarketPrice"),
            "currency": q.get("currency"),
            "change": q.get("regularMarketChange"),
            "change_pct": q.get("regularMarketChangePercent"),
            "market_cap": q.get("marketCap"),
            "pe": q.get("trailingPE"),
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
