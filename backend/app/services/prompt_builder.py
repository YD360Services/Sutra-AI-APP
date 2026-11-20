"""
Single place where live-answer prompts are assembled.

System prompt layers, in order (later layers win on conflict):
  1. Persona + grounding rules           (always)
  1b. Live-speech guidance               (fuzzy / partial / misheard transcripts; not screenshot or intro)
  2. Mode guidance                       (coding / system design / hr / screenshot / introduction)
  3. Default answer style                (DROPPED entirely when the user has custom prompts)
  4. The user's custom prompts           (highest priority)
  5. Output contract                     (JSON / plain text / plain text with a `Q:` headline line)

Everything about the candidate (resume, JD, notes, history, transcript) goes in the
user message as plain data via build_context_message(). The user message never
carries instructions, so nothing there can compete with the custom prompts.
"""
import logging
import re
import time
from typing import Any, Dict, List, Optional

from app.cache.redis import redis_cache

logger = logging.getLogger("roundmate.prompt_builder")

MODES = ("interview", "hr", "coding", "system_design", "screenshot", "introduction")

PERSONA = """You write the words a job candidate will say out loud in a live interview, right now. Write as the candidate, in the first person.

Ground rules:
- Only claim experience that shows up in the background you're given (resume, notes, prepared answers). If it doesn't cover something, say how you'd approach it rather than inventing a project, employer, or number.
- Answer the question that was actually asked. If it has several parts, cover every part.
- Put any code, SQL, shell commands, or diagrams in fenced code blocks with a language tag."""

LIVE_SPEECH_GUIDANCE = """The question comes from live speech-to-text, so treat its wording as approximate:
- It may be cut off, start mid-sentence, or be a fragment that continues the interviewer's previous turn (e.g. "helped you at your job" after "how has your experience..."). Use the conversation so far to rebuild the full question.
- Words may be misheard as similar-sounding ones, especially technical terms and names: "hoops" -> OOPS, "sequel" -> SQL, "cuber nets" -> Kubernetes, "wrecked hooks" -> React hooks, "Jason" -> JSON, "post grass" -> Postgres, "a sink" -> async. Pick the reading that makes sense for this role, the job description, and the candidate's resume.
- Filler, false starts, and repeated words ("so, um, can you, can you tell me") are noise; ignore them.
- If one turn holds several questions, answer the latest one, plus any earlier part it clearly depends on.
- Answer what the interviewer most plausibly meant. Never mention transcription, mishearing, or that the question was unclear."""

DEFAULT_STYLE = """How the answer should sound:
- Like a sharp, relaxed engineer talking across the table: plain words, contractions, mostly short sentences. It should be easy to read aloud on the first try.
- First sentence is the actual answer. No warm-up, and don't restate the question.
- Short spoken paragraphs. Only use a short list when the question has several separate parts, and never use bold headings or labels like "Situation:" / "Result:".
- Be specific. Name the real tool, the real decision, the real trade-off. One concrete detail from the candidate's background beats three general claims.
- Don't steer the answer toward the job description unless the question is about fit for the role.
- About 30–60 seconds of speech (roughly 80–150 words) unless the question clearly needs more."""

MODE_GUIDANCE = {
    "interview": "",
    "hr": (
        "This is an HR or behavioral question. Answer from the candidate's own experience, with one real example "
        "where there is one. For a story, say what was going on, what the candidate did, and how it turned out — "
        "told as a story, not as labelled sections. For strengths or weaknesses, be specific and honest."
    ),
    "coding": (
        "This is a coding or SQL question. Give the idea in a sentence, then the complete working code in a fenced "
        "block with brief comments, then a couple of sentences on why this approach and its time and space complexity."
    ),
    "system_design": (
        "This is a system design question. Open with the key requirements or assumptions in a sentence or two, "
        "draw a simple text diagram in a ```text block, then talk through the main components, how data flows, "
        "and the trade-offs worth calling out — the way you'd walk a panel through a whiteboard."
    ),
    "screenshot": (
        "You're given a screenshot from a technical interview or assessment (code, a diagram, a circuit, a network, "
        "a math problem, an MCQ, etc.). Work out exactly what is being asked and solve it. Expand acronyms the "
        "candidate might be asked about, explain how the thing works, show the steps of any calculation, and for "
        "code use the language shown in the screenshot and give a complete, correct solution. For MCQs, state the "
        "correct option and why."
    ),
    "introduction": (
        "You'll get the candidate's pre-written self-introduction. Tidy it into something that sounds natural when "
        "spoken: fix grammar, smooth the flow, cut stiff or generic phrases. Keep every fact (names, dates, "
        "technologies, employers, achievements) exactly as written — don't add, remove, or tailor anything to the job."
    ),
}

