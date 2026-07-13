import re
import uuid
import logging
from typing import List, Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.models import JobDescription, Resume, KnowledgeDocument, QuestionAnswer
from app.db.repositories import ResumeRepository, JDRepository, KnowledgeRepository, QARepository
from app.cache.redis import redis_cache

logger = logging.getLogger("copilotx.context_orchestrator")

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

    async def get_session_knowledge_docs(self, user_id: Optional[uuid.UUID], knowledge_content: Optional[str], knowledge_repo: JDRepository) -> List[Dict[str, str]]:
        docs = []
        # If IDs format was sent
        if knowledge_content and ("doc_id:" in knowledge_content or "prompt_id:" in knowledge_content):
            parts = knowledge_content.split("|")
            for part in parts:
                if not part.strip():
                    continue
                key_val = part.split(":", 1)
                if len(key_val) == 2:
                    k, val = key_val[0].strip(), key_val[1].strip()
                    try:
                        doc_uuid = uuid.UUID(val)
                        cache_key = f"knowledge:{doc_uuid}"
                        cached_doc = await redis_cache.get_cached_item(cache_key)
                        
                        import json
                        if cached_doc:
                            try:
                                doc_data = json.loads(cached_doc)
                                docs.append({
                                    "name": doc_data.get("name", "Document"),
                                    "type": doc_data.get("type", "document"),
                                    "content": doc_data.get("content", "")
                                })
                            except Exception:
                                docs.append({"name": "Document", "type": "document", "content": cached_doc})
                        else:
                            db_doc = await knowledge_repo.db.get(KnowledgeDocument, doc_uuid)
                            if db_doc:
                                doc_json = json.dumps({
                                    "name": db_doc.document_name,
                                    "type": db_doc.document_type,
                                    "content": db_doc.content
                                })
                                await redis_cache.set_cached_item(cache_key, doc_json)
                                docs.append({
                                    "name": db_doc.document_name,
                                    "type": db_doc.document_type,
                                    "content": db_doc.content
                                })
                    except Exception as e:
                        logger.warning(f"Error fetching knowledge doc in orchestrator: {e}")
        elif knowledge_content:
            docs.append({"name": "Reference Document", "type": "document", "content": knowledge_content})
        elif user_id:
            # Load active knowledge docs from repository
            # Since repository lacks direct database reference in some imports, construct locally
            try:
                stmt = select(KnowledgeDocument).where(KnowledgeDocument.user_id == user_id).order_by(KnowledgeDocument.uploaded_at.desc())
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

            if resume_obj:
                # Attempt to use pre-calculated summaries to minimize tokens
                parts = []
                if resume_obj.professional_summary:
                    parts.append(f"Professional Summary:\n{resume_obj.professional_summary}")
                if resume_obj.strengths:
                    parts.append(f"Core Strengths:\n{resume_obj.strengths}")
                if resume_obj.project_summary:
                    parts.append(f"Key Projects:\n{resume_obj.project_summary}")
                
                if parts:
                    optimized_resume = "\n\n".join(parts)
                else:
                    raw_resume = resume_obj.parsed_content
            elif resume_content:
                raw_resume = resume_content

            if raw_resume:
                ranked = rank_and_truncate_text(raw_resume, search_terms, max_items=20)
                if ranked:
                    optimized_resume = "\n".join(ranked)
                else:
                    optimized_resume = raw_resume[:2500]

        # 2. Optimize Job Description Context
        optimized_jd = "None loaded."
        if jd_text and jd_text != "None loaded.":
            ranked_jd = rank_and_truncate_text(jd_text, search_terms, max_items=15)
            if ranked_jd:
                optimized_jd = "\n".join(ranked_jd)
            else:
                optimized_jd = jd_text[:1500]

        # 3. Optimize Knowledge Reference Documents & Prompts
        prompts = [d for d in knowledge_docs if d["type"] == "prompt"]
        documents = [d for d in knowledge_docs if d["type"] != "prompt"]

        # Only chunk and rank reference documents
        doc_chunks = []
        for doc in documents:
            chunks = re.split(r'[\n\r\t]+', doc["content"])
            for ch in chunks:
                ch_stripped = ch.strip()
                if len(ch_stripped) > 20:
                    doc_chunks.append((doc["name"], doc["type"], ch_stripped))

        scored_chunks = []
        for doc_name, doc_type, ch in doc_chunks:
            score = 0
            ch_lower = ch.lower()
            for term in search_terms:
                if term.lower() in ch_lower:
                    score += 2
            scored_chunks.append((score, doc_name, doc_type, ch))

        scored_chunks.sort(key=lambda x: x[0], reverse=True)
        top_chunks = scored_chunks[:3]

        knowledge_list = []
        # Keep instruction prompt template exactly as uploaded
        for p in prompts:
            knowledge_list.append(f"AI Instruction Prompt [{p['name']}]: {p['content']}")
        # Rank reference doc chunks
        for score, name, doc_type, chunk in top_chunks:
            knowledge_list.append(f"Reference Document [{name}]: {chunk}")

        # Reasoning Focus logic
        reasoning_focus = "General technical review and validation."
        if any(t in ["Redis", "Kafka", "PostgreSQL"] for t in technologies):
            reasoning_focus = "Focus on cache strategy, data consistency, message durability, and system scaling."
        elif any(t in ["Snowflake", "Databricks", "Fabric", "ADF"] for t in technologies):
            reasoning_focus = "Focus on cloud data architecture, pipeline dependency routing, micro-partitioning, and lazy evaluation constraints."

        return {
            "resume_context": optimized_resume if optimized_resume else "None loaded.",
            "knowledge_context": "\n".join(knowledge_list) if knowledge_list else "None loaded.",
            "jd_context": optimized_jd if optimized_jd else "None loaded.",
            "previous_context": "\n".join([f"Q: {qa['question']}\nA: {qa['answer']}" for qa in previous_qas]) if previous_qas else "None.",
            "reasoning_focus": reasoning_focus
        }

context_orchestrator = ContextOrchestrator()


