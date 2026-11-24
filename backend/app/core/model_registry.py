import time
import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field

logger = logging.getLogger("roundmate.model_registry")

@dataclass
class ModelMetadata:
    id: str
    provider: str
    real_api_model: str
    display_name: str
    context_window: int = 128000
    supports_streaming: bool = True
    supports_vision: bool = False
    cost_per_m_in: float = 0.15
    cost_per_m_out: float = 0.60
    recommended_rank: int = 99

class ModelRegistry:
    """
    Centralized Model Registry and Granular Circuit Breaker.
    Tracks model-level and provider-level health, Retry-After cooldowns,
    and dynamically routes to the healthiest available alternative.
    """
    def __init__(self):
        self._registry: Dict[str, ModelMetadata] = {}
        self._model_cooldowns: Dict[str, float] = {}      # f"{provider}:{model}" -> cooldown timestamp
        self._provider_cooldowns: Dict[str, float] = {}   # provider -> cooldown timestamp
        self._model_failures: Dict[str, int] = {}
        self._provider_failures: Dict[str, int] = {}
        self._failure_threshold = 3
        self._default_cooldown_seconds = 30.0
        self._init_default_models()

    def _init_default_models(self):
        models = [
            ModelMetadata(
                id="gpt-6-astra",
                provider="openai",
                real_api_model="gpt-6.1-terra",
                display_name="GPT-6 Astra (Recommended #1)",
                context_window=128000,
                cost_per_m_in=2.50,
                cost_per_m_out=10.00,
                recommended_rank=1
            ),
            ModelMetadata(
                id="gemini-4-argon",
                provider="google",
                real_api_model="gemini-3.8-flash",
                display_name="Gemini 4 Argon (Recommended #2)",
                context_window=1000000,
                cost_per_m_in=0.075,
                cost_per_m_out=0.30,
                recommended_rank=2
            ),
            ModelMetadata(
                id="gemini-3.8-flash",
                provider="google",
                real_api_model="gemini-3.5-flash-lite",
                display_name="Gemini 3.8 Flash (Ultra-Fast)",
                context_window=1000000,
                cost_per_m_in=0.075,
                cost_per_m_out=0.30,
                recommended_rank=3
            ),
            ModelMetadata(
                id="claude-fable-5.1",
                provider="anthropic",
                real_api_model="claude-sonnet-5-5",
                display_name="Claude Fable 5.1 (High Reasoning)",
                context_window=200000,
                cost_per_m_in=3.00,
                cost_per_m_out=15.00,
                recommended_rank=4
            ),
            ModelMetadata(
                id="gpt-6.1-sol",
                provider="openai",
                real_api_model="gpt-6-luna",
                display_name="GPT-6.1 Sol",
                context_window=128000,
                cost_per_m_in=0.15,
                cost_per_m_out=0.60,
                recommended_rank=5
            ),
            ModelMetadata(
                id="claude-opus-5-5",
                provider="anthropic",
                real_api_model="claude-haiku-5-5",
                display_name="Claude Opus 5.5",
                context_window=200000,
                cost_per_m_in=0.25,
                cost_per_m_out=1.25,
                recommended_rank=6
            ),
            ModelMetadata(
                id="openai/gpt-oss-20b",
                provider="groq",
                real_api_model="openai/gpt-oss-120b",
                display_name="Groq LPU (GPT-OSS 120B)",
                context_window=128000,
                cost_per_m_in=0.10,
                cost_per_m_out=0.30,
                recommended_rank=7
            )
        ]
        for m in models:
            self._registry[m.id] = m

    def get_model(self, model_id: str) -> Optional[ModelMetadata]:
        return self._registry.get(model_id)

    def is_model_healthy(self, provider: str, model_id: str) -> bool:
        """
        Checks if a model or its upstream provider is currently healthy.
        Separates model-specific quota rate-limits from whole-provider outages.
        """
        # 1. Check if the entire provider is cooling down (e.g. 503 outage or 401 auth failure)
        prov_cooldown = self._provider_cooldowns.get(provider, 0.0)
        if time.time() < prov_cooldown:
            logger.debug(f"[CircuitBreaker] Entire provider '{provider}' is cooling down ({prov_cooldown - time.time():.1f}s remaining)")
            return False

        # 2. Check if this specific model is cooling down (e.g. model-specific 429 Retry-After)
        model_key = f"{provider}:{model_id}"
        model_cooldown = self._model_cooldowns.get(model_key, 0.0)
        if time.time() < model_cooldown:
            logger.debug(f"[CircuitBreaker] Model '{model_key}' is cooling down ({model_cooldown - time.time():.1f}s remaining)")
            return False

        return True

    def record_success(self, provider: str, model_id: str):
        """Resets failure counts on successful execution."""
        model_key = f"{provider}:{model_id}"
        self._model_failures[model_key] = 0
        self._provider_failures[provider] = 0
        self._model_cooldowns.pop(model_key, None)

    def record_failure(
        self,
        provider: str,
        model_id: str,
        error_code: int = 500,
        retry_after: Optional[float] = None
    ):
        """
        Categorized failure handler:
        - 429: Model-specific quota rate limit -> cools down ONLY this model. Other models on this provider stay healthy.
        - 401: Invalid API credentials -> cools down the entire provider.
        - 5xx: Provider outage / server error -> trips provider circuit breaker after repeated threshold.
        """
        model_key = f"{provider}:{model_id}"
        cooldown_duration = retry_after if retry_after else self._default_cooldown_seconds

        if error_code == 429:
            # Model-specific quota or rate-limit: cool down ONLY this model
            self._model_cooldowns[model_key] = time.time() + cooldown_duration
            logger.warning(
                f"[CircuitBreaker] Model-level 429 rate limit on '{model_key}'. "
                f"Isolated cooldown for {cooldown_duration}s. Other '{provider}' models remain healthy."
            )
        elif error_code == 401:
            # Authentication failure: disable provider
            self._provider_cooldowns[provider] = time.time() + 86400.0
            logger.error(f"[CircuitBreaker] 401 Unauthorized for provider '{provider}'. Disabled for 24h.")
        else:
            # 5xx Server Error or Connection Timeout: track provider-level failures
            prov_fails = self._provider_failures.get(provider, 0) + 1
            self._provider_failures[provider] = prov_fails
            if prov_fails >= self._failure_threshold:
                self._provider_cooldowns[provider] = time.time() + cooldown_duration
                logger.error(
                    f"[CircuitBreaker] Provider-wide circuit TRIPPED for '{provider}' after {prov_fails} consecutive errors. "
                    f"All models under '{provider}' cooling down for {cooldown_duration}s."
                )

    def get_healthiest_alternative(
        self,
        current_model_id: str,
        task_type: str = "live_answer"
    ) -> str:
        """
        Dynamically finds the healthiest available fallback model matching the task,
        skipping all models currently under cooldown.
        """
        candidates = sorted(self._registry.values(), key=lambda x: x.recommended_rank)
        for cand in candidates:
            if cand.id == current_model_id:
                continue
            if self.is_model_healthy(cand.provider, cand.real_api_model):
                return cand.real_api_model
        # If all cooled down, return zero-budget flash
        return "gemini-3.8-flash"

model_registry = ModelRegistry()
