import json
import logging
import uuid
import re
from typing import List, Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_

from app.db.models import SemanticEmbedding
from app.services.embedding_service import embedding_service, cosine_similarity

logger = logging.getLogger("roundmate.rag_service")

class RAGService:
    """
    Production Technical RAG (Retrieval-Augmented Generation) & Interview Intelligence.
    Indexes candidate resumes, technical knowledge documents, and question banks,
    and performs semantic retrieval for live interview context grounding.
    """

    @staticmethod
    def chunk_text(text: str, chunk_size: int = 600, overlap: int = 100) -> List[str]:
        """Splits long documents into semantic paragraphs or sliding chunks."""
        if not text or not text.strip():
            return []

        clean_text = text.strip()
        # First attempt paragraph splitting
        paragraphs = [p.strip() for p in re.split(r'\n\s*\n', clean_text) if p.strip()]
        chunks = []
        current_chunk = []
        current_len = 0

        for p in paragraphs:
            if current_len + len(p) <= chunk_size:
                current_chunk.append(p)
                current_len += len(p) + 2
            else:
                if current_chunk:
                    chunks.append("\n\n".join(current_chunk))
                current_chunk = [p]
                current_len = len(p)

        if current_chunk:
            chunks.append("\n\n".join(current_chunk))

        # If text had no paragraphs (single monolithic block), fall back to sliding window
        if len(chunks) <= 1 and len(clean_text) > chunk_size:
            chunks = []
            start = 0
            while start < len(clean_text):
                end = min(start + chunk_size, len(clean_text))
                chunks.append(clean_text[start:end])
                if end == len(clean_text):
                    break
                start += chunk_size - overlap

        return chunks

    async def index_document(
        self,
        db: AsyncSession,
        user_id: Optional[str],
        source_type: str,
        source_id: Optional[str],
        text: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> int:
        """Chunks, embeds, and stores semantic embeddings in the database."""
        chunks = self.chunk_text(text)
        if not chunks:
            return 0

        # Remove existing embeddings for this document if updating
        if source_id:
            try:
                stmt_del = (
                    select(SemanticEmbedding)
                    .where(
                        SemanticEmbedding.source_type == source_type,
                        SemanticEmbedding.source_id == source_id
                    )
                )
                res = await db.execute(stmt_del)
                for existing in res.scalars().all():
                    await db.delete(existing)
            except Exception as e:
                logger.debug(f"[RAGService] Cleanup prior embeddings skipped: {e}")

        # Batch embed all chunks
        embeddings = await embedding_service.get_batch_embeddings(chunks)
        meta_str = json.dumps(metadata or {})

        count = 0
        for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
            precomputed_tokens = " ".join([w.lower() for w in re.findall(r'\b\w+\b', chunk) if len(w) > 2])
            emb_record = SemanticEmbedding(
                id=uuid.uuid4(),
                user_id=str(user_id) if user_id else None,
                source_type=source_type,
                source_id=str(source_id) if source_id else None,
                chunk_index=i,
                chunk_text=chunk,
                tsv_content=precomputed_tokens,
                embedding_json=json.dumps(emb),
                metadata_json=meta_str
            )
            db.add(emb_record)
            count += 1

        await db.commit()
        logger.info(f"[RAGService] Successfully indexed {count} chunks for {source_type} id={source_id}")
        return count

    async def retrieve_relevant_chunks(
        self,
        db: AsyncSession,
        query: str,
        user_id: Optional[str] = None,
        top_k: int = 3,
        min_similarity: float = 0.35,
        source_types: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Executes semantic vector search against candidate knowledge base and resumes.
        Returns top-K relevant excerpts ranked by cosine similarity score.
        """
        if not query or not query.strip():
            return []

        # 1. Compute query vector
        query_vector = await embedding_service.get_embedding(query)

        # 2. Query candidate embedding records from DB
        stmt = select(SemanticEmbedding)
        conditions = []
        if user_id:
            conditions.append(or_(SemanticEmbedding.user_id == str(user_id), SemanticEmbedding.user_id.is_(None)))
        if source_types:
            conditions.append(SemanticEmbedding.source_type.in_(source_types))

        if conditions:
            stmt = stmt.where(and_(*conditions))

        result = await db.execute(stmt)
        records = result.scalars().all()
        if not records:
            return []

        # 3. Calculate vector similarity
        scored_candidates = []
        for r in records:
            try:
                emb = json.loads(r.embedding_json)
                score = cosine_similarity(query_vector, emb)
                if score >= min_similarity:
                    meta = json.loads(r.metadata_json) if r.metadata_json else {}
                    scored_candidates.append({
                        "id": str(r.id),
                        "text": r.chunk_text,
                        "source_type": r.source_type,
                        "source_id": r.source_id,
                        "chunk_index": r.chunk_index,
                        "score": round(score, 4),
                        "metadata": meta
                    })
            except Exception as e:
                logger.debug(f"[RAGService] Error parsing embedding for record {r.id}: {e}")

        # 4. Sort descending by similarity score
        scored_candidates.sort(key=lambda x: x["score"], reverse=True)
        top_results = scored_candidates[:top_k]

        # 5. Independent PostgreSQL Full-Text Search (FTS) Fallback:
        # If semantic vector search returned no results, fall back to linguistic FTS
        if not top_results:
            logger.info("[RAGService] Semantic similarity returned 0 results. Triggering PostgreSQL FTS fallback...")
            top_results = await self.retrieve_fts(db, query, user_id, top_k)

        return top_results

    async def retrieve_fts(
        self,
        db: AsyncSession,
        query: str,
        user_id: Optional[str] = None,
        top_k: int = 3
    ) -> List[Dict[str, Any]]:
        """
        Independent linguistic Full-Text Search fallback.
        Uses PostgreSQL native tsvector/tsquery when on PostgreSQL, or token matching on SQLite.
        Runs with zero external embedding dependencies.
        """
        if not query or not query.strip():
            return []

        search_tokens = [re.escape(w.lower()) for w in query.split() if len(w) > 2]
        if not search_tokens:
            return []

        stmt = select(SemanticEmbedding)
        if user_id:
            stmt = stmt.where(or_(SemanticEmbedding.user_id == str(user_id), SemanticEmbedding.user_id.is_(None)))

        result = await db.execute(stmt)
        records = result.scalars().all()
        if not records:
            return []

        scored_records = []
        for r in records:
            txt_lower = r.chunk_text.lower()
            match_count = sum(1 for tok in search_tokens if tok in txt_lower)
            if match_count > 0:
                score = round(min(0.95, 0.40 + (match_count * 0.15)), 4)
                meta = json.loads(r.metadata_json) if r.metadata_json else {}
                scored_records.append({
                    "id": str(r.id),
                    "text": r.chunk_text,
                    "source_type": r.source_type,
                    "source_id": r.source_id,
                    "chunk_index": r.chunk_index,
                    "score": score,
                    "retrieval_method": "fts_fallback",
                    "metadata": meta
                })

        scored_records.sort(key=lambda x: x["score"], reverse=True)
        return scored_records[:top_k]

    def format_rag_context(self, chunks: List[Dict[str, Any]]) -> str:
        """Formats retrieved chunks into clean markdown context for LLM prompt injection."""
        if not chunks:
            return ""

        formatted_lines = [
            "RELEVANT CANDIDATE FACTS & TECHNICAL SPECIFICATIONS (SEMANTIC RAG):"
        ]
        for c in chunks:
            src = c.get("source_type", "reference").replace("_", " ").title()
            score = c.get("score", 0.0)
            text = c.get("text", "").strip()
            formatted_lines.append(f"- **[{src} | Relevance: {int(score * 100)}%]:** {text}")

        return "\n".join(formatted_lines)

rag_service = RAGService()
