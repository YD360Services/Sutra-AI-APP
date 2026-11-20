"""
Live answers: the prompt must handle fuzzy speech-to-text (fragments, misheard tech terms), and the
web app can ask for a `Q: <clean question>` first line to show as the answer card headline instead
of a raw fragment like "Helped you at your job." The desktop app doesn't send `headline`, so its
stream format is unchanged.
"""
from app.services.prompt_builder import LIVE_SPEECH_GUIDANCE, build_system_prompt, split_headline


def test_live_speech_guidance_for_spoken_modes_only():
    for mode in ("interview", "hr", "coding", "system_design"):
        assert LIVE_SPEECH_GUIDANCE in build_system_prompt(mode)
    for mode in ("screenshot", "introduction"):
        assert LIVE_SPEECH_GUIDANCE not in build_system_prompt(mode)


def test_live_speech_guidance_survives_custom_prompts():
    prompt = build_system_prompt("interview", [{"name": "Style", "content": "Answer in bullets."}])
    assert LIVE_SPEECH_GUIDANCE in prompt and "Answer in bullets." in prompt


def test_headline_contract_is_opt_in():
    assert "`Q: `" not in build_system_prompt("interview")
    assert "`Q: `" in build_system_prompt("interview", headline=True)
    # JSON (screenshots) already carries a question field; headline doesn't apply.
    assert "`Q: `" not in build_system_prompt("screenshot", output="json", headline=True)


def test_split_headline():
    q, a = split_headline("Q: How has your full-stack experience helped you at your job?\n\nIt taught me to...")
    assert q == "How has your full-stack experience helped you at your job?"
    assert a == "It taught me to..."
    # Models sometimes bold or heading-mark the line.
    assert split_headline("**Q: What is a closure?**\n\nA closure is...")[0] == "What is a closure?"
    # No headline: everything is the answer.
    assert split_headline("Quick answer: closures capture scope.") == ("", "Quick answer: closures capture scope.")
