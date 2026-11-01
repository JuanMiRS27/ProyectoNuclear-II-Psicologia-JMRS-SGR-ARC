"""Configuración central de la aplicación, cargada desde variables de entorno o `.env`."""

import base64
import binascii
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
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

    # --- Módulo 3: sesiones y convocatorias ---
    # Plazo tras la hora de fin programada para que el cierre automático finalice una sesión.
    SESSION_AUTO_CLOSE_HOURS: int = Field(default=24, ge=0, le=24 * 30)
    # Limitación de intentos fallidos de acceso estudiantil (ventana deslizante).
    STUDENT_LOGIN_WINDOW_SECONDS: int = Field(default=900, gt=0)
    STUDENT_LOGIN_MAX_FAILURES_PER_EMAIL: int = Field(default=5, gt=0)
    STUDENT_LOGIN_MAX_FAILURES_PER_IP: int = Field(default=30, gt=0)
    # Permite al docente responsable consultar los códigos (hasta que el módulo 6 los envíe por correo).
    CONVOCATION_CODE_RETRIEVAL_ENABLED: bool = True
    # Expiración de SEGURIDAD del token estudiantil. Es independiente de `sesion.tiempo_max_min`:
    # el temporizador académico lo calculará el módulo 4 desde `participacion.inicio`.
    # Por defecto 300 min, por encima del máximo de participación (240 min).
    STUDENT_TOKEN_EXPIRE_MINUTES: int = Field(default=300, gt=0, le=60 * 24)
    # Clave AES-256 (32 bytes en Base64 URL-safe) para cifrar los códigos de convocatoria en reposo.
    # Obligatoria y fuera de la base de datos y de Git. Generar con:
    #   python -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
    CONVOCATION_CODE_ENCRYPTION_KEY: str

    @field_validator("CONVOCATION_CODE_ENCRYPTION_KEY")
    @classmethod
    def _clave_aes_256(cls, valor: str) -> str:
        try:
            clave = base64.urlsafe_b64decode(valor.encode())
        except (binascii.Error, ValueError) as exc:
            raise ValueError("CONVOCATION_CODE_ENCRYPTION_KEY debe estar en Base64 URL-safe.") from exc
        if len(clave) != 32:
            raise ValueError("CONVOCATION_CODE_ENCRYPTION_KEY debe codificar exactamente 32 bytes (AES-256).")
        return valor

    @property
    def docs_enabled(self) -> bool:
        return self.ENVIRONMENT != "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
