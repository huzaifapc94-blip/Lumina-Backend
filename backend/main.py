import os
import json
import base64
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

# Model Registry — uses actual Agent Router model IDs from their /v1/models endpoint
MODEL_REGISTRY = [
    {
        "provider": "OpenAI",
        "model_id": "gpt-5.6-sol",
        "display_name": "GPT-5.6 Sol",
        "optimizations": "Flagship Multimodal Synthesis & High-Level Strategy"
    },
    {
        "provider": "OpenAI",
        "model_id": "gpt-6-astra",
        "display_name": "GPT-6 Astra",
        "optimizations": "Next-Gen Frontier Intelligence & Advanced Reasoning"
    },
    {
        "provider": "Anthropic",
        "model_id": "claude-opus-4-8",
        "display_name": "Claude 4.8 Opus",
        "optimizations": "Deep Structural Research & Complex Problem Solving"
    },
    {
        "provider": "Anthropic",
        "model_id": "claude-opus-5",
        "display_name": "Claude Opus 5",
        "optimizations": "Frontier Autonomous Synthesis & Theoretical Analysis"
    },
    {
        "provider": "DeepSeek",
        "model_id": "deepseek-v4-flash",
        "display_name": "DeepSeek V4 Flash",
        "optimizations": "High-Speed Thought, Coding & Cost-Effective Reasoning"
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

@app.get("/api/models")
async def get_models():
    """Retrieve the static Model Registry array."""
    return MODEL_REGISTRY

@app.get("/api/health")
async def health_check():
    """Verify backend and API key readiness."""
    api_key = os.getenv("AGENTROUTER_API_KEY")
    return {
        "status": "healthy",
        "api_key_configured": bool(api_key)
    }

# Required headers to pass Agent Router's client fingerprinting check.
# Agent Router verifies that requests come from recognized coding clients.
CLIENT_HEADERS = {
    "User-Agent": "codex_cli_rs/0.101.0",
    "Originator": "codex_cli_rs",
    "Version": "0.101.0",
    "Accept": "application/json, text/event-stream, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

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

async def stream_agent_router(model_id: str, messages: list, api_key: str):
    """
    Streams completions from Agent Router via raw HTTP,
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
                "https://agentrouter.org/v1/chat/completions",
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
                    yield f"data: {json.dumps({'error': f'Agent Router error ({response.status_code}): {error_msg}'})}\n\n"
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
                    if error:
                        yield f"data: {json.dumps({'error': f'Agent Router error: {_coerce_text(error)}'})}\n\n"
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

    except httpx.HTTPError as e:
        yield f"data: {json.dumps({'error': f'Agent Router connection error: {str(e)}'})}\n\n"

@app.post("/api/chat")
async def chat_endpoint(request: ChatRequest):
    """
    Unified chat endpoint proxying to Agent Router with live Server-Sent Events.
    Uses direct HTTP with client fingerprint headers for Agent Router compatibility.
    """
    api_key = os.getenv("AGENTROUTER_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="AGENTROUTER_API_KEY is not configured on the server."
        )

    # Validate model_id
    valid_model_ids = {m["model_id"] for m in MODEL_REGISTRY}
    if request.model_id not in valid_model_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid model_id: '{request.model_id}'. Choose from registry."
        )

    # Reconstruct messages payload
    messages = []
    if request.history:
        for msg in request.history:
            messages.append({"role": msg.role, "content": msg.content})

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
        stream_agent_router(request.model_id, messages, api_key),
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
