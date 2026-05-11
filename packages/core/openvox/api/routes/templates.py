"""Pre-built agent templates — the user-facing catalogue."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from openvox.config import get_settings
from openvox.db import db_session
from openvox.db.models import Agent

router = APIRouter()


# ──────────────────────────────────────────────────────────────────────
# Built-in templates. Each is a fully-configured agent the user can
# instantiate with one click. The skills referenced here must exist in
# the built-in skill registry.
# ──────────────────────────────────────────────────────────────────────
TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "ecommerce-support",
        "name": "E-commerce Customer Support",
        "tagline": "Resolve order, return, and stock questions over voice.",
        "category": "Support",
        "icon": "ShoppingBag",
        "use_cases": [
            "Where is my order?",
            "Start a return",
            "Check stock availability",
            "Update shipping address",
        ],
        "default": {
            "name": "Acme Support Voice",
            "system_prompt": (
                "You are a friendly e-commerce support agent for Acme. "
                "Use the lookup_order, start_return, and check_stock tools. "
                "Always confirm the order ID by reading it back. Keep responses under 2 sentences."
            ),
            "greeting": "Hi! I'm Acme's voice assistant — how can I help you today?",
            "skills": ["lookup_order", "start_return", "check_stock", "get_time"],
            "voice_id": "en_male_tim_uranus_bigtts",
        },
    },
    {
        "id": "education-tutor",
        "name": "Science & Math Tutor",
        "tagline": "Explain concepts, walk through worked examples, give homework hints.",
        "category": "Education",
        "icon": "GraduationCap",
        "use_cases": [
            "Explain photosynthesis to a 12-year-old",
            "Help me solve this quadratic",
            "What is the difference between mitosis and meiosis?",
        ],
        "default": {
            "name": "Science Tutor",
            "system_prompt": (
                "You are a patient science and mathematics tutor. Use the calculator and "
                "explain_concept tools. Always check the student's understanding with a "
                "follow-up question. Speak clearly at a measured pace."
            ),
            "greeting": "Hi there — what would you like to learn about today?",
            "skills": ["calculator", "explain_concept", "web_search"],
            "voice_id": "en_male_adam_mars_bigtts",
        },
    },
    {
        "id": "stock-analyst",
        "name": "Stock Market Analyst",
        "tagline": "Live quotes and basic technical analysis. Voice-first.",
        "category": "Finance",
        "icon": "TrendingUp",
        "use_cases": [
            "How is NVDA doing today?",
            "Compare AAPL and MSFT",
            "What's the RSI on TSLA?",
        ],
        "default": {
            "name": "Market Pulse",
            "system_prompt": (
                "You are a markets analyst. Use the get_quote and technical_indicators tools "
                "for any factual ticker question. Always include a short risk disclaimer "
                "('this is not financial advice'). Keep responses under 3 sentences."
            ),
            "greeting": "Markets desk here — which ticker would you like to look at?",
            "skills": ["get_quote", "technical_indicators", "get_time"],
            "voice_id": "en_male_tim_uranus_bigtts",
        },
    },
    {
        "id": "document-qa",
        "name": "Document Q&A Assistant",
        "tagline": "Upload PDFs, slides, or images and ask questions out loud.",
        "category": "Knowledge",
        "icon": "FileText",
        "use_cases": [
            "What does the contract say about renewal terms?",
            "Summarise page 4 of this report",
            "What's shown in the architecture diagram?",
            "Find references to 'GDPR' across all my docs",
        ],
        "default": {
            "name": "Doc Assistant",
            "system_prompt": (
                "You are a careful document-grounded assistant. When the user asks a question, "
                "ALWAYS call the query_documents tool first to retrieve relevant passages. "
                "If a retrieved passage is an image, call analyze_image with its image_url to inspect it. "
                "Ground every claim in the returned passages and cite sources by document name. "
                "If the answer isn't in the documents, say so plainly. Keep voice responses under 3 sentences."
            ),
            "greeting": "Hi! Upload a document on the agent page and ask me anything about it.",
            "skills": ["query_documents", "analyze_image", "get_time"],
        },
    },
    {
        "id": "voice-analyzer",
        "name": "Voice Recording Analyzer",
        "tagline": "Upload or record audio, get sentiment + profanity + summary.",
        "category": "Analytics",
        "icon": "Mic",
        "use_cases": [
            "Quality-assure call-centre recordings",
            "Moderation on user-generated audio",
            "Coach sales calls",
        ],
        "default": {
            "name": "Audio Analyzer",
            "system_prompt": (
                "You are an audio QA assistant. When the user submits a recording, transcribe "
                "it then call sentiment_analyze and profanity_check on the transcript. Return "
                "a one-paragraph summary plus a structured report."
            ),
            "greeting": "Drop a recording or speak now and I'll analyse it.",
            "skills": ["transcribe_recording", "sentiment_analyze", "profanity_check"],
            "voice_id": "en_male_tim_uranus_bigtts",
        },
    },
]


@router.get("")
async def list_templates() -> list[dict[str, Any]]:
    return TEMPLATES


@router.get("/{template_id}")
async def get_template(template_id: str) -> dict[str, Any]:
    for t in TEMPLATES:
        if t["id"] == template_id:
            return t
    raise HTTPException(404, "template not found")


class InstantiateRequest(BaseModel):
    name: str | None = None


@router.post("/{template_id}/instantiate", status_code=201)
async def instantiate_template(template_id: str, body: InstantiateRequest) -> dict[str, Any]:
    tpl = next((t for t in TEMPLATES if t["id"] == template_id), None)
    if tpl is None:
        raise HTTPException(404, "template not found")
    defaults = tpl["default"]
    settings = get_settings()
    async with db_session() as s:
        a = Agent(
            name=body.name or defaults["name"],
            description=tpl["tagline"],
            template_id=template_id,
            system_prompt=defaults["system_prompt"],
            greeting=defaults["greeting"],
            skills=defaults.get("skills", []),
            # Pull voice + model from settings so env-driven defaults
            # actually reach the agent record.
            voice_id=defaults.get("voice_id") or settings.byteplus_tts_default_voice,
            llm_model=settings.byteplus_llm_model,
        )
        s.add(a)
        await s.flush()
        # Mirror the routes/agents.py serialiser shape
        return {
            "id": a.id,
            "name": a.name,
            "description": a.description,
            "template_id": a.template_id,
            "stt_provider": a.stt_provider,
            "tts_provider": a.tts_provider,
            "llm_provider": a.llm_provider,
            "llm_model": a.llm_model,
            "voice_id": a.voice_id,
            "voice_speed": a.voice_speed,
            "voice_language": a.voice_language,
            "system_prompt": a.system_prompt,
            "greeting": a.greeting,
            "temperature": a.temperature,
            "max_tokens": a.max_tokens,
            "skills": a.skills or [],
            "channels": a.channels or {},
            "status": a.status,
            "created_at": a.created_at.isoformat() if a.created_at else "",
            "updated_at": a.updated_at.isoformat() if a.updated_at else "",
        }
