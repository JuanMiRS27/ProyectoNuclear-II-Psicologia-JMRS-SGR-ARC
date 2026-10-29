# Simulador de Psicología Social — Backend

Monolito modular en **FastAPI + PostgreSQL**. Estado actual: **Módulo 1 — Autenticación y Gestión de Usuarios**.

| Componente | Versión |
|---|---|
| Python | 3.12+ (probado con 3.14) |
| PostgreSQL | 13+ (probado con 17.6) |
| FastAPI / SQLAlchemy / Alembic / Pydantic | ver `requirements.txt` |

## Estructura

```text
app/
├── main.py                      # Fábrica de la app; registra los routers de cada módulo
├── core/                        # Infraestructura compartida por todos los módulos
│   ├── config.py                # Configuración por variables de entorno (.env)
│   ├── database.py              # Motor, sesiones, Base declarativa
│   ├── security.py              # Argon2 + JWT (sin dependencia del modelo)
│   └── exceptions.py            # Errores de aplicación → respuestas HTTP
├── modules/
│   └── auth/                    # Módulo 1
│       ├── models/              # ORM: Usuario, RolUsuario
│       ├── schemas/             # Pydantic: entrada/salida de la API
│       ├── repositories/        # Único acceso a la tabla usuario
│       ├── services/            # Reglas de negocio (AuthService, UsuarioService)
│       ├── routers/             # Endpoints REST (sin SQL ni lógica de negocio)
│       └── dependencies/        # Autenticación y autorización reutilizables
└── scripts/create_admin.py      # Creación segura del administrador inicial
alembic/                         # Migraciones
tests/                           # Pytest contra PostgreSQL real
```

Flujo de una petición: `router → dependencia de autorización → servicio → repositorio → PostgreSQL`.
Los servicios lanzan excepciones de `app.core.exceptions` (sin conocer HTTP) y un manejador global las traduce a JSON.

## Puesta en marcha

### 1. Crear la base de datos

Con `psql` como superusuario (reemplace la contraseña):

```sql
CREATE ROLE simulador_user LOGIN PASSWORD 'una-clave-segura';
CREATE DATABASE simulador_psicologia OWNER simulador_user;
CREATE DATABASE simulador_psicologia_test OWNER simulador_user;  -- solo para pruebas
```

### 2. Entorno Python y configuración

