from collections.abc import Generator

from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import obtener_configuracion

# Convención de nombres para que Alembic genere restricciones con nombres estables.
# Los CHECK no tienen convención: siempre se nombran a mano (ck_opcion_justif, etc.).
CONVENCION_NOMBRES = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=CONVENCION_NOMBRES)


motor = create_engine(obtener_configuracion().url_base_datos, pool_pre_ping=True)
SesionLocal = sessionmaker(bind=motor, autoflush=False, expire_on_commit=False)


def obtener_sesion() -> Generator[Session, None, None]:
    """Dependencia de FastAPI: abre una sesión de BD por petición."""
    with SesionLocal() as sesion:
        yield sesion
