"""
The only module that talks to a model provider.

LLMClient is a small interface: extract() returns structured data, generate() returns text.
There are two adapters:
- AnthropicClient: the Anthropic Messages API.
- OpenAICompatibleClient: the OpenAI Chat Completions API. OpenAI, OpenRouter, and many other
  providers accept it; only the base URL changes (see PROVIDERS in config.py).
"""

import json
import re
from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .config import PROVIDERS, Settings

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """The model call failed, or the output is not usable."""


class LLMClient(ABC):
    @abstractmethod
    def extract(self, system: str, messages: list[dict], schema: type[T]) -> T:
        """Return an object of the Pydantic class `schema`. Raise LLMError on failure."""

    @abstractmethod
    def generate(self, system: str, messages: list[dict]) -> str:
        """Return a natural-language reply. Raise LLMError on failure."""


class AnthropicClient(LLMClient):
    """
    Anthropic (Claude) adapter. Structured output: messages.parse(output_format=Schema).
    "effort" is low, because chat turns must be fast. Haiku models do not accept it.
    """

    MAX_TOKENS = 16000

    def __init__(self, api_key: str | None, model: str, effort: str = "low") -> None:
        import anthropic  # Here, so other adapters do not need this package.

        self._anthropic = anthropic
        try:
            # api_key=None: the SDK reads ANTHROPIC_API_KEY.
            self.client = anthropic.Anthropic(api_key=api_key)
        except anthropic.AnthropicError as error:
            raise LLMError(f"Cannot create the Anthropic client: {error}") from error
        self.model = model
        self.extra = {} if "haiku" in model else {"output_config": {"effort": effort}}

    def _check_stop(self, response) -> None:
        if response.stop_reason in ("refusal", "max_tokens"):
            raise LLMError(f"The model stopped early: {response.stop_reason}")

    def extract(self, system: str, messages: list[dict], schema: type[T]) -> T:
        try:
            response = self.client.messages.parse(
                model=self.model,
                max_tokens=self.MAX_TOKENS,
                system=system,
                messages=messages,
                output_format=schema,
                **self.extra,
            )
        except self._anthropic.APIError as error:
            raise LLMError(f"API error: {error}") from error
        self._check_stop(response)
        if response.parsed_output is None:
            raise LLMError("The model output does not match the schema.")
        return response.parsed_output

    def generate(self, system: str, messages: list[dict]) -> str:
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.MAX_TOKENS,
                system=system,
                messages=messages,
                **self.extra,
            )
        except self._anthropic.APIError as error:
            raise LLMError(f"API error: {error}") from error
        self._check_stop(response)
        return "".join(block.text for block in response.content if block.type == "text").strip()


class OpenAICompatibleClient(LLMClient):
    """
    Adapter for the OpenAI Chat Completions API (OpenAI, OpenRouter, and other compatible servers).
    Structured output: chat.completions.parse(response_format=Schema). If the model or server does
    not support it, the fallback asks for JSON in the prompt and checks it with Pydantic (one retry).
    """

    MAX_TOKENS = 16000

    def __init__(self, api_key: str | None, model: str, base_url: str | None = None) -> None:
        import openai  # Here, so other adapters do not need this package.

        self._openai = openai
        try:
            self.client = openai.OpenAI(api_key=api_key, base_url=base_url)
        except openai.OpenAIError as error:
            raise LLMError(f"Cannot create the OpenAI-compatible client: {error}") from error
        self.model = model

    def _call(self, method, system: str, messages: list[dict], **kwargs):
        try:
            completion = method(
                model=self.model,
                max_completion_tokens=self.MAX_TOKENS,
                messages=[{"role": "system", "content": system}, *messages],
                **kwargs,
            )
        except self._openai.APIError as error:
            raise LLMError(f"API error: {error}") from error
        if not completion.choices:
            raise LLMError("The model returned no answer.")
        choice = completion.choices[0]
        if choice.finish_reason == "length":
            raise LLMError("The model stopped early: length")
        return choice.message

    def extract(self, system: str, messages: list[dict], schema: type[T]) -> T:
        try:
            message = self._call(self.client.chat.completions.parse, system, messages, response_format=schema)
        except (LLMError, ValidationError, self._openai.LengthFinishReasonError):
            return self._extract_with_prompt(system, messages, schema)
        if getattr(message, "refusal", None):
            raise LLMError(f"The model refused: {message.refusal}")
        return message.parsed if message.parsed is not None else self._extract_with_prompt(system, messages, schema)

    def _extract_with_prompt(self, system: str, messages: list[dict], schema: type[T]) -> T:
        """Fallback: ask for JSON in the prompt, then validate it with Pydantic."""
        system = (
            f"{system}\n\nReply with ONLY one JSON object (no other text) that matches this JSON schema:\n"
            f"{json.dumps(schema.model_json_schema())}"
        )
        error_note = ""
        for _ in range(2):
            message = self._call(self.client.chat.completions.create, system + error_note, messages)
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (message.content or "").strip())
            try:
                return schema.model_validate_json(text)
            except ValidationError as error:
                error_note = f"\n\nYour last answer was not valid: {error.errors()[:3]}. Try again."
        raise LLMError("The model output does not match the schema.")

    def generate(self, system: str, messages: list[dict]) -> str:
        message = self._call(self.client.chat.completions.create, system, messages)
        return (message.content or "").strip()


def make_llm_client(settings: Settings) -> LLMClient:
    """Create the adapter for settings.llm_provider."""
    preset = PROVIDERS.get(settings.llm_provider)
    if preset is None:
        raise ValueError(f"Unknown LLM_PROVIDER '{settings.llm_provider}'. Supported: {', '.join(PROVIDERS)}")
    if preset.adapter == "anthropic":
        return AnthropicClient(settings.llm_api_key, settings.llm_model)
    return OpenAICompatibleClient(settings.llm_api_key, settings.llm_model, settings.llm_base_url or preset.base_url)