```powershell
py -3 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Edite `.env`: `DATABASE_URL`, `TEST_DATABASE_URL` y `JWT_SECRET_KEY`. Para generar la clave:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

### 3. Migraciones

```powershell
alembic upgrade head      # aplicar
alembic current           # revisión actual
alembic downgrade base    # revertir todo
```

| Revisión | Contenido |
|---|---|
| `0001_usuario` | Tipo `rol_usuario`, tabla `usuario`, restricciones e índice. |
| `0002_version_token` | Columna `usuario.version_token INTEGER NOT NULL DEFAULT 0` + `CHECK (version_token >= 0)`. Las filas existentes quedan en 0. Los JWT emitidos antes de aplicarla no llevan el claim `ver` y dejan de ser válidos: los usuarios deben iniciar sesión de nuevo. |

### 4. Administrador inicial

```powershell
python -m app.scripts.create_admin --nombre "Nombre Apellido" --correo admin@universidad.edu.co
```

La contraseña se pide por consola sin mostrarse, dos veces. En despliegues no interactivos se lee de la variable
`INITIAL_ADMIN_PASSWORD`. No se acepta como argumento, para que no quede en el historial. Si el correo ya existe, el script falla y no modifica nada.

### 5. Servidor

```powershell
uvicorn app.main:app --reload            # desarrollo
uvicorn app.main:app --host 0.0.0.0 --port 8000   # sin recarga
```

Documentación interactiva: <http://localhost:8000/docs> (deshabilitada si `ENVIRONMENT=production`).
En Swagger, pulse **Authorize** y pegue el `access_token` obtenido en `/api/v1/auth/login`.

### 6. Pruebas

```powershell
pytest
```

Requiere `TEST_DATABASE_URL` (en `.env` o como variable de entorno) apuntando a una base PostgreSQL **exclusiva**.
Al iniciar, la suite ejecuta `alembic downgrade base` + `upgrade head` sobre esa base (así verifica las migraciones),
vacía la tabla tras cada prueba y revierte el esquema al terminar.

Como esas operaciones son destructivas, `tests/db_guard.py` aborta la suite antes de tocar nada si alguna de estas
condiciones no se cumple:

| Comprobación | Momento |
|---|---|
| La URL es de PostgreSQL y el nombre de la base empieza por `test_` o termina en `_test`. | Antes de importar la app |
| `ENVIRONMENT` no es `production`. | Antes de importar la app |
| El host es `localhost`/`127.0.0.1`/`::1`, salvo que se defina `ALLOW_REMOTE_TEST_DATABASE=true` (p. ej., un contenedor de CI). | Antes de importar la app |
| No apunta a la misma base que `DATABASE_URL` (se comparan host normalizado, puerto y nombre). | Antes de importar la app |
| `current_database()` coincide con la de la URL. | Conectado, antes de migrar |
| La base tiene la marca `COMMENT ON DATABASE … IS 'simulador-psicologia:base-de-pruebas-desechable'` o está vacía. Si está vacía, la suite la marca en la primera ejecución. Una base con tablas y sin marca (una base de desarrollo) se rechaza. | Conectado, antes de migrar |

Las pruebas de concurrencia (`tests/test_concurrency.py`) usan hilos con conexiones independientes contra la misma base.

## API (`/api/v1`)

| Método | Endpoint | Acceso | Descripción |
|---|---|---|---|
| POST | `/auth/login` | Público | `{correo, clave}` → `{access_token, token_type, expires_in}` |
| GET | `/auth/me` | Admin / Docente | Perfil del usuario autenticado |
| PATCH | `/auth/password` | Admin / Docente | `{clave_actual, clave_nueva}`. Invalida **todos** los tokens anteriores, incluido el usado en la petición. |
| POST | `/users/teachers` | Admin | Crear docente `{nombre_completo, correo, clave, codigo_institucional?}` → 201 |
| GET | `/users` | Admin | Listar. Filtros: `rol`, `activo`, `skip`, `limit` (≤100) |
| GET | `/users/{id}` | Admin | Consultar usuario |
| PATCH | `/users/{id}` | Admin | Actualizar `nombre_completo`, `correo`, `codigo_institucional` |
| PATCH | `/users/{id}/status` | Admin | `{activo: bool}` |
| PATCH | `/users/{id}/case-permission` | Admin | `{puede_crear_casos: bool}` (solo docentes) |
| GET | `/health` | Público | Comprobación de disponibilidad |

Códigos: 200, 201, 400 (regla de negocio), 401 (token ausente, inválido o expirado; credenciales incorrectas),
403 (rol insuficiente o cuenta desactivada en el login), 404, 409 (correo o código duplicado), 422 (validación).
Las respuestas de error tienen la forma `{"detail": "..."}` y nunca exponen detalles de la base de datos.
Ninguna respuesta incluye `clave_hash`.

## Uso de los permisos desde otros módulos

```python
from fastapi import APIRouter, Depends
from app.modules.auth.dependencies import AdminUser, CaseCreator, CurrentUser, require_roles
from app.modules.auth.models import RolUsuario

router = APIRouter()

@router.post("/cases")
def crear_caso(docente: CaseCreator):          # docente activo con puede_crear_casos
    ...

