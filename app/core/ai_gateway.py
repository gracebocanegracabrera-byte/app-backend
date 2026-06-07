"""AI Gateway — Cliente OpenRouter centralizado."""
import json, asyncio, logging, re
from openai import AsyncOpenAI, APIStatusError
from app.core.config import settings

logger = logging.getLogger(__name__)

_client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=settings.OPENROUTER_API_KEY,
)

_EXTRA_HEADERS = {
    "HTTP-Referer": settings.OPENROUTER_REFERER,
    "X-OpenRouter-Title": settings.OPENROUTER_TITLE,
}


async def ai_complete(
    model: str,
    messages: list[dict],
    system: str | None = None,
    max_tokens: int = 800,
) -> str:
    full_messages = []
    if system:
        full_messages.append({"role": "system", "content": system})
    full_messages.extend(messages)

    fallback = settings.MODEL_FALLBACK
    model_sequence = [model] if model == fallback else [model, fallback]
    for attempt, target_model in enumerate(model_sequence):
        try:
            response = await _client.chat.completions.create(
                model=target_model,
                messages=full_messages,
                max_tokens=max_tokens,
                extra_headers=_EXTRA_HEADERS,
                timeout=60,
            )
            return response.choices[0].message.content or ""
        except APIStatusError as e:
            if e.status_code in (429, 503, 404) and attempt == 0:
                logger.warning(
                    f"Model {model} unavailable ({e.status_code}), "
                    f"falling back to {settings.MODEL_FALLBACK}"
                )
                await asyncio.sleep(1)
                continue
            raise
    return ""


async def ai_json(
    model: str,
    messages: list[dict],
    system: str | None = None,
    max_tokens: int = 500,
) -> dict:
    for attempt in range(3):
        text = await ai_complete(model, messages, system, max_tokens)
        text = text.strip()
        if text.startswith("```"):
            match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
            if match:
                text = match.group(1).strip()
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            if attempt < 2:
                logger.warning(f"JSON parse failed (attempt {attempt+1}), retrying...")
                await asyncio.sleep(0.5)
    logger.error(f"Could not parse JSON from model {model} after 3 attempts")
    return {}
