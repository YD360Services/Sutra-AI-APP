import re
import json
import uuid
import logging
from typing import List, Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.models import JobDescription, Resume, KnowledgeDocument, QuestionAnswer
from app.db.repositories import ResumeRepository, JDRepository, KnowledgeRepository, QARepository
from app.cache.redis import redis_cache
from app.services.candidate_memory import CandidateMemoryStore, CandidateMemoryItem, candidate_memory_store

logger = logging.getLogger("roundmate.context_orchestrator")

def rank_and_truncate_text(text: str, keywords: List[str], max_items: int) -> List[str]:
    if not text or text == "None loaded.":
        return []
    # Split by newlines, bullet symbols, or standalone dashes (not inside words)
    text_clean = re.sub(r'^\s*[\-\*•]\s*', '\n', text, flags=re.MULTILINE)
    text_clean = re.sub(r'\s*[\*•]\s*', '\n', text_clean)
    text_clean = re.sub(r'\s+-\s+', '\n', text_clean)
    raw_lines = text_clean.split('\n')
    lines = []
    for line in raw_lines:
        line_stripped = line.strip()
        if len(line_stripped) > 10:
            lines.append(line_stripped)
            
    if not lines:
        return []
        
    scored_lines = []
    kws_lower = [kw.lower() for kw in keywords]
    for line in lines:
        score = 0
        line_lower = line.lower()
        for kw in kws_lower:
            # Overlap scoring
            if kw in line_lower:
                score += 2
            # Check single word matches inside line
            words = line_lower.split()
            if any(w == kw for w in words):
                score += 1
        scored_lines.append((score, line))
        
    # Stable sort by overlap score descending
    scored_lines.sort(key=lambda x: x[0], reverse=True)
    return [item[1] for item in scored_lines[:max_items]]

