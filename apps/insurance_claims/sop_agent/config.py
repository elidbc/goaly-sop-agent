"""
Settings for the SOP agent. All magic numbers and paths live here.

Values come from environment variables (or the .env file), with defaults.
The AI model is provider-agnostic: LLM_PROVIDER picks a preset from PROVIDERS,
LLM_MODEL overrides the preset model, and the API key comes from LLM_API_KEY
or from the provider's own key variable (for example ANTHROPIC_API_KEY or OR_API_KEY).
"""

import os
from dataclasses import dataclass, replace
from pathlib import Path

from dotenv import load_dotenv

PACKAGE_DIR = Path(__file__).resolve().parent
APP_DIR = PACKAGE_DIR.parent


@dataclass(frozen=True)
class Provider:
    adapter: str                   # "anthropic" or "openai_compatible" (see llm_client.py)
    key_env: tuple[str, ...]       # Environment variables that can hold the key, in order.
    default_model: str
    base_url: str | None = None


# To add an OpenAI-compatible provider (Groq, Mistral, a local Ollama server, ...), add one line here.
PROVIDERS: dict[str, Provider] = {
    "anthropic": Provider("anthropic", ("ANTHROPIC_API_KEY",), "claude-sonnet-5"),
    "openrouter": Provider(
        "openai_compatible", ("OPENROUTER_API_KEY", "OR_API_KEY"), "openai/gpt-6-luna", "https://openrouter.ai/api/v1"
    ),
    "openai": Provider("openai_compatible", ("OPENAI_API_KEY",), "gpt-5-mini"),
}


@dataclass
class Settings:
    """All the values that control the agent. One object for the full app."""

    # --- AI model (provider-agnostic) ---
    llm_provider: str = "anthropic"
    llm_model: str = PROVIDERS["anthropic"].default_model
    llm_api_key: str | None = None
    llm_base_url: str | None = None    # Overrides the preset base URL (any OpenAI-compatible server).

    # --- Data ---
    # The folder with the JSON fixtures (the mock company database).
    fixtures_dir: Path = APP_DIR / "fixtures"
    # The folder where the mock email service writes the "sent" emails.
    outbox_dir: Path = APP_DIR / "outbox"

    # --- Demo clock ---
    # The date that the agent uses as "today".
    # The fixture deadlines are in early 2026.
    # This date makes the demo more realistic and cover more cases.
    demo_today: str = "2026-02-15"

    # --- SOP limits ---
    # The number of identity fields that must match before we trust the caller.
    required_identity_matches: int = 3
    # The number of out-of-scope messages before the agent suggests a human representative.
    max_off_topic_strikes: int = 3
    # The number of times we check the consent status before we stop.
    max_consent_polls: int = 5
    # The consent scenario to use from consent_scenarios.json ("default" or "timeout").
    consent_scenario: str = "default"


def with_model(settings: Settings, provider: str, model: str | None = None, api_key: str | None = None) -> Settings:
    """
    Copy of `settings` for another provider/model/key (the UI uses this).
    Empty values fall back to the provider preset and the provider's key variable.
    """
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown LLM_PROVIDER '{provider}'. Supported: {', '.join(PROVIDERS)}")
    preset = PROVIDERS[provider]
    same_provider = provider == settings.llm_provider
    key = api_key or (settings.llm_api_key if same_provider else None)
    key = key or next((os.getenv(name) for name in preset.key_env if os.getenv(name)), None)
    return replace(
        settings,
        llm_provider=provider,
        llm_model=model or (settings.llm_model if same_provider else preset.default_model),
        llm_api_key=key,
        llm_base_url=settings.llm_base_url if same_provider else None,
    )


def load_settings(env_file: Path | None = None) -> Settings:
    """Load .env (the real environment wins), then read the settings."""
    load_dotenv(env_file or APP_DIR / ".env", override=False)
    provider = os.getenv("LLM_PROVIDER", "anthropic")
    base = Settings(
        llm_provider=provider,
        llm_model=os.getenv("LLM_MODEL") or PROVIDERS.get(provider, PROVIDERS["anthropic"]).default_model,
        llm_api_key=os.getenv("LLM_API_KEY") or None,
        llm_base_url=os.getenv("LLM_BASE_URL") or None,
        demo_today=os.getenv("DEMO_TODAY", Settings.demo_today),
        consent_scenario=os.getenv("CONSENT_SCENARIO", Settings.consent_scenario),
    )
    return with_model(base, provider, base.llm_model, base.llm_api_key)
