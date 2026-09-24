from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App / runtime
    app_env: str = "development"
    public_base_url: str = "http://localhost:8000"
    cors_origins: str = "*"

    # Local storage
    database_path: str = str(BACKEND_DIR / "data" / "orders.db")

    # Paddle Billing
    paddle_env: str = "sandbox"
    paddle_api_key: str = ""
    paddle_client_token: str = ""
    paddle_price_id: str = ""
    paddle_expected_currency: str = "USD"
    paddle_expected_amount_minor: str = ""
    paddle_checkout_url: str = ""
    paddle_webhook_secret: str = ""
    webhook_max_age_seconds: int = 300

    # Protected access links (signing + expiry)
    access_link_secret: str = ""
    access_link_ttl_hours: int = 72

    # Email delivery (SMTP)
    email_host: str = ""
    email_port: int = 587
    email_username: str = ""
    email_password: str = ""
    email_from: str = ""
    email_from_name: str = "Caltur"
    email_use_tls: bool = True
    email_use_ssl: bool = False

    # Digital deliverables
    delivery_file_workbook: str = ""
    masterclass_url: str = ""

    # Protected admin/status endpoint
    admin_status_token: str = ""

    # Optional local dev: serve the existing page without modifying it
    html_path: str = str(BACKEND_DIR.parent / "index (6).html")

    @property
    def paddle_api_base_url(self) -> str:
        if self.paddle_env.lower() == "sandbox":
            return "https://sandbox-api.paddle.com"
        return "https://api.paddle.com"

    def resolve_path(self, value: str) -> Path | None:
        if not value:
            return None
        p = Path(value)
        if not p.is_absolute():
            p = BACKEND_DIR / p
        return p

    @property
    def workbook_path(self) -> Path | None:
        return self.resolve_path(self.delivery_file_workbook)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()