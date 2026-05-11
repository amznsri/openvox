"""Skills for the Document Q&A template.

The agent calls these tools during a conversation. The retrieval skill
returns raw chunks (not a finished answer) so the LLM can synthesize a
grounded response in its own voice. The image skill bypasses retrieval
and asks the vision-capable LLM directly.
"""

from __future__ import annotations

from typing import Any

from openvox.providers.base import LLMConfig, LLMMessage, ProviderType
from openvox.providers.registry import get_registry
from openvox.rag import query as rag_query
from openvox.rag.byteplus_cloud import get_rag_client
from openvox.skills.base import BaseSkill, SkillContext


class QueryDocuments(BaseSkill):
    id = "query_documents"
    display_name = "Search uploaded documents"
    description = (
        "Retrieve passages from documents the user has uploaded to this agent. "
        "Use this whenever the user asks about content of their files. Always "
        "ground your answer in the returned passages and cite the source name."
    )
    parameters = {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "The user's question or search topic"},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
        },
        "required": ["question"],
    }

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        question = (args.get("question") or "").strip()
        top_k = int(args.get("top_k") or 5)
        agent_id = ctx.agent_id
        if not agent_id:
            return {"error": "no agent context"}
        if not question:
            return {"error": "empty question"}

        # Prefer BytePlus RAG Cloud if AK/SK + collection are configured;
        # otherwise fall back to the local in-DB vector store.
        cloud = get_rag_client()
        if cloud is not None:
            try:
                resp = await cloud.chat(question, top_k=top_k)
                # The collection_service_chat response includes both a final
                # answer and the retrieved docs. We surface both — the agent's
                # LLM can choose to repeat the answer verbatim or rephrase it.
                data = resp.get("data") or resp
                return {
                    "source": "byteplus_rag_cloud",
                    "answer": data.get("generated_answer") or data.get("answer") or "",
                    "passages": [
                        {
                            "source": p.get("doc_name") or p.get("source") or "",
                            "page": p.get("page") or 0,
                            "kind": "text",
                            "score": float(p.get("score") or p.get("rerank_score") or 0.0),
                            "text": p.get("content") or p.get("text") or "",
                            "image_url": None,
                        }
                        for p in (data.get("result_list") or data.get("passages") or [])
                    ],
                }
            except Exception as e:
                # Don't crash the agent — log and fall through to local.
                import logging
                logging.getLogger(__name__).warning(
                    "RAG Cloud chat failed, falling back to local: %s", e
                )

        results = await rag_query(agent_id=agent_id, question=question, top_k=top_k)
        return {
            "source": "local",
            "passages": [
                {
                    "source": r.document_name,
                    "page": r.page,
                    "kind": r.kind,
                    "score": round(r.score, 3),
                    # For images we return the data URL — the LLM can't
                    # render it from a tool message, so we hint the agent
                    # to call analyze_image with this URL.
                    "text": r.text if r.kind == "text" else "(image attachment — call analyze_image)",
                    "image_url": r.text if r.kind == "image" else None,
                }
                for r in results
            ],
        }


class AnalyzeImage(BaseSkill):
    id = "analyze_image"
    display_name = "Analyze an image"
    description = (
        "Inspect an image and answer a question about it. The image_url can be "
        "an http(s) URL or a base64 data URI returned by query_documents."
    )
    parameters = {
        "type": "object",
        "properties": {
            "image_url": {"type": "string"},
            "question": {"type": "string"},
        },
        "required": ["image_url", "question"],
    }

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        url = (args.get("image_url") or "").strip()
        q = (args.get("question") or "Describe this image in detail.").strip()
        if not url:
            return {"error": "image_url required"}

        # Use whichever LLM is the agent's default — for vision we need
        # a multimodal model. BytePlus Seed-2.0 supports vision out of
        # the box (the same chat-completions endpoint accepts an
        # image_url content block).
        reg = get_registry()
        llm = reg.get(ProviderType.LLM, "byteplus") or reg.get(ProviderType.LLM, "openai")
        if llm is None or not llm.is_available():
            return {"error": "no vision-capable LLM configured"}

        messages = [
            LLMMessage(role="system", content="You are a careful image analyst. Be concise."),
            # Multimodal content block — OpenAI-compatible Ark accepts
            # `content` as a list of typed parts.
            LLMMessage(
                role="user",
                content=[  # type: ignore[arg-type]
                    {"type": "text", "text": q},
                    {"type": "image_url", "image_url": {"url": url}},
                ],
            ),
        ]
        text = await llm.chat(
            messages,
            LLMConfig(model="", temperature=0.2, max_tokens=600, stream=False),
        )
        return {"answer": text.strip()}


SKILLS = [QueryDocuments, AnalyzeImage]
