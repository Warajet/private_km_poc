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
    gcp_location: str = "global"

    # ── Discovery Engine / Vertex AI Search ────────────────────────────────────
    # Format: projects/{project}/locations/{location}/collections/{collection}/dataStores/{datastore}
    discovery_engine_datastore_id: str
    discovery_engine_serving_config_id: str = "default_config"

    # ── Gemini Enterprise ──────────────────────────────────────────────────────
    # Leave empty to use the datastore's default model
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

    # ── Authorization ──────────────────────────────────────────────────────────
    # Internal email pattern for department-level impersonation.
    # {department}@{workspace_domain}  →  e.g. A@hello.org
    department_email_template: str = "{department}@{workspace_domain}"

    # Group email templates used in Discovery Engine ACL
    relate_group_email_template: str = "relate-{relate_id}@{workspace_domain}"
    jd_group_email_template: str = "jd-{jd_code}@{workspace_domain}"

    # ── JWT for Frontend ↔ Backend communication ───────────────────────────────
    jwt_secret: str = "change-this-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expiry_minutes: int = 60

    # ── User ↔ Department Mapping (override via JSON file for large orgs) ───────
    # Maps "userA@hello.org" → {"department": "A", "jd_code": "ENG001"}
    # In production, load this from a DB or Google Directory instead.
    user_mapping_file: Optional[str] = None  # path to JSON mapping file

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

    @property
    def serving_config_path(self) -> str:
        return (
            f"projects/{self.gcp_project_id}"
            f"/locations/{self.gcp_location}"
            f"/collections/default_collection"
            f"/dataStores/{self.discovery_engine_datastore_id}"
            f"/servingConfigs/{self.discovery_engine_serving_config_id}"
        )

    @property
    def datastore_path(self) -> str:
        return (
            f"projects/{self.gcp_project_id}"
            f"/locations/{self.gcp_location}"
            f"/collections/default_collection"
            f"/dataStores/{self.discovery_engine_datastore_id}"
        )


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
