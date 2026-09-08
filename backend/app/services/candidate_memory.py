import re
import json
import uuid
import logging
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger("copilotx.candidate_memory")

class CandidateMemoryItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: str = "star_story"  # star_story, project, metric, practiced_qa, skill
    topic: str = ""
    company: str = ""
    role: str = ""
    technologies: List[str] = Field(default_factory=list)
    metrics: List[str] = Field(default_factory=list)
    star: Dict[str, str] = Field(default_factory=dict)  # { situation, task, action, result }
    raw_text: str = ""
    score: Optional[int] = None

# Metric extraction pattern (e.g., 40%, 10x, 500ms, 10k req/s, $1.2M)
METRIC_REGEX = re.compile(
    r'\b(\d+(?:\.\d+)?\s*(?:%|x|ms|s|k|m|b|gb|tb|req/s|rps|tps|qps|users|customers|dollars|\$|percent))\b',
    re.IGNORECASE
)

# Common technical stop words for keyword extraction
STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "he",
    "in", "is", "it", "its", "of", "on", "that", "the", "to", "was", "were",
    "will", "with", "i", "my", "we", "our", "you", "your", "can", "could",
    "would", "should", "what", "how", "why", "tell", "about", "describe", "explain"
}

def tokenize_for_search(text: str) -> List[str]:
    if not text:
        return []
    words = re.findall(r'[a-zA-Z0-9_\-\.\#\+]+', text.lower())
    return [w for w in words if len(w) > 1 and w not in STOP_WORDS]

