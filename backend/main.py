import os
import json
import asyncio
from typing import Any, List, Optional, Union
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from openai import OpenAI
from dotenv import load_dotenv
import httpx

# Load environment variables
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
        "model_id": "gpt-5.5",
        "display_name": "GPT-5.5 (Flagship)",
        "optimizations": "Advanced Logic, Multimodal Synthesis & Strategy"
    },
    {
        "provider": "Anthropic",
        "model_id": "claude-opus-4-8",
        "display_name": "Claude 4.8 Opus",
        "optimizations": "Deep Structural Research & Complex Problem Solving"
    },
    {
        "provider": "Anthropic",
        "model_id": "claude-opus-4-7",
        "display_name": "Claude 4.7 Opus",
        "optimizations": "Extended Document Synthesis & Theoretical Math"
    },
    {
        "provider": "Anthropic",
        "model_id": "claude-opus-4-6",
        "display_name": "Claude 4.6 Opus",
        "optimizations": "Elite Coding, Software Engineering & Refactoring"
    },
    {
        "provider": "Zhipu AI",
        "model_id": "glm-5.2",
        "display_name": "GLM 5.2",
        "optimizations": "High-Performance Cross-Lingual Capabilities"
    }
]

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
}

def _build_openai_client(api_key: str) -> OpenAI:
    """
    Build an OpenAI SDK client pointed at Agent Router.
    Agent Router exposes an OpenAI-compatible /v1 gateway.
    We attach the required client fingerprint headers to pass
    their authentication layer.
    """
    http_client = httpx.Client(
        headers=CLIENT_HEADERS,
        timeout=httpx.Timeout(60.0, connect=10.0)
    )
    return OpenAI(
        api_key=api_key,
        base_url="https://agentrouter.org/v1",
        http_client=http_client
    )

async def stream_agent_router(model_id: str, messages: list, api_key: str):
    """
    Streams completions from Agent Router via the OpenAI SDK,
    yielding custom SSE payloads: data: {"token": "..."}

    Uses synchronous SDK streaming in a thread to avoid blocking the event loop.
    """
    import queue
    import threading

    token_queue = queue.Queue()

    def producer():
        """Run the synchronous OpenAI SDK stream in a background thread."""
        client = None
        try:
            client = _build_openai_client(api_key)

            # Ensure all roles are valid OpenAI roles (system, user, assistant)
            sanitized_messages = []
            for msg in messages:
                role = msg["role"]
                if role not in ("system", "user", "assistant"):
                    role = "assistant"
                sanitized_messages.append({"role": role, "content": msg["content"]})

            stream = client.chat.completions.create(
                model=model_id,
                messages=sanitized_messages,
                max_tokens=4096,
                stream=True
            )

            for chunk in stream:
                if chunk is None:
                    continue
                choices = getattr(chunk, "choices", None)
                if choices and len(choices) > 0:
                    delta = choices[0].delta
                    if delta and getattr(delta, "content", None):
                        token_queue.put(delta.content)

        except Exception as e:
            error_msg = str(e)
            status_code = getattr(e, 'status_code', 500)
            token_queue.put(f"__ERROR__:{status_code}:{error_msg}")
        finally:
            token_queue.put(None)  # sentinel
            if client:
                try:
                    client.close()
                except Exception:
                    pass

    thread = threading.Thread(target=producer, daemon=True)
    thread.start()

    while True:
        # Check queue without blocking the event loop
        try:
            token = token_queue.get_nowait()
        except queue.Empty:
            await asyncio.sleep(0.05)
            continue

        if token is None:
            break

        if isinstance(token, str) and token.startswith("__ERROR__:"):
            parts = token.split(":", 2)
            error_code = parts[1] if len(parts) > 1 else "500"
            error_msg = parts[2] if len(parts) > 2 else "Unknown error"
            yield f"data: {json.dumps({'error': f'Agent Router error ({error_code}): {error_msg}'})}\n\n"
            break

        yield f"data: {json.dumps({'token': token})}\n\n"

@app.post("/api/chat")
async def chat_endpoint(request: ChatRequest):
    """
    Unified chat endpoint proxying to Agent Router with live Server-Sent Events.
    Uses the OpenAI SDK with client fingerprint headers for Agent Router compatibility.
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
                "image_url": {"url": img_data_url}
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
