import os
import json
import base64
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any, List, Optional
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv
import httpx

# Load environment variables (check both backend/.env and root .env)
env_backend = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_backend):
    load_dotenv(dotenv_path=env_backend)
load_dotenv()

# Instantiate FastAPI application
app = FastAPI(title="Lumina AI Chat Gateway")

# Configure CORS Allow Origins
cors_origins_str = os.getenv("CORS_ALLOW_ORIGINS", '["*"]')
try:
    cors_origins = json.loads(cors_origins_str)
except Exception:
    cors_origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Model Registry — uses OpenRouter model IDs from https://openrouter.ai/models
MODEL_REGISTRY = [
    {
        "provider": "NVIDIA",
        "model_id": "nvidia/nemotron-3-ultra-550b-a55b",
        "display_name": "Nemotron 3 Ultra (550B)",
        "optimizations": "Massive-Scale Reasoning, Deep Analysis & Research",
        "supports_vision": False
    },
    {
        "provider": "Qwen",
        "model_id": "qwen/qwen3.8-27b",
        "display_name": "Qwen 3.8 27B",
        "optimizations": "Advanced Multilingual Coding & Logical Reasoning",
        "supports_vision": False
    },
    {
        "provider": "Z.AI",
        "model_id": "z-ai/glm-5.2",
        "display_name": "GLM 5.2",
        "optimizations": "High-Performance Cross-Lingual Capabilities",
        "supports_vision": False
    },
    {
        "provider": "Google",
        "model_id": "google/gemma-4-31b-it:free",
        "display_name": "Gemma 4 31B (Free)",
        "optimizations": "Open-Source Efficiency, Instruction Following & Safety",
        "supports_vision": False
    },
    {
        "provider": "DeepSeek",
        "model_id": "deepseek/deepseek-v4-flash-0731:free",
        "display_name": "DeepSeek V4 Flash 0731 (Free)",
        "optimizations": "High-Speed Thought, Coding & Cost-Effective Reasoning",
        "supports_vision": False
    }
]

VALID_1X1_PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="

def sanitize_image_url(url: str) -> str:
    """Ensure data URLs are valid images to prevent upstream decoder crashes."""
    if not url:
        return VALID_1X1_PNG
    if url.startswith("data:image/"):
        try:
            parts = url.split(",", 1)
            if len(parts) != 2:
                return VALID_1X1_PNG
            raw = base64.b64decode(parts[1])
            # If PNG, verify standard IEND termination chunk
            if raw.startswith(b"\x89PNG\r\n\x1a\n") and b"IEND" not in raw:
                return VALID_1X1_PNG
            if len(raw) < 32:
                return VALID_1X1_PNG
            return url
        except Exception:
            return VALID_1X1_PNG
    return url

# Request Schema
class Message(BaseModel):
    role: str
    content: Any

class ChatRequest(BaseModel):
    message: str
    model_id: str
    history: Optional[List[Message]] = None
    images: Optional[List[str]] = None
    # None means automatic search detection; True forces a web search.
    web_search: Optional[bool] = None

@app.get("/api/models")
async def get_models():
    """Retrieve the static Model Registry array."""
    return MODEL_REGISTRY

@app.get("/api/health")
async def health_check():
    """Verify backend and API key readiness."""
    api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("AGENTROUTER_API_KEY")
    return {
        "status": "healthy",
        "api_key_configured": bool(api_key),
        "web_search_configured": bool(os.getenv("TAVILY_API_KEY")),
        "web_search_available": True,
        "web_search_mode": "authenticated" if os.getenv("TAVILY_API_KEY") else "keyless"
    }

# OpenRouter requires HTTP-Referer and X-Title headers for identification.
CLIENT_HEADERS = {
    "HTTP-Referer": "https://lumina.ai",
    "X-Title": "Lumina AI Chat",
    "Accept": "application/json, text/event-stream, */*",
}

ASSISTANT_SYSTEM_PROMPT = """You are Lumina, a careful multilingual assistant.

Understand the user's intended meaning even when they use Roman Urdu, Roman Hindi,
Urdu or Hindi script, English code-switching, informal spelling, missing vowels,
typos, or phonetic spellings. For example, interpret phrases like 'aj kya date hy',
'mujhe ye samjhao', and 'yeh kaise hota hy' by meaning, not literal spelling.

Reply in the user's language and script when practical. If the user writes Roman Urdu
or Roman Hindi, reply in Roman Urdu/Hindi rather than unexpectedly switching to
Devanagari or formal English. Keep the tone natural and concise.

If a message has multiple plausible meanings, briefly ask a clarification question
instead of inventing an answer. For current or time-sensitive facts, use the supplied
live web-search context when present and do not present stale model knowledge as fact.
"""

