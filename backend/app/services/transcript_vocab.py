"""
Role-aware vocabulary for live transcripts.

Speech-to-text hears technical terms as everyday words ("OOPS" -> "hoops", "SQL" -> "sequel").
For a software interview those readings are almost always wrong, but for, say, a sports-coaching
role "hoops" is exactly right, so everything here is gated on the session's role:

  1. keyterms(): terms passed to Deepgram (Nova-3 keyterm prompting) so it hears them correctly
     in the first place.
  2. correct_transcript(): a safety net that rewrites the common mishearings that still slip
     through, before the text is shown to the candidate or sent to the answer pipeline.
"""
import re
from typing import List, Optional

_SOFTWARE_ROLE = re.compile(
    r"\b(sde|swe|sdet|software|developer|dev|programmer|coder|full[\s-]?stack|front[\s-]?end|back[\s-]?end|"
    r"web|mobile|android|ios|devops|sre|cloud|platform|infra(structure)?|data|ml|ai|machine learning|"
    r"qa|test(ing)?|automation|cyber\s?security|tech(nical)?|engineer(ing)?|solutions? architect|software architect|"
    r"java|python|javascript|typescript|react|node|golang|\.net|dotnet|c\+\+|c#|php|ruby|rust|kotlin|swift|"
    r"coding|dsa|system design)\b",
    re.IGNORECASE,
)
# "Engineer" alone also covers non-software roles.
_NON_SOFTWARE_ENGINEERING = re.compile(
    r"\b(mechanical|civil|electrical|chemical|structural|automobile|automotive|aerospace|petroleum|"
    r"mining|marine|biomedical|production|manufacturing|sales engineer)\b",
    re.IGNORECASE,
)


def is_software_role(*role_hints: Optional[str]) -> bool:
    text = " ".join(h for h in role_hints if h)
    if not text.strip():
        return False
    if _NON_SOFTWARE_ENGINEERING.search(text) and not re.search(r"\b(software|sde|developer|coding)\b", text, re.I):
        return False
    return bool(_SOFTWARE_ROLE.search(text))


# Deepgram keyterm prompting: the terms most often misheard in software interviews.
SOFTWARE_KEYTERMS: List[str] = [
    "OOPS", "OOP", "polymorphism", "encapsulation", "inheritance", "abstraction",
    "SQL", "NoSQL", "MySQL", "PostgreSQL", "MongoDB", "Redis", "Kafka",
    "JSON", "API", "REST", "GraphQL", "gRPC", "microservices",
    "Kubernetes", "Docker", "AWS", "Azure", "GCP", "CI/CD", "DevOps", "Git", "GitHub",
    "JavaScript", "TypeScript", "React", "Node.js", "Next.js", "Angular", "Vue",
    "Python", "Java", "Spring Boot", "Django", "Flask", "C++", "C#", ".NET", "Golang",
    "async", "await", "multithreading", "deadlock", "DSA", "LeetCode", "Big O",
    "hash map", "linked list", "binary tree", "recursion", "dynamic programming",
    "SOLID", "MVC", "OAuth", "JWT", "HTTP", "HTTPS", "TCP", "DNS", "load balancer",
]