CUSTOM_PROMPTS_HEADER = (
    "The candidate's own instructions. These take priority: follow them over any style, format, length, or "
    "structure guidance above. The only rule that still applies is not inventing experience."
)

OUTPUT_CONTRACT = {
    "json": (
        'Reply with only a JSON object, no code fence: '
        '{"question": "<the interviewer\'s full question>", "answer": "<the answer>"}'
    ),
    "text": "Reply with only the answer itself — no preamble, no labels, no JSON.",
    # Web app: a clean question line it shows as the answer card's headline.
    "text_headline": (
        "Format: the very first line is `Q: ` followed by the interviewer's question as you understood it — "
        "one complete, clearly worded question (fragments rebuilt, misheard words fixed, under 20 words, "
        "no answer content). Then one blank line, then the answer itself, with no other preamble, labels, or JSON."
    ),
}

HEADLINE_PREFIX = "Q:"


def split_headline(text: str) -> tuple:
    """Split a `Q: <question>` first line off a headline-format answer. Returns (question or "", answer)."""
    stripped = (text or "").lstrip().lstrip("*#").lstrip()
    if stripped[:2].upper() != HEADLINE_PREFIX:
        return "", text or ""
    first, _, rest = stripped.partition("\n")
    return first[2:].strip().strip("*").strip(), rest.strip()

_EMPTY_VALUES = {"", "none", "none.", "none loaded", "none loaded.", "skipped for coding question."}


def _is_empty(value: Optional[str]) -> bool:
    return not value or value.strip().lower() in _EMPTY_VALUES


# ── Mode routing ────────────────────────────────────────────────────────────

def session_type_from_name(session_name: str) -> str:
    """The desktop app names sessions "<Company> (<Session Type>)", e.g. "Acme (HR / Manager Round)"."""
    match = re.search(r"\(([^()]*)\)\s*$", session_name or "")
    return match.group(1).strip() if match else ""


def _category_to_mode(category: str) -> str:
    c = (category or "").strip().lower()
    if not c or "interview+coding" in c:
        return ""  # mixed round: decide per question
    if "coding" in c or c in {"sql", "dsa"}:
        return "coding"
    if c.startswith("hr") or "manager" in c or "behavioral" in c:
        return "hr"
    if "design" in c or "architecture" in c:
        return "system_design"
    return ""


_CODING_PATTERNS = [
    r"\bwrite\b.{0,40}\b(query|queries|function|program|code|script|method|class|sql|algorithm)\b",
    r"\bimplement\b.{0,40}\b(function|algorithm|method|class|stack|queue|cache|lru)\b",
    r"\bleet ?code\b", r"\b(time|space) complexity\b", r"\bbig[- ]o\b",
    r"\b(delete|remove|find)\b.{0,20}\bduplicates?\b", r"\bnth highest\b", r"\bsecond highest\b",
    r"\breverse (a|an|the)\b", r"\btwo sum\b", r"\b(linked list|binary tree|binary search)\b",
    r"\bwhat (is|will be) the output\b", r"\bfix (this|the) (code|query|bug)\b",
]
_HR_PATTERNS = [
    r"\btell me about (yourself|a time)\b", r"\bintroduce yourself\b", r"\bwalk me through your\b",
    r"\byour (greatest |biggest )?(strengths?|weakness(es)?)\b", r"\bwhy (this|our) (company|role|team)\b",
    r"\bwhy should we hire\b", r"\bwhy do you want\b", r"\bwhy are you leaving\b", r"\bsalary\b",
    r"\bcompensation\b", r"\bnotice period\b", r"\bwhere do you see yourself\b", r"\bcareer goals?\b",
    r"\b(conflict|disagree(ment|d)?) with\b", r"\bdescribe a (situation|time)\b", r"\bbiggest (challenge|mistake|failure)\b",
]
_DESIGN_PATTERNS = [
    r"\bsystem design\b", r"\bdesign (a|an|the)\b", r"\bhow would you (design|architect|build|scale)\b",
    r"\bhigh[- ]level design\b", r"\blow[- ]level design\b", r"\barchitecture (for|of)\b",
    r"\brate limiter\b", r"\burl shortener\b", r"\bdistributed system\b",
]


