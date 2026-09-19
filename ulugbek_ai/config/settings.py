"""Typed application settings, loaded from the environment / ``.env``.

Secrets are *only* ever read from the environment — never hardcoded, never
committed. ``ANTHROPIC_API_KEY`` is stored as a ``SecretStr`` so that an
accidental ``repr()`` of the settings object cannot leak it.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ulugbek_ai.core.enums import PermissionLevel, PermissionMode

Environment = Literal["local", "test", "staging", "production"]


class Settings(BaseSettings):
    """All runtime configuration in one place."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application ------------------------------------------------------- #
    app_name: str = "Ulugbek AI"
    environment: Environment = "local"
    debug: bool = False
    log_level: str = "INFO"
    log_json: bool = False

    # --- API --------------------------------------------------------------- #
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_prefix: str = "/api"
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])

    # --- Database ---------------------------------------------------------- #
    database_url: str = "postgresql+asyncpg://ulugbek:ulugbek@localhost:5432/ulugbek_ai"
    db_echo: bool = False
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # --- Claude ------------------------------------------------------------ #
    anthropic_api_key: SecretStr | None = None
    #: Required only for a key that spans several workspaces — such a key runs
    #: in the workspace named by each request, so without this the API rejects
    #: the call. A key scoped to a single workspace needs nothing here.
    anthropic_workspace_id: str | None = None
    claude_model: str = "claude-opus-5"
    claude_max_tokens: Annotated[int, Field(ge=256, le=128_000)] = 16_000
    claude_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    claude_thinking: bool = True
    claude_timeout_seconds: Annotated[float, Field(gt=0)] = 120.0
    claude_max_retries: Annotated[int, Field(ge=0, le=10)] = 2

    # --- GitHub integration ------------------------------------------------ #
    #: A fine-grained PAT or an installation token. Read from the environment
    #: only; never written to a log, an audit row or an API response.
    github_token: SecretStr | None = None
    github_api_url: str = "https://api.github.com"
    github_timeout_seconds: Annotated[float, Field(gt=0)] = 20.0

    # --- Railway integration ----------------------------------------------- #
    #: An account, workspace or project token. Read from the environment only;
    #: never written to a log, an audit row or an API response.
    railway_token: SecretStr | None = None
    #: Project tokens authenticate with their own header rather than a bearer,
    #: so the kind has to be declared — the two are not interchangeable.
    railway_token_kind: Literal["account", "project"] = "account"
    railway_api_url: str = "https://backboard.railway.com/graphql/v2"
    railway_timeout_seconds: Annotated[float, Field(gt=0)] = 30.0
    #: Default ids, so a command does not have to repeat them. A project's own
    #: Railway binding overrides these; an explicit argument overrides both.
    railway_project_id: str | None = None
    railway_environment_id: str | None = None
    railway_service_id: str | None = None
    #: How long a deploy waits for Railway to reach a terminal status before
    #: reporting what it saw. Longer than this is reported as still running.
    railway_deploy_wait_seconds: Annotated[float, Field(ge=0, le=300)] = 90.0

    # --- Agent loop -------------------------------------------------------- #
    agent_max_iterations: Annotated[int, Field(ge=1, le=100)] = 12
    agent_max_replans: Annotated[int, Field(ge=0, le=20)] = 2
    agent_run_timeout_seconds: Annotated[float, Field(gt=0)] = 300.0
    agent_verification_enabled: bool = True

    # --- Tools ------------------------------------------------------------- #
    tool_default_timeout_seconds: Annotated[float, Field(gt=0)] = 30.0
    tool_max_result_chars: Annotated[int, Field(ge=256)] = 8_000

    # --- Memory ------------------------------------------------------------ #
    memory_context_limit: Annotated[int, Field(ge=1, le=200)] = 20
    memory_context_max_chars: Annotated[int, Field(ge=256)] = 12_000

    # --- Permission policy -------------------------------------------------- #
    permission_read: PermissionMode = PermissionMode.AUTO
    permission_write: PermissionMode = PermissionMode.AUTO
    permission_execute: PermissionMode = PermissionMode.APPROVAL
    permission_delete: PermissionMode = PermissionMode.APPROVAL
    permission_critical: PermissionMode = PermissionMode.APPROVAL

    # ---------------------------------------------------------------- helpers #
    @field_validator("database_url")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        """Upgrade sync Postgres URLs to the asyncpg driver.

        Railway (and most managed providers) inject ``postgres://...``, which
        SQLAlchemy's async engine cannot use directly.
        """
        if value.startswith("postgres://"):
            return "postgresql+asyncpg://" + value[len("postgres://") :]
        if value.startswith("postgresql://"):
            return "postgresql+asyncpg://" + value[len("postgresql://") :]
        return value

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        level = value.upper()
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}
        if level not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}")
        return level

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def sync_database_url(self) -> str:
        """Driver-less URL for tooling that needs a synchronous connection."""
        return self.database_url.replace("+asyncpg", "").replace("+aiosqlite", "")

    def permission_policy(self) -> dict[PermissionLevel, PermissionMode]:
        """Configured mode per permission level.

        ``CRITICAL`` is deliberately not configurable downward: a critical action
        always requires an explicit human approval.
        """
        return {
            PermissionLevel.READ: self.permission_read,
            PermissionLevel.WRITE: self.permission_write,
            PermissionLevel.EXECUTE: self.permission_execute,
            PermissionLevel.DELETE: self.permission_delete,
            PermissionLevel.CRITICAL: (
                PermissionMode.DENY
                if self.permission_critical is PermissionMode.DENY
                else PermissionMode.APPROVAL
            ),
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton (cached; override in tests via DI)."""
    return Settings()
