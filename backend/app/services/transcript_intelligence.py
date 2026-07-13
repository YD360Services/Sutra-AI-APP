import re
import json
import logging
from typing import List, Set, Dict, Any, Tuple
from enum import Enum

logger = logging.getLogger("copilotx.transcript_intelligence")

class SessionState(str, Enum):
    WAITING = "WAITING"
    QUESTION_STARTED = "QUESTION_STARTED"
    QUESTION_BUILDING = "QUESTION_BUILDING"
    QUESTION_COMPLETED = "QUESTION_COMPLETED"

# Configurable Pattern Registry for categories and technologies
PATTERN_REGISTRY = {
    "categories": {
        "Coding": ["array", "string", "leetcode", "algorithm", "tree", "graph", "dfs", "bfs", "binary search", "recursion", "complexity"],
        "SQL": ["join", "group by", "rank", "dense_rank", "cte", "window", "merge", "row_number", "partition by", "indexes", "primary key"],
        "Snowflake": ["warehouse", "task", "stream", "pipe", "micro partition", "snowpipe", "time travel", "clustering"],
        "Fabric": ["lakehouse", "warehouse", "notebook", "pipeline", "synapse", "onelake", "direct lake"],
        "Azure": ["adf", "data factory", "logic app", "key vault", "azure sql", "blob storage", "synapse link"],
        "PySpark": ["pyspark", "dataframe", "rdd", "spark context", "broadcast join", "lazy evaluation", "partitioning"],
        "ADF": ["adf", "copy activity", "mapping data flow", "integration runtime", "pipeline run", "triggers"],
        "Databricks": ["databricks", "delta lake", "unity catalog", "photon", "dbfs", "spark clusters", "notebooks"],
        "System Design": ["cache", "redis", "load balancer", "queue", "kafka", "microservice", "scalability", "sharding", "replication", "consistency", "availability", "cdn"],
        "Behavioral": ["time when", "challenge", "team", "leadership", "conflict", "disagreement", "success", "mistake", "prioritize"],
        "HR": ["salary", "strength", "weakness", "career goal", "why this company", "compensation", "benefits"],
        "General Technical": []
    },
    "technologies": {
        "redis": "Redis",
        "kafka": "Kafka",
        "snowflake": "Snowflake",
        "azure": "Azure",
        "fabric": "Fabric",
        "adf": "ADF",
        "python": "Python",
        "java": "Java",
        "react": "React",
        "node": "Node",
        "spark": "Spark",
        "pyspark": "PySpark",
        "databricks": "Databricks",
        "kubernetes": "Kubernetes",
        "docker": "Docker",
        "postgresql": "PostgreSQL",
        "postgres": "PostgreSQL",
        "aws": "AWS",
        "gcp": "GCP"
    },
    "keywords": [
        "cache", "replication", "joins", "window functions", "streams", "microservices", 
        "partitioning", "dataframe", "pipelines", "triggers", "warehouse", "lakehouse"
    ],
    "predictions": {
        # Mapping keyword tuples to predicted completed questions
        ("redis", "cache"): "Explain Redis caching strategy, eviction policies, and how to scale it.",
        ("rank", "dense_rank"): "Explain the difference between RANK(), DENSE_RANK(), and ROW_NUMBER() in SQL.",
        ("design", "notification"): "Design a highly scalable, real-time push notification system.",
        ("time", "conflict"): "Tell me about a time you handled a significant conflict or disagreement within a team.",
        ("snowflake", "warehouse"): "Explain Snowflake's multi-cluster warehouse architecture and scaling policies.",
        ("pyspark", "join"): "Explain broadcast joins and partitioning optimization in PySpark.",
        ("adf", "pipeline"): "How would you design a robust retry and dependency flow in an Azure Data Factory pipeline?",
        ("system", "design"): "Explain your approach to system design, load balancing, and caching strategies."
    }
}

