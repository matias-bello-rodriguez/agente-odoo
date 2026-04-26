from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Primero plantilla (.env.example), luego secretos locales (.env).
        # El segundo archivo sobrescribe al primero para que OPENAI_API_KEY en .env gane siempre.
        env_file=(".env.example", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    odoo_url: str = Field(default="http://127.0.0.1:8069")
    odoo_db: str = Field(default="odoo")
    odoo_username: str = Field(default="admin")
    odoo_password: str = Field(default="admin")

    openai_api_key: str | None = Field(default=None)
    openai_llm_model: str = Field(default="gpt-4o-mini")
    openai_embed_model: str = Field(default="text-embedding-3-small")

    odoo_rag_record_limit: int = Field(default=400, ge=1, le=5000)
    odoo_rag_storage_dir: Path = Field(default=Path("storage"))

    odoo_rag_web_host: str = Field(default="127.0.0.1")
    odoo_rag_web_port: int = Field(default=8787, ge=1, le=65535)

    # --- Caché (memoria local + opcional Redis) ----------------------------
    # Si REDIS_URL está definida (p. ej. redis://localhost:6379/0) el sistema
    # la usa para caché y memoria; si no, todo cae a un dict en memoria.
    redis_url: str | None = Field(default=None)
    cache_default_ttl: int = Field(default=60, ge=1, le=86400)
    cache_namespace: str = Field(default="odoo_rag")

    # --- Memoria conversacional -------------------------------------------
    memory_max_messages: int = Field(default=12, ge=2, le=50)
    memory_ttl_seconds: int = Field(default=60 * 60 * 6, ge=60, le=60 * 60 * 24 * 7)

    # --- Alertas proactivas -----------------------------------------------
    alert_low_stock_threshold: float = Field(default=10.0, ge=0)
    alert_overdue_days: int = Field(default=0, ge=0)
    alert_cache_ttl: int = Field(default=120, ge=10, le=3600)

    # --- Observabilidad ---------------------------------------------------
    log_dir: Path = Field(default=Path("storage") / "logs")
    log_to_file: bool = Field(default=True)

    # --- Permisos ---------------------------------------------------------
    # Si se activa, los endpoints exigen un X-User-Role o user_id reconocido
    # (ver permissions.ROLE_PERMISSIONS). En desarrollo puede dejarse en False.
    enforce_permissions: bool = Field(default=False)
    default_user_role: str = Field(default="operator")

    def odoo_xmlrpc_common_url(self) -> str:
        return f"{self.odoo_url.rstrip('/')}/xmlrpc/2/common"

    def odoo_xmlrpc_object_url(self) -> str:
        return f"{self.odoo_url.rstrip('/')}/xmlrpc/2/object"


def load_settings() -> Settings:
    return Settings()
