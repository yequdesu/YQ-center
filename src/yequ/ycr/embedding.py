"""YCR embedding provider boundary."""

from __future__ import annotations

import httpx

from yequ.config import Settings, get_settings


class EmbeddingError(RuntimeError):
    pass


async def embed_text(
    text: str,
    *,
    settings: Settings | None = None,
) -> tuple[list[float], str, str]:
    settings = settings or get_settings()
    provider = settings.ycr_embedding_provider
    model = settings.ycr_embedding_model
    if provider == "openai_compatible":
        if not settings.ycr_embedding_base_url or not settings.ycr_embedding_api_key:
            raise EmbeddingError("YCR embedding endpoint is not configured")
        async with httpx.AsyncClient(
            base_url=settings.ycr_embedding_base_url,
            timeout=settings.ycr_timeout_sec,
            headers={"Authorization": f"Bearer {settings.ycr_embedding_api_key}"},
        ) as client:
            response = await client.post(
                "/v1/embeddings",
                json={"model": model, "input": text},
            )
            response.raise_for_status()
            payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or not data:
            raise EmbeddingError("Embedding response has no data")
        embedding = data[0].get("embedding") if isinstance(data[0], dict) else None
        if not isinstance(embedding, list):
            raise EmbeddingError("Embedding response has no vector")
        return [float(value) for value in embedding], provider, model
    raise EmbeddingError(f"Unsupported YCR embedding provider: {provider}")