QUESTION_TRIGGERS = {
    "can", "could", "what", "why", "how", "explain", "difference", 
    "tell me", "describe", "implement", "write", "create", 
    "optimize", "solve", "debug", "find", "describe", "return"
}

INTENT_MAP = [
    (["difference", "compare", "versus", "vs"], "Compare"),
    (["design", "architecture", "system", "structure"], "Design"),
    (["implement", "how to write", "how do you"], "Implement"),
    (["optimize", "fast", "performance", "speed", "scalable"], "Optimize"),
    (["debug", "fix", "error", "exception", "broken"], "Debug"),
    (["write", "code", "programming", "script"], "Write Code"),
    (["time when", "conflict", "situation", "challenge", "disagree"], "Behavioral"),
    (["salary", "weakness", "strength", "compensation"], "HR"),
    (["explain", "what is", "tell me", "describe", "how does"], "Explain")
]

class TranscriptIntelligenceEngine:
    def __init__(self):
        # Pre-compile triggers for micro-second question detection speed
        self._trigger_patterns = [
            re.compile(rf"\b{re.escape(trigger)}\b", re.IGNORECASE)
            for trigger in QUESTION_TRIGGERS
        ]
        
        # Pre-compile technology matching patterns
        self._tech_patterns = [
            (re.compile(rf"\b{re.escape(key)}\b", re.IGNORECASE), canonical)
            for key, canonical in PATTERN_REGISTRY["technologies"].items()
        ]

    def normalize(self, text: str) -> str:
        if not text:
            return ""
        # Lowercase, clean up punctuation (but keep sentence endings like ?, . for state logic), and spaces
        cleaned = text.lower().strip()
        cleaned = re.sub(r'\s+', ' ', cleaned)
        return cleaned

    def detect_question_started(self, normalized_text: str) -> bool:
        if not normalized_text:
            return False
        # Match starts or contains trigger phrases using pre-compiled regexes
        for pattern in self._trigger_patterns:
            if pattern.search(normalized_text):
                return True
        return False

    def determine_state(self, current_state: SessionState, text: str, question_started: bool, question_completed: bool) -> SessionState:
        if not text.strip():
            return SessionState.WAITING
        
        if question_completed:
            return SessionState.QUESTION_COMPLETED
        
        if current_state == SessionState.WAITING and question_started:
            return SessionState.QUESTION_STARTED
            
        if current_state in (SessionState.QUESTION_STARTED, SessionState.QUESTION_BUILDING):
            return SessionState.QUESTION_BUILDING
            
        return current_state

    def detect_intent(self, normalized_text: str) -> str:
        for keywords, intent in INTENT_MAP:
            for kw in keywords:
                if kw in normalized_text:
                    return intent
        return "General Technical"

    def detect_category(self, normalized_text: str) -> str:
        best_category = "General Technical"
        max_matches = 0
        
        for category, patterns in PATTERN_REGISTRY["categories"].items():
            matches = 0
            for pat in patterns:
                if pat in normalized_text:
                    matches += 1
            if matches > max_matches:
                max_matches = matches
                best_category = category
                
        return best_category

    def detect_technologies(self, normalized_text: str) -> List[str]:
        found = []
        for pattern, canonical in self._tech_patterns:
            if pattern.search(normalized_text):
                if canonical not in found:
                    found.append(canonical)
        return found

    def extract_keywords(self, normalized_text: str) -> List[str]:
        found = []
        for kw in PATTERN_REGISTRY["keywords"]:
            if kw == "cache":
                if "cache" in normalized_text or "caching" in normalized_text or "cached" in normalized_text:
                    found.append("cache")
            elif kw in normalized_text:
                found.append(kw)
        return found

    def predict_question(self, normalized_text: str, detected_tech: List[str], detected_keywords: List[str]) -> str:
        # Check trigger patterns match in custom prediction dict
        tech_lower = [t.lower() for t in detected_tech]
        kws_lower = [k.lower() for k in detected_keywords]
        
        # Exact/subset match of keywords
        for keys, predicted in PATTERN_REGISTRY["predictions"].items():
            if all(k in tech_lower or k in kws_lower or k in normalized_text for k in keys):
                return predicted
                
        # Fallback: capitalize sentence or format cleanly
        if len(normalized_text) > 3:
            capitalized = normalized_text[0].upper() + normalized_text[1:]
            if not capitalized.endswith(('.', '?', '!')):
                capitalized += "?"
            return capitalized
        return "Can you explain?"

    def estimate_difficulty(self, detected_tech: List[str], intent: str, keywords: List[str]) -> str:
        score = 0
        # 1. Tech count
        score += len(detected_tech) * 2
        # 2. Complex intents
        if intent in ("Design", "Compare", "Optimize"):
            score += 3
        # 3. Complex keyword count
        score += len(keywords)
        
        if score <= 2:
            return "Easy"
        elif score <= 5:
            return "Medium"
        else:
            return "Hard"

    def build_vector_query(self, detected_tech: List[str], keywords: List[str], intent: str) -> str:
        parts = [t.lower() for t in detected_tech] + keywords
        if intent not in ("General Technical", "Explain"):
            parts.append(intent.lower())
        query = " ".join(parts).strip()
        return query or "caching strategy scalability"

    def check_completion(self, text: str, pause_duration: float = 0.0) -> Tuple[bool, bool]:
        if not text:
            return False, False
            
        ended_with_punctuation = text.strip().endswith(('?', '.', '!'))
        exceeded_pause = pause_duration > 1.5
        
        ready = ended_with_punctuation or exceeded_pause
        completed = ended_with_punctuation or pause_duration > 1.0
        
        return completed, ready

    def compute_confidence(
        self, 
        question_started: bool, 
        intent: str, 
        detected_tech: List[str], 
        has_prediction: bool, 
        sentence_completed: bool, 
        pause_duration: float
    ) -> float:
        score = 0.0
        
        # 1. Question started: 30%
        if question_started:
            score += 0.30
            
        # 2. Intent matched: 15%
        if intent != "General Technical":
            score += 0.15
            
        # 3. Tech detected: 15%
        if detected_tech:
            score += 0.15
            
        # 4. Assembled prediction: 20%
        if has_prediction:
            score += 0.20
            
        # 5. Sentence completed (punctuation): 10%
        if sentence_completed:
            score += 0.10
            
        # 6. Silence detected (pause > 1.0s): 10%
        if pause_duration > 1.0:
            score += 0.10
            
        return min(1.0, max(0.0, round(score, 2)))

    def analyze(self, raw_transcript: str, previous_state: SessionState = SessionState.WAITING, pause_duration: float = 0.0) -> Dict[str, Any]:
        norm = self.normalize(raw_transcript)
        
        started = self.detect_question_started(norm)
        completed, ready = self.check_completion(raw_transcript, pause_duration)
        
        state = self.determine_state(previous_state, raw_transcript, started, completed)
        
        intent = self.detect_intent(norm)
        category = self.detect_category(norm)
        techs = self.detect_technologies(norm)
        kws = self.extract_keywords(norm)
        
        pred = self.predict_question(norm, techs, kws)
        diff = self.estimate_difficulty(techs, intent, kws)
        v_query = self.build_vector_query(techs, kws, intent)
        
        conf = self.compute_confidence(started, intent, techs, len(pred) > 0, completed, pause_duration)
        
        return {
            "state": state.value,
            "question_started": started,
            "question_completed": completed,
            "ready_for_answer": ready,
            "confidence": conf,
            "intent": intent,
            "category": category,
            "difficulty": diff,
            "prediction": pred,
            "technologies": techs,
            "keywords": kws,
            "vector_query": v_query
        }

transcript_engine = TranscriptIntelligenceEngine()
