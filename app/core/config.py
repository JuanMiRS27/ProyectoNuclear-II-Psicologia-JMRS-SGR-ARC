"""Configuración central de la aplicación, cargada desde variables de entorno o `.env`."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "Simulador de Psicología Social - API"
    API_V1_PREFIX: str = "/api/v1"
    ENVIRONMENT: Literal["development", "testing", "production"] = "development"

    DATABASE_URL: str
    SQL_ECHO: bool = False

    # Sin valor por defecto: la aplicación no arranca si no se define una clave.
    JWT_SECRET_KEY: str = Field(min_length=32)
    # Solo algoritmos HMAC; nunca "none".
    JWT_ALGORITHM: Literal["HS256", "HS384", "HS512"] = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=60, gt=0, le=60 * 24)

    @property
    def docs_enabled(self) -> bool:
        return self.ENVIRONMENT != "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