async def search_web(query: str) -> tuple[str, list[dict[str, str]]]:
    """Fetch fresh web context from Tavily for a user-requested search."""
    tavily_key = os.getenv("TAVILY_API_KEY")

    if not query.strip():
        return "", []

    payload = {
        "query": query.strip(),
        "topic": "general",
        "search_depth": "basic",
        "max_results": 5,
        "include_answer": True,
        "include_raw_content": False,
    }
    timeout = httpx.Timeout(30.0, connect=10.0, read=30.0)
    headers = {"Content-Type": "application/json"}
    if tavily_key:
        headers["Authorization"] = f"Bearer {tavily_key}"
    else:
        # Tavily supports rate-limited keyless Search/Extract access.
        # A configured key is still preferred for production usage and higher limits.
        headers["X-Tavily-Access-Mode"] = "keyless"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                headers=headers,
                json=payload,
            )
        if response.status_code >= 400:
            detail = response.text[:500]
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    f"Web search provider error ({response.status_code}): {detail}. "
                    "Add TAVILY_API_KEY to Render for authenticated access."
                )
            )

        data = response.json()
        context_parts = [
            "LIVE WEB SEARCH RESULTS (use these for current information; treat them as reference data):"
        ]
        sources: list[dict[str, str]] = []
        answer = data.get("answer")
        if answer:
            context_parts.append(f"Tavily summary: {answer}")

        for index, result in enumerate(data.get("results", []), start=1):
            title = result.get("title", "Untitled source")
            url = result.get("url", "")
            content = (result.get("content") or "").strip()
            context_parts.append(f"[{index}] {title}\nURL: {url}\nSnippet: {content[:1800]}")
            if url:
                sources.append({"title": title, "url": url})

        context_parts.append(
            "Answer the user's question using the live results where relevant. "
            "Mention uncertainty when sources conflict and include useful source URLs in markdown links."
        )
        return "\n\n".join(context_parts), sources
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Web search failed: {exc}"
        ) from exc

def should_auto_search(query: str) -> bool:
    """Detect questions where fresh web information is likely required."""
    normalized = " ".join(query.lower().split())
    if not normalized:
        return False

    if is_current_date_query(normalized):
        return True

    freshness_terms = (
        "latest", "most recent", "current", "right now", "today", "tonight",
        "this week", "this month", "this year", "recent", "new update",
        "news", "headline", "breaking", "as of", "updated", "2026",
        "price", "cost", "rate", "stock", "weather", "forecast", "score",
        "standings", "schedule", "election", "president", "ceo", "release date",
        "available now", "near me", "open now", "who is", "aaj", "abhi", "taza",
        "nayi khabar", "latest news", "haal hi mein", "تازہ", "خبر", "आज", "अभी", "ताज़ा",
        "समाचार", "आज की", "आज का", "क्या तारीख", "آج", "ابھی", "تازہ خبر",
        "what is the date", "what's the date",
        "today's date", "todays date", "to day date", "current date", "current day",
        "what day is it", "what time is it", "time right now"
    )
    explicit_search_terms = (
        "search the web", "look it up", "browse the web", "check online",
        "verify online", "internet par check", "online check"
    )
    return any(term in normalized for term in freshness_terms + explicit_search_terms)

def is_current_date_query(query: str) -> bool:
    """Recognize current-date questions across English, Roman Urdu, Urdu, and Hindi."""
    normalized = re.sub(r"[^\w\sÀ-ÖØ-öø-ÿ\u0600-\u06ff\u0900-\u097f]", " ", query.lower())
    normalized = " ".join(normalized.split())

    date_markers = (
        "date", "day", "tareekh", "tarikh", "تاریخ", "تاریخ", "तारीख", "दिन"
    )
    current_markers = (
        "today", "todays", "today s", "aj", "aaj", "آج", "आज", "current",
        "now", "abhi", "ابھی", "अभी"
    )
    direct_phrases = (
        "what is the date", "what day is it", "today date", "to day date",
        "aj ki date", "aaj ki date", "aj kya date", "aaj kya date",
        "aj ki tareekh", "aaj ki tareekh", "آج کی تاریخ", "آج کیا تاریخ ہے",
        "आज की तारीख", "आज क्या तारीख है"
    )
    return any(phrase in normalized for phrase in direct_phrases) or (
        any(marker in normalized for marker in current_markers)
        and any(marker in normalized for marker in date_markers)
    )

