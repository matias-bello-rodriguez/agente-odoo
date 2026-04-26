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

    def odoo_xmlrpc_common_url(self) -> str:
        return f"{self.odoo_url.rstrip('/')}/xmlrpc/2/common"

    def odoo_xmlrpc_object_url(self) -> str:
        return f"{self.odoo_url.rstrip('/')}/xmlrpc/2/object"


def load_settings() -> Settings:
    return Settings()