class ContextOrchestrator:
    def __init__(self):
        pass

    async def get_session_active_resume(self, user_id: Optional[uuid.UUID], resume_content: Optional[str], resume_repo: ResumeRepository) -> str:
        # Check cache / ID first
        if resume_content and len(resume_content) < 100:
            cached_resume = await redis_cache.get_resume(resume_content)
            if cached_resume:
                return cached_resume
            try:
                res_uuid = uuid.UUID(resume_content)
                db_resume = await resume_repo.db.get(Resume, res_uuid)
                if db_resume:
                    await redis_cache.set_resume(resume_content, db_resume.parsed_content)
                    return db_resume.parsed_content
            except Exception:
                pass
        elif resume_content:
            return resume_content

        # Standard fetch active resume
        if user_id:
            active_resume = await resume_repo.get_active(user_id)
            if active_resume:
                cached_resume = await redis_cache.get_resume(str(active_resume.id))
                if cached_resume:
                    return cached_resume
                await redis_cache.set_resume(str(active_resume.id), active_resume.parsed_content)
                return active_resume.parsed_content
        return ""

    async def get_session_active_jd(self, user_id: Optional[uuid.UUID], session_jd_id: Optional[uuid.UUID], jd_repo: JDRepository) -> str:
        if session_jd_id:
            cache_key = f"jd:{session_jd_id}"
            cached_jd = await redis_cache.get_cached_item(cache_key)
            if cached_jd:
                return cached_jd
            db_jd = await jd_repo.db.get(JobDescription, session_jd_id)
            if db_jd:
                await redis_cache.set_cached_item(cache_key, db_jd.description)
                return db_jd.description
        if user_id:
            cache_key = f"jd:active:{user_id}"
            cached_jd = await redis_cache.get_cached_item(cache_key)
            if cached_jd:
                return cached_jd
            active_jd = await jd_repo.get_active(user_id)
            if active_jd:
                await redis_cache.set_cached_item(cache_key, active_jd.description)
                return active_jd.description
        return ""

    async def _load_knowledge_doc(self, doc_id: str, knowledge_repo) -> Optional[Dict[str, str]]:
        try:
            doc_uuid = uuid.UUID(doc_id)
        except ValueError:
            return None
        cache_key = f"knowledge:{doc_uuid}"
        try:
            cached_doc = await redis_cache.get_cached_item(cache_key)
            if cached_doc:
                try:
                    data = json.loads(cached_doc)
                    return {
                        "name": data.get("name", "Document"),
                        "type": data.get("type", "document"),
                        "content": data.get("content", ""),
                    }
                except Exception:
                    return {"name": "Document", "type": "document", "content": cached_doc}
            db_doc = await knowledge_repo.db.get(KnowledgeDocument, doc_uuid)
            if not db_doc:
                return None
            doc = {"name": db_doc.document_name, "type": db_doc.document_type, "content": db_doc.content}
            await redis_cache.set_cached_item(cache_key, json.dumps(doc))
            return doc
        except Exception as e:
            logger.warning(f"Error fetching knowledge doc {doc_id}: {e}")
            return None

    async def get_custom_prompts(self, knowledge_content: Optional[str], db: AsyncSession) -> List[Dict[str, str]]:
        """Only the custom prompts the user explicitly selected (document_type == "prompt")."""
        if not knowledge_content or ("doc_id:" not in knowledge_content and "prompt_id:" not in knowledge_content):
            return []
        docs = await self.get_session_knowledge_docs(None, knowledge_content, JDRepository(db))
        return [{"name": d["name"], "content": d["content"]} for d in docs if d.get("type") == "prompt" and d.get("content")]

    async def get_session_knowledge_docs(self, user_id: Optional[uuid.UUID], knowledge_content: Optional[str], knowledge_repo: JDRepository) -> List[Dict[str, str]]:
        docs = []
        # If IDs format was sent: "doc_id:<id>|prompt_id:<id>|..." — the prefix is informational only,
        # the stored document_type decides whether a doc is a prompt. Older desktop builds join several
        # ids with commas inside one entry ("prompt_id:a,b|"), so split on commas too.
        if knowledge_content and ("doc_id:" in knowledge_content or "prompt_id:" in knowledge_content):
            ids: List[str] = []
            for part in knowledge_content.split("|"):
                key_val = part.split(":", 1)
                if len(key_val) == 2:
                    ids.extend(v.strip() for v in key_val[1].split(",") if v.strip())
            for val in dict.fromkeys(ids):  # de-duplicate, keep selection order
                doc = await self._load_knowledge_doc(val, knowledge_repo)
                if doc:
                    docs.append(doc)
        elif knowledge_content:
            docs.append({"name": "Reference Document", "type": "document", "content": knowledge_content})
        elif user_id:
            # Nothing selected: fall back to the user's reference documents. Custom prompts are NOT
            # applied implicitly — they override answer style, so only explicitly selected ones count.
            try:
                stmt = (
                    select(KnowledgeDocument)
                    .where(KnowledgeDocument.user_id == user_id, KnowledgeDocument.document_type != "prompt")
                    .order_by(KnowledgeDocument.uploaded_at.desc())
                )
                res = await knowledge_repo.db.execute(stmt)
                db_docs = res.scalars().all()
                for doc in db_docs:
                    docs.append({
                        "name": doc.document_name,
                        "type": doc.document_type,
                        "content": doc.content
                    })
            except Exception as e:
                logger.error(f"Failed to load DB docs in orchestrator: {e}")
        return docs

    async def get_previous_qas(self, session_id: Optional[uuid.UUID], qa_repo: QARepository) -> List[Dict[str, str]]:
        if not session_id:
            return []
        try:
            cached_session = await redis_cache.get_session_state(str(session_id))
            if cached_session and "previous_context" in cached_session:
                prev_ctx = cached_session["previous_context"]
                if prev_ctx and prev_ctx != "None." and prev_ctx != "None":
                    qas = []
                    parts = prev_ctx.split("Q: ")
                    for part in parts:
                        if not part.strip():
                            continue
                        q_a = part.split("\nA: ", 1)
                        if len(q_a) == 2:
                            qas.append({
                                "question": q_a[0].strip(),
                                "answer": q_a[1].strip()
                            })
                    if qas:
                        return qas[-2:]
            
            db_qas = await qa_repo.list_by_session(session_id)
            qas = [{"question": qa.question, "answer": qa.answer} for qa in db_qas[-2:]]
            if qas:
                if not cached_session:
                    cached_session = {}
                cached_session["previous_context"] = "\n".join([f"Q: {qa['question']}\nA: {qa['answer']}" for qa in qas])
                await redis_cache.set_session_state(str(session_id), cached_session)
            return qas
        except Exception as e:
            logger.error(f"Error fetching previous QAs in orchestrator: {e}")
            return []

    async def prepare_context(
        self, 
        session_id: Optional[uuid.UUID],
        user_id: Optional[uuid.UUID],
        db: AsyncSession,
        keywords: List[str],
        technologies: List[str],
        resume_content: Optional[str] = None,
        knowledge_content: Optional[str] = None,
        session_jd_id: Optional[uuid.UUID] = None
    ) -> Dict[str, Any]:
        import asyncio

        resume_repo = ResumeRepository(db)
        jd_repo = JDRepository(db)
        knowledge_repo = JDRepository(db)  # Use repo as generic database runner
        qa_repo = QARepository(db)

        # --- PARALLEL fetch all context sources simultaneously ---
        resume_text, jd_text, knowledge_docs, previous_qas = await asyncio.gather(
            self.get_session_active_resume(user_id, resume_content, resume_repo),
            self.get_session_active_jd(user_id, session_jd_id, jd_repo),
            self.get_session_knowledge_docs(user_id, knowledge_content, knowledge_repo),
            self.get_previous_qas(session_id, qa_repo),
        )
        search_terms = keywords + technologies

        # 1. Optimize Resume Context
        is_coding = False
        coding_triggers = [
            "code", "coding", "function", "algorithm", "implement", "write a", "leetcode", "leet code",
            "binary tree", "linked list", "complexity", "array", "matrix", "string", "two sum", "most water"
        ]
        if any(t in [kw.lower() for kw in search_terms] for t in coding_triggers):
            is_coding = True

        optimized_resume = "Skipped for coding question."
        if not is_coding:
            raw_resume = ""
            resume_obj = None
            if resume_content and len(resume_content) < 100:
                try:
                    res_uuid = uuid.UUID(resume_content)
                    resume_obj = await resume_repo.db.get(Resume, res_uuid)
                except Exception:
                    pass
            if not resume_obj and user_id:
                resume_obj = await resume_repo.get_active(user_id)

            # Use the candidate's real resume text. The AI-generated summaries are only a fallback:
            # feeding the model AI-written text about the candidate makes answers sound AI-written.
            if resume_obj and resume_obj.parsed_content:
                raw_resume = resume_obj.parsed_content
            elif resume_obj:
                parts = []
                if resume_obj.professional_summary:
                    parts.append(f"Professional Summary:\n{resume_obj.professional_summary}")
                if resume_obj.strengths:
                    parts.append(f"Core Strengths:\n{resume_obj.strengths}")
                if resume_obj.project_summary:
                    parts.append(f"Key Projects:\n{resume_obj.project_summary}")
                if parts:
                    optimized_resume = "\n\n".join(parts)
            elif resume_content:
                raw_resume = resume_content

            if raw_resume:
                # Compact token limit: top 12 most relevant resume lines
                ranked = rank_and_truncate_text(raw_resume, search_terms, max_items=12)
                if ranked:
                    optimized_resume = "\n".join(ranked)
                else:
                    optimized_resume = raw_resume[:1200]

        # 2. Optimize Job Description Context (compact limit: top 6 requirements)
        optimized_jd = "None loaded."
        if jd_text and jd_text != "None loaded.":
            ranked_jd = rank_and_truncate_text(jd_text, search_terms, max_items=6)
            if ranked_jd:
                optimized_jd = "\n".join(ranked_jd)
            else:
                optimized_jd = jd_text[:800]

        # 3. Optimize Candidate Memories & Reference Documents
        prompts = [d for d in knowledge_docs if d["type"] == "prompt"]
        documents = [d for d in knowledge_docs if d["type"] != "prompt"]

        knowledge_list = []
        candidate_memories_found: List[CandidateMemoryItem] = []

        # Check if knowledge_docs contain structured candidate mock practice answers
        for doc in documents:
            content = doc.get("content", "")
            if "[CANDIDATE MOCK PRACTICE ANSWERS]:" in content or "Prepared Answer:" in content:
                segments = re.findall(r'Practice Q(?:\s*\(([^)]+)\))?:\s*(.*?)\s*->\s*Prepared Answer:\s*(.*?)(?=(?:Practice Q|\Z))', content, re.DOTALL)
                for role_co, q, raw_a in segments:
                    a = re.sub(r'[\|\s]+$', '', raw_a).strip()
                    star = CandidateMemoryStore.parse_star_from_answer(a)
                    metrics = CandidateMemoryStore.extract_metrics(a)
                    candidate_memories_found.append(CandidateMemoryItem(
                        topic=q.strip()[:80],
                        company=role_co.split('@')[-1].strip() if role_co and '@' in role_co else "",
                        role=role_co.split('@')[0].strip() if role_co and '@' in role_co else (role_co or ""),
                        star=star,
                        metrics=metrics,
                        raw_text=f"Q: {q.strip()}\nA: {a.strip()}"
                    ))

        # Retrieve top 2-3 most relevant candidate memories for current question
        if candidate_memories_found:
            store = CandidateMemoryStore()
            store.add_memories(candidate_memories_found)
            top_memories = store.retrieve_top_memories(
                query=" ".join(search_terms),
                search_terms=search_terms,
                max_items=3
            )
            if top_memories:
                knowledge_list.append(store.format_compact_context(top_memories))

        # Only chunk and rank other reference documents if present
        ref_docs = [d for d in documents if "[CANDIDATE MOCK PRACTICE ANSWERS]:" not in d.get("content", "")]
        doc_chunks = []
        for doc in ref_docs:
            chunks = re.split(r'[\n\r\t]+', doc["content"])
            for ch in chunks:
                ch_stripped = ch.strip()
                if len(ch_stripped) > 20:
                    doc_chunks.append((doc["name"], doc["type"], ch_stripped))

        if doc_chunks:
            scored_chunks = []
            for doc_name, doc_type, ch in doc_chunks:
                score = 0
                ch_lower = ch.lower()
                for term in search_terms:
                    if term.lower() in ch_lower:
                        score += 2
                scored_chunks.append((score, doc_name, doc_type, ch))

            scored_chunks.sort(key=lambda x: x[0], reverse=True)
            top_chunks = scored_chunks[:2]
            for score, name, doc_type, chunk in top_chunks:
                knowledge_list.append(f"Reference Document [{name}]: {chunk}")

        # Custom prompts are NOT mixed into reference material: they are returned separately
        # and placed in the system prompt by prompt_builder.build_system_prompt().

        # 4. Hybrid Session Context (Recent 2 verbatim turns + Rolling Summary of earlier turns)
        previous_str = "None."
        if previous_qas:
            followup_triggers = ["why", "how", "what was", "biggest challenge", "tradeoff", "elaborate", "scale", "edge case", "disagree"]
            is_followup = any(t in " ".join(search_terms).lower() for t in followup_triggers)
            last_qa = previous_qas[-1]

            # Verbatim recent turns (up to 2)
            recent_turns = previous_qas[-2:]
            recent_str = "\n".join([f"Q: {qa['question']}\nA: {qa['answer'][:220]}..." for qa in recent_turns])

            # Earlier turns (>2) condensed into an incremental rolling summary to preserve older context
            earlier_turns = previous_qas[:-2]
            summary_str = ""
            if earlier_turns:
                earlier_topics = [f"- Earlier Topic: '{q['question'][:70]}' (Candidate key point: {q['answer'][:80]}...)" for q in earlier_turns]
                summary_str = "[Rolling Summary of Earlier Interview Turns]:\n" + "\n".join(earlier_topics) + "\n\n"

            if is_followup and last_qa:
                previous_str = (
                    f"{summary_str}[Follow-Up Note: Interviewer is following up on previous topic]\n"
                    f"Previous Question: {last_qa['question']}\n"
                    f"Candidate's Previous Point: {last_qa['answer'][:250]}..."
                )
            else:
                previous_str = f"{summary_str}{recent_str}"

        # 5. Reasoning Focus logic
        reasoning_focus = "General technical review and validation."
        if any(t in ["Redis", "Kafka", "PostgreSQL"] for t in technologies):
            reasoning_focus = "Focus on cache strategy, data consistency, message durability, and system scaling."
        elif any(t in ["Snowflake", "Databricks", "Fabric", "ADF"] for t in technologies):
            reasoning_focus = "Focus on cloud data architecture, pipeline dependency routing, micro-partitioning, and lazy evaluation constraints."

        return {
            "resume_context": optimized_resume if optimized_resume else "None loaded.",
            "knowledge_context": "\n\n".join(knowledge_list) if knowledge_list else "None loaded.",
            "jd_context": optimized_jd if optimized_jd else "None loaded.",
            "previous_context": previous_str,
            "reasoning_focus": reasoning_focus,
            "custom_prompts": [{"name": p["name"], "content": p["content"]} for p in prompts if p.get("content")],
        }

context_orchestrator = ContextOrchestrator()