# (pattern, replacement). Patterns are matched case-insensitively on word boundaries, longest first.
_SOFTWARE_CORRECTIONS = [
    # OOPS / OOP
    (r"hoops?(?=\s+(concepts?|principles?|pillars?|in|based|programming|languages?)\b)", "OOPS"),
    (r"oops(?=\s+(concepts?|principles?|pillars?|in|based|programming|languages?)\b)", "OOPS"),
    (r"o[\s.]+o[\s.]+p[\s.]+s", "OOPS"),
    # "oops" as the subject ("pillars of oops", "explain oops"), not the exclamation.
    (r"(of|about|explain|in|using|with|on|know)\s+oops", r"\1 OOPS"),
    (r"o\.?\s?o\.?\s?p(?!\w)", "OOP"),
    (r"hoops", "OOPS"),
    (r"object oriented", "object-oriented"),
    # Databases
    (r"no\s?sequel", "NoSQL"),
    (r"my\s?sequel", "MySQL"),
    (r"post\s?gres(s|ql)?|post\s?grass|post\s?grease|post\s?gray\s?sequel", "PostgreSQL"),
    (r"sequel(?!\s+(to|of)\b)", "SQL"),
    (r"s\.?\s?q\.?\s?l", "SQL"),
    (r"mongo\s?(d\.?\s?b|db)", "MongoDB"),
    # Data formats / APIs
    (r"jason(?=\s+(files?|objects?|format|data|responses?|payloads?|parsing|schema|web\s?tokens?)\b)", "JSON"),
    (r"graph\s?q\.?\s?l", "GraphQL"),
    (r"rest\s?full", "RESTful"),
    (r"a\.?\s?p\.?\s?i\.?s(?!\w)", "APIs"),
    (r"a\.?\s?p\.?\s?i(?!\w)", "API"),
    (r"j\.?\s?w\.?\s?t", "JWT"),
    # Infra
    (r"(cuber|kuber|cooper|cube\s?er|cube)\s?n[aei]t+(e?s|ies|ease|is|ees)", "Kubernetes"),
    (r"c\.?\s?i\.?\s?/?\s?c\.?\s?d", "CI/CD"),
    (r"dev\s?ops", "DevOps"),
    (r"git\s?hub", "GitHub"),
    (r"a\.?\s?w\.?\s?s(?!\w)", "AWS"),
    # Languages / frameworks
    (r"java\s?script", "JavaScript"),
    (r"type\s?script", "TypeScript"),
    (r"node\s?(dot\s?)?js|node\s?j\.?\s?s", "Node.js"),
    (r"next\s?(dot\s?)?js|next\s?j\.?\s?s", "Next.js"),
    (r"react\s?(dot\s?)?js|react\s?j\.?\s?s", "React.js"),
    (r"c\s?plus\s?plus", "C++"),
    (r"(c|see)\s?sharp", "C#"),
    (r"dot\s?net", ".NET"),
    (r"spring\s?boots?", "Spring Boot"),
    (r"jay\s?query", "jQuery"),
    # Concepts
    (r"a\s?sink(?=\s+(and|await|function|code|calls?|programming|operations?)\b)", "async"),
    (r"lead\s?code|leet\s?code", "LeetCode"),
    (r"d\.?\s?s\.?\s?a(?!\w)", "DSA"),
    (r"big\s?oh?(?=\s+(notation|of|complexity)\b)", "Big O"),
    (r"poly\s?morph(ism)?", "polymorphism"),
    (r"micro\s?services", "microservices"),
    (r"multi\s?threading", "multithreading"),
    (r"g\.?\s?r\.?\s?p\.?\s?c(?!\w)", "gRPC"),
    (r"k\s?8\s?s(?!\w)", "Kubernetes"),
    (r"fast\s?a\.?\s?p\.?\s?i", "FastAPI"),
    (r"pie\s?test", "pytest"),
    (r"pie\s?thon", "Python"),
]
_COMPILED = [(re.compile(rf"(?<!\w)(?:{p})(?!\w)", re.IGNORECASE), r) for p, r in _SOFTWARE_CORRECTIONS]

PROCESSOR_VERSION = "vocab-v1.3"


def keyterms(software: bool) -> List[str]:
    return list(SOFTWARE_KEYTERMS) if software else []


def correct_transcript(text: str, software: bool) -> str:
    """Rewrite common technical mishearings. No-op unless the session is for a software role."""
    if not software or not text:
        return text
    out = text
    for pattern, replacement in _COMPILED:
        out = pattern.sub(replacement, out)
    return out


def correct_transcript_with_confidence(
    text: str,
    software: bool,
    asr_confidence: float = 1.0,
    min_asr_confidence: float = 0.35,
) -> dict:
    """
    LLD v1.3 Item 22: Context-aware transcript correction with abstention and provenance.
    Abstains on non-software roles or ultra-low ASR confidence to avoid harmful rewrites.
    """
    if not text:
        return {
            "raw_text": text,
            "corrected_text": text,
            "changed": False,
            "abstained": False,
            "reason": "empty_text",
            "confidence": asr_confidence,
            "replacements": [],
            "processor_version": PROCESSOR_VERSION,
        }

    if not software:
        return {
            "raw_text": text,
            "corrected_text": text,
            "changed": False,
            "abstained": True,
            "reason": "non_software_role",
            "confidence": asr_confidence,
            "replacements": [],
            "processor_version": PROCESSOR_VERSION,
        }

    if asr_confidence < min_asr_confidence:
        return {
            "raw_text": text,
            "corrected_text": text,
            "changed": False,
            "abstained": True,
            "reason": f"low_asr_confidence:{asr_confidence:.2f}<{min_asr_confidence}",
            "confidence": asr_confidence,
            "replacements": [],
            "processor_version": PROCESSOR_VERSION,
        }

    out = text
    applied = []
    for pattern, replacement in _COMPILED:
        matches = pattern.findall(out)
        if matches:
            applied.append({"replacement": replacement, "count": len(matches)})
            out = pattern.sub(replacement, out)

    return {
        "raw_text": text,
        "corrected_text": out,
        "changed": out != text,
        "abstained": False,
        "reason": "applied_domain_vocab" if out != text else "no_match",
        "confidence": asr_confidence,
        "replacements": applied,
        "processor_version": PROCESSOR_VERSION,
    }