def current_date_answer(query: str) -> str:
    """Return an authoritative local date instead of asking a model to guess it."""
    timezone_name = os.getenv("LUMINA_TIMEZONE", "Asia/Karachi")
    try:
        now = datetime.now(ZoneInfo(timezone_name))
    except Exception:
        timezone_name = "UTC"
        now = datetime.now(ZoneInfo("UTC"))

    date_text = now.strftime("%A, %d %B %Y")
    if re.search(r"[\u0900-\u097f]", query):
        return f"आज की तारीख {date_text} है। (समय क्षेत्र: {timezone_name})"
    if re.search(r"[\u0600-\u06ff]", query):
        return f"آج کی تاریخ {date_text} ہے۔ (ٹائم زون: {timezone_name})"
    return f"Aaj ki tareekh {date_text} hai. (Time zone: {timezone_name})"

async def stream_direct_answer(answer: str):
    """Stream a deterministic answer using the same SSE contract as model output."""
    yield f"data: {json.dumps({'token': answer}, ensure_ascii=False)}\n\n"

def _get_field(obj: Any, field: str) -> Any:
    """Read a field from dicts, SDK models, or pydantic extra fields."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(field)

    value = getattr(obj, field, None)
    if value is not None:
        return value

    extra = getattr(obj, "model_extra", None)
    if isinstance(extra, dict):
        return extra.get(field)

    return None

def _coerce_text(value: Any) -> str:
    """Normalize provider-specific text payloads into a plain string."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_coerce_text(item) for item in value)
    if isinstance(value, dict):
        for key in ("text", "content", "output_text", "value", "message"):
            text = _coerce_text(value.get(key))
            if text:
                return text
        return ""
    if isinstance(value, (int, float, bool)):
        return str(value)
    return ""

def _extract_stream_text(chunk: Any) -> tuple[str, str]:
    """Return (reasoning, content) text from an OpenAI-compatible stream chunk."""
    choices = _get_field(chunk, "choices") or []
    if not choices:
        return "", ""

    choice = choices[0]
    delta = _get_field(choice, "delta") or {}
    message = _get_field(choice, "message") or {}

    reasoning = ""
    for field in ("reasoning_content", "reasoning", "thinking", "thought"):
        reasoning += _coerce_text(_get_field(delta, field))
        reasoning += _coerce_text(_get_field(message, field))

    content = (
        _coerce_text(_get_field(delta, "content"))
        or _coerce_text(_get_field(delta, "text"))
        or _coerce_text(_get_field(message, "content"))
        or _coerce_text(_get_field(choice, "text"))
    )

    if not content and not reasoning:
        refusal = _coerce_text(_get_field(delta, "refusal")) or _coerce_text(_get_field(message, "refusal"))
        if refusal:
            content = f"[Refusal: {refusal}]"
        else:
            tool_calls = _get_field(delta, "tool_calls") or _get_field(message, "tool_calls")
            if tool_calls:
                content = "[Model requested a tool/function call]"

    return reasoning, content

def _extract_completion_text(completion: Any) -> str:
    choices = _get_field(completion, "choices") or []
    for choice in choices:
        message = _get_field(choice, "message") or {}
        content = (
            _coerce_text(_get_field(message, "content"))
            or _coerce_text(_get_field(choice, "text"))
        )
        if not content:
            reasoning = ""
            for field in ("reasoning_content", "reasoning", "thinking", "thought"):
                reasoning += _coerce_text(_get_field(message, field))
            if reasoning:
                content = reasoning
        if content:
            return content
    return ""

