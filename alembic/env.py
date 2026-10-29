from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.core.config import get_settings
from app.core.database import Base
from app.modules.auth import models  # noqa: F401  (registra los modelos en Base.metadata)
from app.modules.cases import models as cases_models  # noqa: F401
from app.modules.sessions import models as sessions_models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    # No desactiva los loggers ya creados por la aplicación (p. ej., si se migra desde las pruebas).
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Permite que las pruebas indiquen otra URL; si no, se usa la de la aplicación.
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", get_settings().DATABASE_URL.replace("%", "%%"))

# Clave de cifrado de códigos para las migraciones de datos (0006). Viaja en memoria desde la
# configuración de la aplicación: nunca se escribe en PostgreSQL ni en los archivos de migración.
config.attributes.setdefault("convocation_code_key", get_settings().CONVOCATION_CODE_ENCRYPTION_KEY)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