class CandidateMemoryStore:
    """
    Ultra-lightweight, in-memory candidate memory index.
    Provides sub-millisecond retrieval of precomputed candidate STAR stories,
    practiced answers, metrics, and project experiences.
    """
    def __init__(self):
        self._memories: List[CandidateMemoryItem] = []

    def clear(self):
        self._memories.clear()

    def add_memory(self, item: CandidateMemoryItem):
        self._memories.append(item)

    def add_memories(self, items: List[CandidateMemoryItem]):
        self._memories.extend(items)

    @classmethod
    def parse_star_from_answer(cls, answer: str) -> Dict[str, str]:
        """
        Extracts STAR components from structured or semi-structured answers.
        """
        star = {
            "situation": "",
            "task": "",
            "action": "",
            "result": ""
        }
        if not answer:
            return star

        lower = answer.lower()
        # Look for explicit STAR markers (handles newline and pipe delimiters)
        s_match = re.search(r'\b(?:situation|context|background):\s*([^|\n\r]+)', answer, re.IGNORECASE)
        t_match = re.search(r'\b(?:task|challenge|goal|problem):\s*([^|\n\r]+)', answer, re.IGNORECASE)
        a_match = re.search(r'\b(?:action|execution|implementation|solution):\s*([^|\n\r]+)', answer, re.IGNORECASE)
        r_match = re.search(r'\b(?:result|impact|outcome|metrics):\s*([^|\n\r]+)', answer, re.IGNORECASE)

        if s_match: star["situation"] = s_match.group(1).strip()
        if t_match: star["task"] = t_match.group(1).strip()
        if a_match: star["action"] = a_match.group(1).strip()
        if r_match: star["result"] = r_match.group(1).strip()

        # Fallback heuristic: split lines or sentences if explicit markers missing
        if not star["situation"] and not star["action"]:
            lines = [l.strip() for l in answer.split('\n') if len(l.strip()) > 15]
            if len(lines) >= 3:
                star["situation"] = lines[0]
                star["action"] = " ".join(lines[1:-1])
                star["result"] = lines[-1]
            elif len(lines) == 2:
                star["situation"] = lines[0]
                star["result"] = lines[1]
            elif lines:
                star["action"] = lines[0]

        return star

    @classmethod
    def extract_metrics(cls, text: str) -> List[str]:
        if not text:
            return []
        matches = METRIC_REGEX.findall(text)
        # Deduplicate preserving order
        seen = set()
        unique = []
        for m in matches:
            m_clean = m.strip()
            if m_clean.lower() not in seen:
                seen.add(m_clean.lower())
                unique.append(m_clean)
        return unique

    @classmethod
    def normalize_mock_qas(cls, qas: List[Dict[str, Any]], role: str = "", company: str = "") -> List[CandidateMemoryItem]:
        """
        Precomputes structured memories from raw mock interview Q&As.
        Runs in preparation loop so live retrieval is instantaneous.
        """
        items: List[CandidateMemoryItem] = []
        for qa in qas:
            q = str(qa.get("question", "")).strip()
            a = str(qa.get("answer", "")).strip()
            if not q or not a:
                continue

            star = cls.parse_star_from_answer(a)
            metrics = cls.extract_metrics(a)

            # Detect mentioned technologies
            known_techs = [
                "redis", "kafka", "postgres", "postgresql", "snowflake", "databricks",
                "spark", "pyspark", "aws", "gcp", "azure", "docker", "kubernetes",
                "react", "node", "python", "fastapi", "graphql", "grpc", "ci/cd"
            ]
            detected = [tech for tech in known_techs if tech in (q + " " + a).lower()]

            item = CandidateMemoryItem(
                id=str(uuid.uuid4()),
                type="practiced_qa" if not star.get("result") else "star_story",
                topic=q[:80],
                company=company,
                role=role,
                technologies=detected,
                metrics=metrics,
                star=star,
                raw_text=f"Q: {q}\nA: {a}",
                score=qa.get("score")
            )
            items.append(item)
        return items

    def retrieve_top_memories(
        self,
        query: str,
        search_terms: Optional[List[str]] = None,
        max_items: int = 3
    ) -> List[CandidateMemoryItem]:
        """
        Sub-millisecond weighted retrieval of top relevant candidate memories.
        Weights:
          - Exact topic / question match: +5
          - STAR Result with metrics match: +3
          - Technology match: +2
          - Keyword token overlap: +1
        """
        if not self._memories:
            return []

        q_tokens = set(tokenize_for_search(query))
        if search_terms:
            for term in search_terms:
                q_tokens.update(tokenize_for_search(term))

        q_lower = query.lower()

        scored: List[tuple[float, CandidateMemoryItem]] = []
        for item in self._memories:
            score = 0.0

            # 1. Topic substring match
            if item.topic and item.topic.lower() in q_lower:
                score += 5.0
            elif item.topic:
                t_tokens = set(tokenize_for_search(item.topic))
                overlap = len(q_tokens & t_tokens)
                score += overlap * 2.0

            # 2. Technology overlap
            for tech in item.technologies:
                if tech.lower() in q_lower or tech.lower() in q_tokens:
                    score += 2.5

            # 3. Metrics bonus (answers with real quantified results are preferred)
            if item.metrics:
                score += min(len(item.metrics), 3) * 0.5

            # 4. Token overlap across raw text / STAR
            star_text = f"{item.star.get('situation', '')} {item.star.get('action', '')} {item.star.get('result', '')}"
            body_tokens = set(tokenize_for_search(star_text or item.raw_text))
            body_overlap = len(q_tokens & body_tokens)
            score += min(body_overlap * 0.5, 4.0)

            # Prioritize higher scoring mock interview answers
            if item.score:
                score += (item.score / 100.0)

            if score > 0.5:
                scored.append((score, item))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:max_items]]

    def format_compact_context(self, top_memories: List[CandidateMemoryItem]) -> str:
        """
        Builds a compact context block for the LLM prompt.
        Avoids redundant fluff and strictly exposes STAR evidence and metrics.
        """
        if not top_memories:
            return "No previous practice answers match this question."

        blocks = []
        for idx, m in enumerate(top_memories, 1):
            parts = []
            if m.topic:
                parts.append(f"Practiced Topic: {m.topic}")
            if m.star.get("situation") or m.star.get("action") or m.star.get("result"):
                star_lines = []
                if m.star.get("situation"): star_lines.append(f"  • Situation: {m.star['situation']}")
                if m.star.get("task"): star_lines.append(f"  • Task: {m.star['task']}")
                if m.star.get("action"): star_lines.append(f"  • Action: {m.star['action']}")
                if m.star.get("result"): star_lines.append(f"  • Result: {m.star['result']}")
                parts.append("STAR Breakdown:\n" + "\n".join(star_lines))
            elif m.raw_text:
                # Truncate raw text to maximum 300 chars to protect token budget
                parts.append(f"Prepared Answer: {m.raw_text[:300]}...")
            if m.metrics:
                parts.append(f"Verified Metrics: {', '.join(m.metrics)}")

            blocks.append(f"[Candidate Memory #{idx}]\n" + "\n".join(parts))

        return "\n\n".join(blocks)

candidate_memory_store = CandidateMemoryStore()
