from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Configuracion(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    entorno: str = "desarrollo"

    postgres_user: str = "simulador"
    postgres_password: str = "simulador"
    postgres_db: str = "simulador"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    jwt_secreto: str = "solo-para-desarrollo-cambiar-en-produccion"
    jwt_algoritmo: str = "HS256"
    jwt_minutos_expiracion: int = 60

    # Correo: "consola" lo escribe en el log; "smtp" lo envía con AdaptadorProveedorCorreo
    correo_modo: str = "consola"
    correo_remitente: str = "Simulador de Psicología Social <no-responder@simulador.edu.co>"
    smtp_host: str = "localhost"
    smtp_puerto: int = 1025
    smtp_usuario: str | None = None
    smtp_clave: str | None = None
    smtp_tls: bool = False

    @property
    def url_base_datos(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def obtener_configuracion() -> Configuracion:
    return Configuracion()
