# Proyecto Nuclear II · Simulador de Psicología Social

Backend del simulador en **FastAPI + SQLAlchemy 2 + Alembic + PostgreSQL 16**, dockerizado.

## Arranque rápido

Requisito: Docker Desktop.

```bash
cp .env.example .env
docker compose up --build
```

- API: http://localhost:8000
- Documentación interactiva (Swagger): http://localhost:8000/docs
- Estado: http://localhost:8000/health
- Bandeja de correos de desarrollo (Mailpit): http://localhost:8025

Al arrancar, el contenedor del backend aplica las migraciones (`alembic upgrade head`).
El código de `backend/` está montado como volumen y el servidor se recarga solo al guardar.

## Comandos útiles

```bash
# Usuarios de prueba (admin, docente y estudiante; credenciales en .env)
docker compose exec backend python -m app.scripts.sembrar_usuarios

# Pruebas
docker compose exec backend pytest

# Nueva migración después de cambiar modelos
docker compose exec backend alembic revision --autogenerate -m "descripcion"

# Consola de PostgreSQL
docker compose exec db psql -U simulador -d simulador
```

## Estructura

```
backend/
├── app/
│   ├── core/            # configuración, BD, enums, dependencias compartidas
│   ├── usuarios/        # Dev 1 · usuario, espacio_academico
│   ├── notificaciones/  # Dev 1 · notificacion, Adapter (correo), Observer (eventos)
│   ├── casos/           # Dev 2 · caso_estudio, escenario, pregunta, opcion, rubrica, criterio_rubrica
│   ├── sesiones/        # Dev 3 · sesion, convocatoria
│   ├── participacion/   # Dev 3 · participacion, respuesta
│   ├── evaluacion/      # Dev 3 · Strategy de evaluación
│   ├── models.py        # registro de los 13 modelos para Alembic
│   └── main.py
├── alembic/             # migraciones
└── tests/
```

## Contratos compartidos

| Contrato | Ubicación |
|---|---|
| `get_current_user()`, `require_rol(*roles)` | `app/core/deps.py` |
| `ServicioCorreo` (Adapter) | `app/notificaciones/correo.py` |
| `PublicadorEventos`, `ObservadorEvento` (Observer) | `app/notificaciones/eventos.py` |
| `obtener_sesion()` (sesión de BD por petición) | `app/core/db.py` |

### Cómo publicar un evento (Dev 3)

Publicar siempre **después** del `commit`, porque los observadores leen con su propia sesión:

```python
from app.notificaciones.eventos import EventoAcademico, TipoEvento, publicador_eventos

sesion.commit()
publicador_eventos.publicar(EventoAcademico(TipoEvento.SESION_PROGRAMADA, sesion_academica.id))
publicador_eventos.publicar(
    EventoAcademico(
        TipoEvento.ESTUDIANTES_MODIFICADOS,
        sesion_academica.id,
        {"convocatoria_ids": [str(c.id) for c in nuevas]},
    )
)
publicador_eventos.publicar(EventoAcademico(TipoEvento.CALIFICACION_REGISTRADA, participacion.id))
publicador_eventos.publicar(EventoAcademico(TipoEvento.RETROALIMENTACION_REGISTRADA, participacion.id))
```

Cada correo enviado queda registrado en la tabla `notificacion`.

## Reglas del equipo

- Cada dev trabaja en su rama y abre PR hacia `develop`.
- Commits en español con prefijo de tipo: `feat`, `fix`, `build`, `chore`, `docs`, `test`, `refactor`.
- Antes de crear una migración: `git pull` de `develop`. No modificar migraciones ajenas ya fusionadas.
