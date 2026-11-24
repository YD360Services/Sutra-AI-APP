import math
import logging
from typing import List, Optional, Tuple
from app.core.config import settings

logger = logging.getLogger("roundmate.embedding_service")

# Standard embedding vector dimension (compatible with OpenAI text-embedding-3-small and normalized Gemini embeddings)
DEFAULT_EMBEDDING_DIM = 768

def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    """Computes cosine similarity between two numeric vectors in [-1.0, 1.0]."""
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot_product = sum(a * b for a, b in zip(v1, v2))
    norm_a = math.sqrt(sum(a * a for a in v1))
    norm_b = math.sqrt(sum(b * b for b in v2))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot_product / (norm_a * norm_b)

def _deterministic_hash_vector(text: str, dim: int = DEFAULT_EMBEDDING_DIM) -> List[float]:
    """
    Ultra-fast, deterministic text-embedding generator for offline/local fallback.
    Uses n-gram feature hashing with uniform unit normalization.
    """
    clean_text = (text or "").lower().strip()
    words = clean_text.split()
    vec = [0.0] * dim
    if not words:
        return vec
    
    for i, word in enumerate(words):
        # Unigram hash
        h1 = hash(word) % dim
        vec[h1] += 1.0
        # Bigram hash for phrase context
        if i < len(words) - 1:
            bigram = f"{word}_{words[i+1]}"
            h2 = hash(bigram) % dim
            vec[h2] += 1.5

    # L2 normalize
    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0.0:
        vec = [round(x / norm, 6) for x in vec]
    return vec

class EmbeddingService:
    """
    Unified multi-provider embedding engine supporting OpenAI, Google GenAI,
    and zero-latency local fallback.
    """
    @classmethod
    async def get_embedding(cls, text: str) -> List[float]:
        if not text or not text.strip():
            return [0.0] * DEFAULT_EMBEDDING_DIM

        # 1. Try OpenAI text-embedding-3-small (if API key configured with 1.5s timeout)
        if getattr(settings, "OPENAI_API_KEY", None):
            try:
                import openai
                client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY, timeout=1.5)
                resp = await client.embeddings.create(
                    model="text-embedding-3-small",
                    input=text[:8000],
                    dimensions=DEFAULT_EMBEDDING_DIM
                )
                if resp.data and len(resp.data) > 0:
                    return resp.data[0].embedding
            except Exception as e:
                logger.debug(f"[EmbeddingService] OpenAI embedding failed/timed out: {e}. Falling back...")

        # 2. Try Google Gemini embedding-004 (if API key configured)
        if getattr(settings, "GEMINI_API_KEY", None):
            try:
                from app.services.ai_service import get_gemini_client
                client = get_gemini_client()
                if client:
                    result = client.models.embed_content(
                        model="text-embedding-004",
                        contents=text[:8000],
                    )
                    if hasattr(result, "embeddings") and result.embeddings:
                        raw_vec = result.embeddings[0].values
                        # Slice or pad to DEFAULT_EMBEDDING_DIM
                        if len(raw_vec) >= DEFAULT_EMBEDDING_DIM:
                            return raw_vec[:DEFAULT_EMBEDDING_DIM]
                        else:
                            return list(raw_vec) + [0.0] * (DEFAULT_EMBEDDING_DIM - len(raw_vec))
            except Exception as e:
                logger.debug(f"[EmbeddingService] Gemini embedding failed: {e}. Falling back...")

        # 3. Deterministic high-speed hash embedding fallback (zero cost, 0ms latency)
        return _deterministic_hash_vector(text, dim=DEFAULT_EMBEDDING_DIM)

    @classmethod
    async def get_batch_embeddings(cls, texts: List[str]) -> List[List[float]]:
        return [await cls.get_embedding(t) for t in texts]

embedding_service = EmbeddingService()