@router.get("/algo", dependencies=[Depends(require_roles(RolUsuario.ADMINISTRADOR, RolUsuario.DOCENTE))])
def algo(): ...
```

Todas las dependencias consultan el estado **actual** del usuario en la base de datos en cada petición.

## Decisiones técnicas

1. **`clave_hash` admite NULL.** Los estudiantes accederán con correo institucional y código de convocatoria (módulo 3), así que no tienen contraseña. No se inventan contraseñas: la restricción `ck_usuario_clave_requerida_por_rol` exige `clave_hash` para ADMINISTRADOR y DOCENTE.
2. **Restricciones en PostgreSQL** (además de las validaciones en Python):
   - `UNIQUE(correo)` + `CHECK (correo = lower(btrim(correo)))`: unicidad sin distinguir mayúsculas, garantizada por la base de datos.
   - `CHECK (puede_crear_casos = false OR rol = 'DOCENTE')`.
   - `CHECK (length(btrim(nombre_completo)) > 0)`.
   - `UNIQUE(codigo_institucional)` (los NULL se permiten).
   - Tipo nativo `ENUM rol_usuario`, `id UUID DEFAULT gen_random_uuid()` e índice sobre `rol`.
3. **`creado_en` es `TIMESTAMP WITH TIME ZONE`** con `DEFAULT now()`. Sigue siendo un TIMESTAMP del modelo, pero guarda el instante sin ambigüedad de zona horaria.
4. **JWT HS256** con claims `sub`, `rol`, `ver`, `type`, `iat` y `exp`, todos obligatorios. Se rechazan tokens expirados, con otra firma, con `alg: none` o mal formados. El `rol` del token es solo informativo: los permisos se evalúan con el registro actual en la BD, así que una desactivación o una revocación de permiso tiene efecto inmediato aunque el token siga vigente.
5. **Login seguro.** Correo inexistente y contraseña incorrecta producen la misma respuesta 401. Para un correo inexistente se verifica contra un hash ficticio, de modo que el tiempo de respuesta tampoco permite enumerar usuarios. La cuenta desactivada (403) solo se informa a quien dio la contraseña correcta. Si los parámetros de Argon2 cambian, el hash se actualiza en el siguiente login.
6. **Sin cambios de rol por API.** No existe ningún endpoint que modifique `rol`, y los esquemas rechazan campos extra (`extra="forbid"`). Los administradores solo se crean con el script. Así no hay vía de escalación de privilegios.
7. **Protección de administradores.** Un administrador no puede desactivarse a sí mismo ni modificar los datos de otro administrador. Para desactivar a otro administrador, primero se bloquean las filas de los administradores activos (`SELECT … FOR UPDATE ORDER BY id`; el orden fijo evita interbloqueos) y se exige que quede al menos uno activo. Así, dos desactivaciones cruzadas simultáneas no pueden dejar el sistema sin administradores; `tests/test_concurrency.py` lo verifica, junto con una prueba de control que muestra que sin el bloqueo la carrera sí ocurre.
8. **Invalidación de tokens por versión.** `usuario.version_token` (migración 0002) viaja en el claim `ver`. En cada petición se compara con el valor actual en la BD. Un cambio de contraseña lo incrementa de forma atómica en SQL (`version_token = version_token + 1`), y todos los tokens anteriores, en cualquier dispositivo, pasan a responder 401. Se eligió frente a una lista negra de tokens porque no necesita tablas nuevas ni limpieza periódica, y la consulta del usuario ya se hacía en cada petición. La misma columna servirá para un futuro "cerrar todas las sesiones" o un restablecimiento de contraseña por un administrador.
9. **Transacciones en `UsuarioService`.** Toda escritura se hace dentro de `_transaction()`, que cubre tanto los `flush()` del repositorio como el `commit()`. Ante cualquier excepción hace rollback (y libera los bloqueos `FOR UPDATE`). Las violaciones de unicidad (`uq_usuario_correo`, `uq_usuario_codigo_institucional`) se traducen en 409. La verificación previa de duplicados se mantiene para dar el mensaje correcto en el caso habitual; la restricción de la BD es la garantía final ante peticiones concurrentes.
10. **Contraseñas** de 8 a 128 caracteres. El tope evita el costo de hashear entradas gigantes.
11. **Sesiones síncronas de SQLAlchemy** (FastAPI las ejecuta en un pool de hilos). Es más simple y suficiente para el alcance académico; migrar a `AsyncSession` no afectaría a routers ni esquemas.

## Administración de roles: revisión

**Decisión vigente:** ningún endpoint modifica `rol`. Se mantiene por estas razones:

- Cambiar el rol de un usuario existente no es una operación neutra en este modelo. Un ESTUDIANTE no tiene contraseña, así que pasar a DOCENTE o ADMINISTRADOR exigiría asignarle una. Un DOCENTE con `puede_crear_casos = true` viola `ck_usuario_permiso_casos_solo_docente` si cambia de rol. Y los módulos 2–5 asociarán casos, sesiones y participaciones al usuario según su rol, de modo que un cambio posterior dejaría registros incoherentes (por ejemplo, casos cuyo autor ya no es docente).
- Es la vía clásica de escalación de privilegios. Si no existe, no hay nada que proteger.

**Cubierto en el módulo 1:**

| Capacidad | Cómo |
|---|---|
| Asignar el rol al crear la cuenta | DOCENTE: `POST /users/teachers` (el rol lo fija el servidor). ADMINISTRADOR: solo con `python -m app.scripts.create_admin`. |
| Rol inmutable | Ni `PATCH /users/{id}` ni ningún esquema aceptan `rol` (422 por `extra="forbid"`). |
| Permiso granular de docentes | `PATCH /users/{id}/case-permission`, solo para DOCENTE (400 en otro caso; también lo impide un CHECK en la BD). |
| Suspender o restablecer el acceso | `PATCH /users/{id}/status`, con efecto inmediato sobre tokens vigentes. |
| Garantía de al menos un administrador activo | Sin autodesactivación, más bloqueo de filas ante concurrencia. |
| Aislamiento entre administradores | Un administrador no puede editar los datos de otro. |
| Autorización reutilizable | `require_roles(...)`, `require_admin`, `require_docente`, `require_case_creator`. |

**Pendiente (requiere decisión antes de los demás módulos):**

| Funcionalidad | Consideraciones para decidir |
|---|---|
| Promover un DOCENTE a ADMINISTRADOR | ¿Se permite por API o solo por script? Si es por API: ¿doble confirmación, reautenticación con contraseña, auditoría? ¿Pierde `puede_crear_casos` y sus casos? |
| Degradar un ADMINISTRADOR | Exigiría la misma regla de "al menos un administrador activo" e incrementar `version_token`. |
| Crear administradores adicionales por API | Hoy solo es posible con el script, a propósito. |
| Alta de ESTUDIANTES | Depende de `convocatoria` (módulo 3): ¿se crean al inscribirse, por carga masiva o en el primer acceso? |
| Cambio de rol ESTUDIANTE ↔ DOCENTE | Probablemente no deba existir: es mejor una cuenta nueva por rol (el correo es único, así que habría que decidir cómo se maneja). |
| Auditoría de cambios de permisos y estado | No existe tabla de auditoría. ¿Se necesita registrar quién concedió o revocó qué y cuándo? Podría encajar con el módulo 6 (eventos). |
| Restablecimiento de contraseña por un administrador | No implementado. La infraestructura (`version_token`) ya permite invalidar las sesiones al hacerlo. |
| Permisos adicionales además de `puede_crear_casos` | El modelo solo tiene ese booleano. Si los módulos posteriores necesitan más permisos, convendría decidir entre más columnas o una tabla de permisos. |
| Retención del permiso al desactivar | Desactivar a un docente no revoca `puede_crear_casos`; lo conserva al reactivarse. Confirmar si es lo deseado. |

## Pendiente para módulos posteriores

- **Módulo 2:** creación de casos. Debe usar la dependencia `CaseCreator` / `require_case_creator`.
- **Módulo 3:** acceso estudiantil con correo institucional y código de convocatoria. El rol `ESTUDIANTE` y su fila sin contraseña ya están soportados; el endpoint de acceso se añadirá en `routers/auth_router.py` usando la entidad `convocatoria`.
- *Refresh tokens*, "cerrar todas las sesiones" a petición del usuario y limitación de intentos de login. No se pidieron en este módulo.
