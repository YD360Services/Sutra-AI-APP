"""
LLD v1.3 Items 32 & 33: Interview Intelligence Source Governance & Anonymized Aggregation.
- Private sessions NEVER enter shared intelligence automatically.
- Requires explicit opt_in consent, tracks permission basis & deletion linkage.
- Strips PII before aggregating anonymized patterns labeled as RoundMate dataset observations.
"""
import re
import time
from typing import Any, Dict, List, Optional


class InterviewIntelligenceGovernance:
    """Enforces allowed-source rules, opt-in consent, and withdrawal/deletion linkage."""

    ALLOWED_SOURCE_TYPES = {"consented_session", "curated_public_bank", "user_contributed"}

    @classmethod
    def is_eligible_for_shared_intelligence(
        cls,
        source_type: str,
        opt_in_consent: bool,
        withdrawn: bool = False,
    ) -> Dict[str, Any]:
        if withdrawn:
            return {"allowed": False, "reason": "consent_withdrawn"}
        if source_type == "private_session" and not opt_in_consent:
            return {"allowed": False, "reason": "private_session_requires_explicit_opt_in"}
        if source_type not in cls.ALLOWED_SOURCE_TYPES and not opt_in_consent:
            return {"allowed": False, "reason": "unsupported_source_type"}
        if not opt_in_consent and source_type != "curated_public_bank":
            return {"allowed": False, "reason": "missing_opt_in_consent"}
        return {"allowed": True, "reason": "permitted"}


class InterviewPatternAggregator:
    """Extracts anonymized, structured question patterns without republishing raw source text."""

    _EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
    _PHONE_RE = re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")

    @classmethod
    def anonymize_text(cls, text: str) -> str:
        clean = cls._EMAIL_RE.sub("[REDACTED_EMAIL]", text)
        clean = cls._PHONE_RE.sub("[REDACTED_PHONE]", clean)
        return clean.strip()

    @classmethod
    def aggregate_patterns(
        cls,
        records: List[Dict[str, Any]],
        company: Optional[str] = None,
        role: Optional[str] = None,
        stage: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Aggregates permitted records into structured pattern counts.
        Explicitly labels observations as RoundMate dataset observations, not official company statistics.
        """
        pattern_counts: Dict[str, int] = {}
        eligible_count = 0

        for rec in records:
            gov = InterviewIntelligenceGovernance.is_eligible_for_shared_intelligence(
                source_type=rec.get("source_type", "private_session"),
                opt_in_consent=bool(rec.get("opt_in_consent", False)),
                withdrawn=bool(rec.get("withdrawn", False)),
            )
            if not gov["allowed"]:
                continue

            eligible_count += 1
            topic = cls.anonymize_text(rec.get("topic") or rec.get("category") or "General")
            pattern_counts[topic] = pattern_counts.get(topic, 0) + 1

        return {
            "label": "RoundMate dataset observation (not official company statistics)",
            "company_segment": company or "all",
            "role_segment": role or "all",
            "stage_segment": stage or "all",
            "source_count": eligible_count,
            "pattern_frequencies": pattern_counts,
            "freshness_ts": time.time(),
        }
