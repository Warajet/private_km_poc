"""
Application configuration loaded from environment variables.
All sensitive values come from the environment; defaults suit local dev only.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(str, Enum):
    LOCAL = "local"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ────────────────────────────────────────────────────────────
    app_name: str = "Private KM Chatbot API"
    app_version: str = "1.0.0"
    environment: Environment = Environment.LOCAL
    debug: bool = False
    log_level: str = "INFO"

    # ── Server ─────────────────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000
    allowed_origins: List[str] = Field(default=["*"])

    # ── GCP Core ───────────────────────────────────────────────────────────────
    gcp_project_id: str
    # Use "global" for Discovery Engine; GeminiService maps "global" → "us-central1"
    # for Vertex AI which requires a regional location.
    gcp_location: str = "global"
    # Vertex AI region used by GeminiService (separate from DE location).
    vertex_ai_location: str = "us-central1"

    # ── Discovery Engine ───────────────────────────────────────────────────────
    # Serving config ID is shared across all buckets (each datastore gets its own).
    discovery_engine_serving_config_id: str = "default_config"

    # ── Datastore Registry ─────────────────────────────────────────────────────
    # Path to a JSON file that maps bucket type → datastore ID(s).
    # See scripts/datastore_registry.json for the schema.
    #
    # {
    #   "public":       "public-datastore-id",
    #   "internal":     { "A": "internal-dept-a-id", "B": "internal-dept-b-id" },
    #   "relate":       { "ab": "relate-ab-id", "abc": "relate-abc-id" },
    #   "confidential": "confidential-datastore-id"
    # }
    datastore_registry_file: Optional[str] = None

    # ── Gemini Enterprise ──────────────────────────────────────────────────────
    gemini_model: str = "gemini-2.0-flash-001"
    gemini_system_prompt: str = (
        "You are a helpful enterprise knowledge assistant. "
        "Answer questions accurately using only the retrieved documents. "
        "If the information is not available, say so clearly."
    )

    # ── Authentication ─────────────────────────────────────────────────────────
    # Path to the service account key JSON (local dev). On Cloud Run / GKE
    # leave empty and rely on ADC / Workload Identity.
    google_application_credentials: Optional[str] = None

    # Google Workspace domain for DWD (e.g. "hello.org")
    workspace_domain: str = "hello.org"

    # SA must have DWD granted for this scope in Google Workspace Admin
    dwd_scopes: List[str] = Field(default=[
        "https://www.googleapis.com/auth/cloud-platform",
    ])

    # ── Session ────────────────────────────────────────────────────────────────
    # "memory" for in-process dict (dev); "redis" for production
    session_backend: str = "memory"
    redis_url: Optional[str] = None
    session_ttl_seconds: int = 3600

    # ── Authorization email templates ──────────────────────────────────────────
    # {department}@{workspace_domain}  →  e.g. A@hello.org
    department_email_template: str = "{department}@{workspace_domain}"
    # GWS group emails used in acl_info (set during document ingestion)
    relate_group_email_template: str = "relate-{relate_id}@{workspace_domain}"
    jd_group_email_template: str = "jd-{jd_code}@{workspace_domain}"

    # ── JWT for Frontend ↔ Backend ─────────────────────────────────────────────
    jwt_secret: str = "change-this-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expiry_minutes: int = 60

    # ── User ↔ Department Mapping ───────────────────────────────────────────────
    # Maps HWC email → {"department": "A", "jd_code": "ENG001", ...}
    user_mapping_file: Optional[str] = None

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_origins(cls, v):
        if isinstance(v, str):
            return [o.strip() for o in v.split(",")]
        return v

    @field_validator("dwd_scopes", mode="before")
    @classmethod
    def parse_scopes(cls, v):
        if isinstance(v, str):
            return [s.strip() for s in v.split(",")]
        return v

    # ── Derived helpers ────────────────────────────────────────────────────────
    def department_email(self, department: str) -> str:
        return self.department_email_template.format(
            department=department.upper(),
            workspace_domain=self.workspace_domain,
        )

    def relate_group_email(self, relate_id: str) -> str:
        return self.relate_group_email_template.format(
            relate_id=relate_id.lower(),
            workspace_domain=self.workspace_domain,
        )

    def jd_group_email(self, jd_code: str) -> str:
        return self.jd_group_email_template.format(
            jd_code=jd_code.lower(),
            workspace_domain=self.workspace_domain,
        )


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