def resolve_mode(question: str, session_type: str = "") -> str:
    """Pick the answer mode. The session type the user chose wins; otherwise match on the question."""
    forced = _category_to_mode(session_type)
    if forced:
        return forced

    q = (question or "").lower()
    if any(re.search(p, q) for p in _CODING_PATTERNS):
        return "coding"
    if any(re.search(p, q) for p in _DESIGN_PATTERNS):
        return "system_design"
    if any(re.search(p, q) for p in _HR_PATTERNS):
        return "hr"
    return "interview"


# ── Prompt assembly ─────────────────────────────────────────────────────────

def format_custom_prompts(custom_prompts: List[Dict[str, str]]) -> str:
    blocks = []
    for p in custom_prompts or []:
        content = (p.get("content") or "").strip()
        if not content:
            continue
        name = (p.get("name") or "").strip()
        blocks.append(f"[{name}]\n{content}" if name else content)
    return "\n\n".join(blocks)


def build_system_prompt(
    mode: str,
    custom_prompts: Optional[List[Dict[str, str]]] = None,
    output: str = "text",
    headline: bool = False,
) -> str:
    if mode not in MODES:
        mode = "interview"
    custom_text = format_custom_prompts(custom_prompts or [])

    sections = [PERSONA]
    # Screenshots and pre-written introductions aren't transcribed speech.
    if mode not in ("screenshot", "introduction"):
        sections.append(LIVE_SPEECH_GUIDANCE)
    sections.append(MODE_GUIDANCE.get(mode, ""))
    if not custom_text:
        sections.append(DEFAULT_STYLE)
    else:
        sections.append(f"{CUSTOM_PROMPTS_HEADER}\n\n{custom_text}")
    if output == "json":
        contract = OUTPUT_CONTRACT["json"]
    else:
        contract = OUTPUT_CONTRACT["text_headline" if headline else "text"]
    sections.append(contract)

    return "\n\n".join(s.strip() for s in sections if s and s.strip())


def build_context_message(
    question: str,
    role_info: str = "",
    jd_context: str = "",
    resume_context: str = "",
    knowledge_context: str = "",
    history: str = "",
    transcript: str = "",
) -> str:
    """User message: the candidate's background as plain data, then the question. No instructions."""
    sections = []
    if not _is_empty(role_info):
        sections.append(role_info.strip())
    for title, body in (
        ("Job description", jd_context),
        ("Candidate background (resume)", resume_context),
        ("Candidate's prepared answers and notes", knowledge_context),
        ("Already asked earlier in this interview (don't repeat these answers)", history),
        ("Conversation so far", transcript),
    ):
        if not _is_empty(body):
            sections.append(f"## {title}\n{body.strip()}")
    sections.append(f"## Question to answer now\n{(question or '').strip()}")
    return "\n\n".join(sections)


def build_introduction_message(introduction: str) -> str:
    return f"## Pre-written introduction\n{introduction.strip()}"


# ── Session state (used by the websocket pipeline / prewarm) ────────────────

class PromptBuilder:
    async def update_session_prompt(
        self,
        session_id: str,
        analysis: Dict[str, Any],
        context: Dict[str, Any],
        latest_transcript: str,
        user_preferences: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Store the latest transcript analysis + context in Redis session state.

        Prompts are no longer pre-assembled here; /answer builds them at request time
        so that custom prompts are always applied consistently.
        """
        session_state = await redis_cache.get_session_state(session_id) or {}
        session_state.pop("prepared_prompt", None)
        session_state.update({
            "session_id": session_id,
            "state": analysis.get("state", "WAITING"),
            "latest_transcript": latest_transcript,
            "prediction": analysis.get("prediction", ""),
            "prediction_confidence": analysis.get("confidence", 0.0),
            "intent": analysis.get("intent", "General Technical"),
            "category": analysis.get("category", "General Technical"),
            "difficulty": analysis.get("difficulty", "Medium"),
            "technologies": analysis.get("technologies", []),
            "keywords": analysis.get("keywords", []),
            "vector_query": analysis.get("vector_query", ""),
            "resume_context": context.get("resume_context", "None loaded."),
            "knowledge_context": context.get("knowledge_context", "None loaded."),
            "jd_context": context.get("jd_context", "None loaded."),
            "previous_context": context.get("previous_context", "None."),
            "reasoning_focus": context.get("reasoning_focus", "General technical review and validation."),
            "user_preferences": user_preferences or {},
            "last_updated": time.time(),
        })
        await redis_cache.set_session_state(session_id, session_state)
        return session_state


prompt_builder = PromptBuilder()
