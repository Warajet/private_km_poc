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

    # ── Discovery Engine ───────────────────────────────────────────────────────
    # Engine (Vertex AI Search App) that has all bucket datastores attached.
    # Required for multi-datastore answer_query() with DataStoreSpecs.
    # Create via: gcloud discovery-engine engines create ...
    # or in the GCP Console → Vertex AI Search → Apps.
    discovery_engine_engine_id: str

    # Serving config ID used on both the engine and per-datastore serving configs.
    discovery_engine_serving_config_id: str = "default_serving_config"

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
    # ── Derived path helpers ───────────────────────────────────────────────────
    @property
    def engine_serving_config_path(self) -> str:
        """
        Engine-level serving config used by answer_query() with DataStoreSpecs.
        The engine must have all bucket datastores attached in the GCP Console.
        """
        return (
            f"projects/{self.gcp_project_id}"
            f"/locations/{self.gcp_location}"
            f"/collections/default_collection"
            f"/engines/{self.discovery_engine_engine_id}"
            f"/servingConfigs/{self.discovery_engine_serving_config_id}"
        )

    def datastore_resource_name(self, datastore_id: str) -> str:
        """Full resource name for a datastore, used in DataStoreSpec.data_store."""
        return (
            f"projects/{self.gcp_project_id}"
            f"/locations/{self.gcp_location}"
            f"/collections/default_collection"
            f"/dataStores/{datastore_id}"
        )

    def engine_session_auto_path(self) -> str:
        """Auto-create session path for the first turn of a conversation."""
        return (
            f"projects/{self.gcp_project_id}"
            f"/locations/{self.gcp_location}"
            f"/collections/default_collection"
            f"/engines/{self.discovery_engine_engine_id}"
            f"/sessions/-"
        )

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