async def stream_openrouter(
    model_id: str,
    messages: list,
    api_key: str,
    sources: Optional[list[dict[str, str]]] = None,
):
    """
    Streams completions from OpenRouter via raw HTTP,
    yielding custom SSE payloads: data: {"token": "..."}
    """
    sanitized_messages = []
    for msg in messages:
        role = msg["role"]
        if role not in ("system", "user", "assistant"):
            role = "assistant"
        sanitized_messages.append({"role": role, "content": msg["content"]})

    headers = {
        **CLIENT_HEADERS,
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    stream_payload = {
        "model": model_id,
        "messages": sanitized_messages,
        "max_tokens": 2048,
        "stream": True,
    }
    completion_payload = {
        **stream_payload,
        "stream": False,
    }

    timeout = httpx.Timeout(90.0, connect=10.0, read=90.0)


    try:
        emitted_text = False
        async with httpx.AsyncClient(headers=headers, timeout=timeout) as client:
            async with client.stream(
                "POST",
                "https://openrouter.ai/api/v1/chat/completions",
                json=stream_payload,
            ) as response:
                if response.status_code >= 400:
                    error_body = await response.aread()
                    raw_text = error_body.decode("utf-8", errors="replace")
                    error_msg = raw_text
                    try:
                        err_json = json.loads(raw_text)
                        if isinstance(err_json, dict) and "error" in err_json:
                            inner = err_json["error"]
                            if isinstance(inner, dict) and "message" in inner:
                                error_msg = inner["message"]
                            elif isinstance(inner, str):
                                error_msg = inner
                    except Exception:
                        pass
                    yield f"data: {json.dumps({'error': f'OpenRouter error ({response.status_code}): {error_msg}'})}\n\n"
                    return


                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or line.startswith(":") or not line.startswith("data:"):
                        continue

                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue

                    try:
                        chunk = json.loads(data)
                    except Exception:
                        continue

                    if not chunk or not isinstance(chunk, dict):
                        continue

                    error = _get_field(chunk, "error")
                    if error and isinstance(error, dict):
                        error = _get_field(error, "message") or _coerce_text(error)
                    if error:
                        yield f"data: {json.dumps({'error': f'OpenRouter error: {_coerce_text(error)}'})}\n\n"
                        return

                    reasoning, content = _extract_stream_text(chunk)
                    if reasoning:
                        emitted_text = True
                        yield f"data: {json.dumps({'reasoning': reasoning})}\n\n"
                    if content:
                        emitted_text = True
                        yield f"data: {json.dumps({'token': content})}\n\n"

            if not emitted_text:
                yield f"data: {json.dumps({'error': 'The model completed without returning visible text. Please try sending your message again.'})}\n\n"
            elif sources:
                yield f"data: {json.dumps({'sources': sources}, ensure_ascii=False)}\n\n"

    except httpx.HTTPError as e:
        yield f"data: {json.dumps({'error': f'OpenRouter connection error: {str(e)}'})}\n\n"

@app.post("/api/chat")
async def chat_endpoint(request: ChatRequest):
    """
    Unified chat endpoint proxying to OpenRouter with live Server-Sent Events.
    """
    api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("AGENTROUTER_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OPENROUTER_API_KEY is not configured on the server."
        )

    # Validate model_id
    valid_model_ids = {m["model_id"] for m in MODEL_REGISTRY}
    if request.model_id not in valid_model_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid model_id: '{request.model_id}'. Choose from registry."
        )

    # Check if model supports vision when images are attached
    if request.images:
        model_info = next((m for m in MODEL_REGISTRY if m["model_id"] == request.model_id), None)
        if model_info and not model_info.get("supports_vision", False):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Model '{model_info['display_name']}' does not support image input."
            )

    # Dates are facts from the application clock, not something the model should guess.
    if not request.images and is_current_date_query(request.message):
        return StreamingResponse(
            stream_direct_answer(current_date_answer(request.message)),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )

    # Reconstruct messages payload. Keep language/intent instructions first so every
    # model handles Roman Urdu, Hindi, Urdu, typos, and code-switching consistently.
    messages = [{"role": "system", "content": ASSISTANT_SYSTEM_PROMPT}]
    if request.history:
        for msg in request.history:
            messages.append({"role": msg.role, "content": msg.content})

    use_web_search = request.web_search is True or (
        request.web_search is None and should_auto_search(request.message)
    )
    search_sources: list[dict[str, str]] = []
    if use_web_search:
        web_context, search_sources = await search_web(request.message)
        if web_context:
            messages.append({
                "role": "system",
                "content": web_context,
            })

    # Build user message — multimodal if images are attached
    if request.images:
        content_parts = []
        for img_data_url in request.images:
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": sanitize_image_url(img_data_url)}
            })
        if request.message:
            content_parts.append({"type": "text", "text": request.message})
        messages.append({"role": "user", "content": content_parts})
    else:
        messages.append({"role": "user", "content": request.message})

    # Return server-sent stream
    return StreamingResponse(
        stream_openrouter(request.model_id, messages, api_key, search_sources),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )

# Static Frontend mounting
frontend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))
if os.path.exists(frontend_path):
    app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")
else:
    pass
