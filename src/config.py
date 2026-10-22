"""Configuration module using Pydantic Settings."""

from typing import Literal
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    """Global configuration settings for Balatro LLM Agent."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Bridge Connection
    BRIDGE_HOST: str = Field(default="127.0.0.1", description="Balatro Lua RPC Bridge host")
    BRIDGE_PORT: int = Field(default=12346, description="Balatro Lua RPC Bridge port")
    BRIDGE_TIMEOUT: float = Field(default=5.0, description="Socket/HTTP timeout in seconds")
    BRIDGE_MAX_RETRIES: int = Field(default=2, description="Maximum quick socket retries per connection attempt")
    BRIDGE_RETRY_DELAY: float = Field(default=1.0, description="Seconds between quick socket retries")
    BRIDGE_MAX_DISCONNECT_FAILURES: int = Field(
        default=5,
        description="Maximum consecutive bridge reconnection cycles before auto-shutdown (phone closed)",
    )
    BRIDGE_MAX_DISCONNECT_SECONDS: float = Field(
        default=300.0,
        description="Maximum total seconds to wait for bridge reconnection before auto-shutdown (default 5 min)",
    )

    # LLM Settings
    PROVIDER: Literal["openai", "anthropic", "openrouter", "custom"] = Field(
        default="openrouter",
        description="LLM provider: openai, anthropic, openrouter, or custom",
    )
    MODEL: str = Field(
        default="gpt-4o",
        description="Model name (e.g. gpt-4o, claude-3-5-sonnet-20241022, free/gpt-6-luna)",
    )
    TEMPERATURE: float = Field(default=0.2, description="Sampling temperature for LLM actions")

    # API Keys & URLs
    OPENAI_API_KEY: str | None = Field(default=None, description="OpenAI API Key")
    OPENAI_BASE_URL: str | None = Field(default=None, description="Custom OpenAI Base URL or proxy")
    OPENROUTER_API_KEY: str | None = Field(default=None, description="OpenRouter API Key")
    OPENROUTER_BASE_URL: str = Field(default="https://openrouter.ai/api/v1", description="OpenRouter Base URL")
    ANTHROPIC_API_KEY: str | None = Field(default=None, description="Anthropic API Key")
    ANTHROPIC_BASE_URL: str | None = Field(default=None, description="Custom Anthropic Base URL")

    # Custom OpenAI-compatible Provider (e.g. Apinex, Groq, Ollama, DeepSeek)
    CUSTOM_API_KEY: str | None = Field(default=None, description="Custom OpenAI-compatible API Key")
    CUSTOM_BASE_URL: str | None = Field(default=None, description="Custom OpenAI-compatible Base URL")
    REASONING_EFFORT: str | None = Field(
        default=None,
        description="Reasoning / thinking effort level: low, medium, high",
    )
    LLM_TIMEOUT: float = Field(
        default=175.0,
        description="HTTP timeout in seconds for LLM reasoning API calls",
    )
    MAX_TOKENS: int | None = Field(
        default=None,
        description="Optional max completion tokens limit (None = unconstrained)",
    )
    MAX_CONSECUTIVE_LLM_ERRORS: int = Field(
        default=3,
        description="Maximum consecutive LLM errors/timeouts before exiting for supervisor restart",
    )
    MAX_CALCULATION_TURNS: int = Field(
        default=8,
        description="Maximum local calculation/simulation rounds per decision turn before forcing a game action (x2.5 limit for deep calculations)",
    )

    # Game Loop Timing
    POLL_INTERVAL: float = Field(default=1.0, description="Seconds between state polling cycles")
    ANIMATION_DELAY: float = Field(default=1.5, description="Wait seconds when game is animating")
    ACTION_COOLDOWN: float = Field(default=1.0, description="Wait seconds after executing an action")
    CASH_OUT_DELAY: float = Field(
        default=3.0,
        description="Wait seconds on round evaluation screen before bot automatically presses Cash Out if player doesn't",
    )

    # Agent Behavior
    DRY_RUN: bool = Field(default=False, description="If True, prompt LLM but don't dispatch RPC actions")
    LOG_LEVEL: str = Field(default="INFO", description="Console log level")
    VERBOSE_COT: bool = Field(default=True, description="Print detailed Chain-of-Thought reasoning")


settings = AgentSettings()
