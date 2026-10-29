from pydantic import BaseModel, ConfigDict, Field

from app.modules.auth.schemas.usuario import PASSWORD_MAX_LENGTH, Clave, Correo


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    correo: Correo
    # Sin longitud mínima: una contraseña corta simplemente no coincidirá.
    clave: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Segundos de validez del token.")


class PasswordChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clave_actual: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)
    clave_nueva: Clave


class MessageResponse(BaseModel):
    detail: str
