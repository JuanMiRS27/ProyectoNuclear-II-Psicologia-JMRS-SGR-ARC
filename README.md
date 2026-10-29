# Iter Animi — Simulador de Psicología Social (Backend)

Monolito modular en **FastAPI + PostgreSQL**. Estado actual: **Módulo 1 — Autenticación y Gestión de Usuarios** (aprobado) y **Módulo 2 — Gestión de Casos Académicos** (en revisión).

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
| `0003_casos` | Módulo 2: tipo `estado_caso`, tablas `caso_estudio`, `escenario`, `pregunta`, `opcion`, `rubrica`, `criterio_rubrica`, sus restricciones e índices, y el disparador `trg_pregunta_criterio_mismo_caso`. No modifica `usuario`. |
| `0004_tiempo_max_240` | Sustituye `ck_caso_estudio_tiempo_positivo` por `ck_caso_estudio_tiempo_en_rango` (`tiempo_estimado_min BETWEEN 1 AND 240`). Si algún caso supera 240 minutos, se detiene sin modificar datos. |

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

## Módulo 2: Gestión de Casos Académicos

Los docentes con el permiso `puede_crear_casos` diseñan casos como borradores, los validan y los publican.
Este módulo solo define y administra el contenido académico. Sesiones, participaciones, calificación y notificaciones
corresponden a módulos posteriores.

### Estructura

```text
app/modules/cases/
├── models/        # ORM: CasoEstudio, Escenario, Pregunta, Opcion, Rubrica, CriterioRubrica, EstadoCasoEnum
├── schemas/       # Pydantic: creación / actualización / respuesta; resultado de validación
├── repositories/  # CasoRepository (raíz del agregado) y ContenidoCasoRepository (componentes)
├── services/      # CasoService, EscenarioService, PreguntaService, OpcionService, RubricaService
├── routers/       # Endpoints REST (sin lógica de negocio ni SQL)
├── builders/      # Patrón Builder
├── validators/    # Patrón Chain of Responsibility
├── states/        # Patrón State
├── dsl/           # CaseDSL 1.0: esquema y exportación
└── dependencies/  # Política de acceso (reutiliza DocenteUser y CaseCreator del módulo 1)
```

### Modelo relacional

| Tabla | Columnas | Integridad |
|---|---|---|
| `caso_estudio` | `id`, `autor_id`, `nombre`, `desarrollo_situacional`, `historia`, `tiempo_estimado_min`, `estado`, `creado_en`, `actualizado_en` | FK `usuario` ON DELETE RESTRICT; `nombre` no vacío; `tiempo` entre 1 y 240 (o nulo en borrador); si `estado <> 'BORRADOR'`, los datos generales son obligatorios; índice `(autor_id, estado)` |
| `escenario` | `id`, `caso_id`, `orden`, `titulo`, `contexto` | FK caso ON DELETE CASCADE; `UNIQUE (caso_id, orden) DEFERRABLE`; `orden >= 1` |
| `pregunta` | `id`, `escenario_id`, `criterio_id`, `orden`, `enunciado` | FK escenario CASCADE; FK criterio **ON DELETE SET NULL** (nulo permitido en borrador); `UNIQUE (escenario_id, orden) DEFERRABLE`; disparador de coherencia de criterio |
| `opcion` | `id`, `pregunta_id`, **`orden`**, `texto`, `es_correcta`, `justificacion` | FK pregunta CASCADE; `UNIQUE (pregunta_id, orden) DEFERRABLE` |
| `rubrica` | `id`, `caso_id`, `escala_max NUMERIC(4,2)` | `UNIQUE (caso_id)`: una rúbrica por caso; `escala_max > 0` |
| `criterio_rubrica` | `id`, `rubrica_id`, `descripcion`, `peso NUMERIC(5,2)`, `niveles JSONB` | FK rúbrica CASCADE; `0 < peso <= 100`; `jsonb_typeof(niveles) = 'array'` |

Correspondencia entre reglas del dominio y restricciones de PostgreSQL:

| Regla | API (Pydantic) | Publicación (cadena) | PostgreSQL |
|---|---|---|---|
| Tiempo estimado 1–240 min | `ge=1, le=240` | `CASO_TIEMPO_REQUERIDO` / `CASO_TIEMPO_FUERA_DE_RANGO` | `ck_caso_estudio_tiempo_en_rango` (nulo permitido en borrador) |
| Peso en (0, 100], 2 decimales | `gt=0, le=100`, `max_digits=5`, `decimal_places=2` | `CRITERIO_PESO_INVALIDO`; suma = 100 | `ck_criterio_rubrica_peso_en_rango` + `NUMERIC(5,2)` |
| Escala máxima > 0 | `gt=0`, `NUMERIC(4,2)` | `RUBRICA_ESCALA_INVALIDA` | `ck_rubrica_escala_positiva` |
| Una rúbrica por caso | — | — | `uq_rubrica_caso_id` |
| Orden único y ≥ 1 | `ge=1` | órdenes consecutivos | `uq_*_orden` (DEFERRABLE) + `ck_*_orden_positivo` |
| Criterio de la misma rúbrica | verificación en servicio (400) | `PREGUNTA_CRITERIO_AJENO` | disparador `trg_pregunta_criterio_mismo_caso` |
| Datos generales completos fuera de BORRADOR | — | `CASO_*_VACIO`, `CASO_TIEMPO_REQUERIDO` | `ck_caso_estudio_publicado_con_datos_generales` |
| Niveles como arreglo | lista de `NivelSchema` | `CRITERIO_NIVELES_FORMATO_INVALIDO` | `ck_criterio_rubrica_niveles_es_arreglo` |

La sumatoria de pesos, la existencia de opciones correctas o de justificaciones no se imponen en la BD: dependen de
varias filas y de la completitud, y un borrador debe poder guardarse incompleto.

- **Borradores:** la base de datos solo impone la integridad que ningún borrador debe romper. Un caso sin escenarios, una pregunta sin criterio, una opción sin justificación o un criterio sin niveles se pueden guardar. La completitud se exige al publicar.
- **`trg_pregunta_criterio_mismo_caso`:** impide asociar una pregunta a un criterio de la rúbrica de otro caso. Comprueba tres tablas, así que no puede ser un `CHECK`. El servicio lo verifica antes y devuelve un 400 legible; el disparador es la garantía final.
- **Restricciones de orden `DEFERRABLE`:** al reordenar se difieren al `COMMIT`, de modo que un intercambio (1↔2) no viola la unicidad a mitad de la transacción. Tras una eliminación, los hermanos se renumeran 1..n.
- **Eliminación física:** solo de borradores. Ningún borrador puede estar vinculado todavía a una sesión, porque solo los casos publicados serán seleccionables.

### Patrones de diseño

**Builder** (`builders/`) construye un caso completo:

| Participante | Clase | Responsabilidad |
|---|---|---|
| Abstracción | `ConstructorCaso` | Declara los pasos: datos generales, escenario, pregunta, opción, rúbrica, criterio y `construir()`. |
| Constructor concreto | `ConstructorCasoAcademico` | Exige la secuencia válida de pasos, numera órdenes, permite referenciar criterios por clave antes de declararlos y los resuelve al construir (rechaza claves desconocidas o repetidas), y fija siempre `BORRADOR`. No usa la sesión de BD. |
| Director | `ServicioConstruccionCaso` | Recorre un documento CaseDSL e invoca los pasos en orden. |
| Producto | `CasoAcademico` | Agregado transitorio (raíz `CasoEstudio` con todo enlazado). |

`CasoService.crear_completo` persiste el producto con un solo `add` en cascada dentro de una transacción. Si cualquier
INSERT falla, el rollback no deja escenarios, preguntas ni rúbricas a medias; las pruebas lo verifican con un fallo
simulado y con una violación real de `UNIQUE`. La edición individual (endpoints de escenarios, preguntas, opciones
y rúbrica) no pasa por el Builder: modifica componentes de un borrador existente.

**Chain of Responsibility** (`validators/`) valida antes de publicar:

`ValidadorDatosCaso → ValidadorEscenariosYPreguntas → ValidadorOpciones → ValidadorRubrica`

Es la variante acumulativa: cada eslabón registra sus inconsistencias en un `ResultadoValidacion` compartido y siempre
delega en el siguiente, así que se devuelven todos los errores y no solo el primero. La cadena no modifica nada y es la
misma para `/validate` y `/publish`. Formato de respuesta:

```json
{
  "valido": false,
  "errores": [
    {
      "codigo": "OPCION_SIN_JUSTIFICACION",
      "componente": "opcion",
      "componente_id": "3f6c…",
      "campo": "justificacion",
      "ruta": "escenarios[1].preguntas[0].opciones[2].justificacion",
      "mensaje": "La opción 3 de la pregunta 1 del escenario 2 no tiene justificación."
    }
  ]
}
```

`componente_id` identifica la entidad exacta y `ruta` su posición, para que un frontend señale qué corregir.

**State** (`states/`) gestiona el ciclo de vida:

| Estado | Editar contenido | Eliminar | Publicar | Archivar | Seleccionable para sesiones (módulo 3) |
|---|---|---|---|---|---|
| `EstadoBorrador` | Sí | Sí | Sí, si la cadena no devuelve errores | No (409) | No |
| `EstadoPublicado` | No (409) | No (409) | No (409) | Sí | Sí (`admite_programacion()`) |
| `EstadoArchivado` | No (409) | No (409) | No (409) | No (409) | No |

`CicloVidaCaso` es el contexto: elige la clase de estado según `caso.estado` y delega en ella. Así los servicios no
contienen `if estado == ...`. Colaboración al publicar:

1. `CasoService.publicar` bloquea la fila del caso (`SELECT … FOR UPDATE`) y carga su contenido.
2. Llama a `CicloVidaCaso(caso).publicar(validar)`, donde `validar` es una función que ejecuta la cadena.
3. Solo `EstadoBorrador` invoca esa función. Los demás estados responden 409 sin validar, de modo que State no duplica la validación.
4. Si hay errores se lanza 422 con la lista y el caso permanece en BORRADOR.
5. Si no los hay, el estado pasa a PUBLICADO y la transacción se confirma.

**Concurrencia:** toda escritura sobre un caso o su contenido bloquea primero la fila del caso. Una edición y una
publicación simultáneas se ejecutan en serie, y dos publicaciones simultáneas producen un 200 y un 409. Una prueba de
control demuestra que, sin el bloqueo, un caso publicado llegaría a modificarse.

### CaseDSL 1.0

Representación declarativa en JSON (camelCase), usada para importar (`POST /cases/full`) y exportar (`GET /cases/{id}/dsl`):

```json
{
  "dslVersion": "1.0",
  "caso": {"nombre": "…", "desarrolloSituacional": "…", "historia": "…", "tiempoEstimadoMin": 45},
  "escenarios": [
    {"titulo": "…", "contexto": "…", "preguntas": [
      {"enunciado": "…", "criterio": "ANALISIS", "opciones": [
        {"texto": "…", "esCorrecta": true, "justificacion": "…"}
      ]}
    ]}
  ],
  "rubrica": {"escalaMax": "5.00", "criterios": [
    {"clave": "ANALISIS", "descripcion": "…", "peso": "60.00", "niveles": [
      {"nombre": "Insuficiente", "valor": "1.00", "minAciertosPct": "0.00"}
    ]}
  ]}
}
```

- El orden de los arreglos es el orden académico.
- Las preguntas referencian criterios por `clave`. La BD no guarda claves, así que al exportar se generan como `C1..Cn`.
- La exportación incluye `id` y `caso.estado` como información; al importar se ignoran y el caso siempre nace en BORRADOR.
- **Dos validaciones distintas:**
  - el esquema Pydantic de la DSL valida la estructura (tipos, longitudes, versión, claves únicas, referencias existentes) y responde 422 sin persistir nada;
  - la cadena valida la completitud para publicar.

  Un documento estructuralmente válido puede describir un borrador incompleto.
- **Una sola fuente de verdad:** la DSL no se almacena. PostgreSQL es la persistencia y la DSL se genera desde él en cada exportación. Las pruebas comprueban la ida y vuelta (importar → exportar → reimportar).

### Decimales y niveles

- `peso` y `escala_max` son `NUMERIC` en PostgreSQL y `Decimal` en Python. La suma de pesos se compara con `Decimal("100.00")`: por ejemplo, 33.33 + 33.33 + 33.34 da exactamente 100.
- Las respuestas JSON devuelven los decimales como texto con dos decimales (`"40.00"`) para no perder precisión en el cliente. Se aceptan como número o como texto, con hasta 2 decimales.
- Los niveles se guardan en JSONB como números, consultables desde SQL. El tipo `NivelesJSONB` los reconstruye como `Decimal` al leer. La conversión es exacta porque los valores tienen como máximo 4 dígitos y 2 decimales.
- Estructura de un nivel, según el JSON Schema de CaseDSL:
  - `nombre` y `valor`: obligatorios;
  - `minAciertosPct` (`min_aciertos_pct` en la API REST y en JSONB): opcional.

  Este módulo no calcula calificaciones; eso corresponde al módulo 5.

### Reglas de publicación

| Validador | Código | Regla |
|---|---|---|
| Datos | `CASO_NOMBRE_VACIO`, `CASO_DESARROLLO_SITUACIONAL_VACIO`, `CASO_HISTORIA_VACIA` | Textos generales obligatorios |
| | `CASO_TIEMPO_REQUERIDO`, `CASO_TIEMPO_FUERA_DE_RANGO` | Tiempo obligatorio, entre 1 y 240 minutos |
| Escenarios y preguntas | `CASO_SIN_ESCENARIOS`, `ESCENARIO_SIN_PREGUNTAS` | Al menos un escenario y al menos una pregunta por escenario |
| | `ESCENARIO_TITULO_VACIO`, `ESCENARIO_CONTEXTO_VACIO`, `PREGUNTA_ENUNCIADO_VACIO` | Contenido obligatorio |
| | `ESCENARIO_ORDEN_INVALIDO`, `PREGUNTA_ORDEN_INVALIDO` | Órdenes consecutivos desde 1 |
| Opciones | `PREGUNTA_OPCIONES_INSUFICIENTES` | Al menos 2 opciones |
| | `PREGUNTA_SIN_OPCION_CORRECTA` | Al menos una opción correcta (se admiten varias, incluso todas) |
| | `OPCION_TEXTO_VACIO`, `OPCION_SIN_JUSTIFICACION` | Texto y justificación en cada opción |
| Rúbrica | `CASO_SIN_RUBRICA`, `RUBRICA_SIN_CRITERIOS`, `RUBRICA_ESCALA_INVALIDA` | Existe una rúbrica, con criterios y escala > 0 |
| | `CRITERIO_DESCRIPCION_VACIA`, `CRITERIO_PESO_INVALIDO`, `RUBRICA_PESOS_NO_SUMAN_100` | Criterios válidos cuyos pesos suman exactamente 100 |
| | `CRITERIO_NIVELES_INSUFICIENTES`, `CRITERIO_NIVELES_FORMATO_INVALIDO`, `NIVEL_NOMBRE_DUPLICADO` | Al menos 2 niveles (JSON Schema de CaseDSL), bien formados y con nombres únicos |
| | `PREGUNTA_SIN_CRITERIO` | Cada pregunta está asociada a un criterio existente |
| | `PREGUNTA_CRITERIO_AJENO` | El criterio pertenece a la rúbrica del mismo caso |

Un criterio puede no tener preguntas asociadas: el caso sigue siendo publicable si cumple las demás reglas.

#### Reglas retiradas en la revisión del módulo 2

| Código retirado | Motivo |
|---|---|
| `CASO_TIEMPO_INSUFICIENTE` (≥ 1 min por pregunta) | No forma parte de los requerimientos. |
| `CASO_TIEMPO_FUERA_DE_RANGO` con máximo 480 | El modelo establece un máximo de 240 minutos (ahora se aplica también en API y BD). |
| `CRITERIO_SIN_PREGUNTAS` | No está establecida y puede impedir configuraciones contempladas en los ejemplos del proyecto. |
| `PREGUNTA_SIN_OPCION_INCORRECTA` | No se exige una opción incorrecta. |
| `OPCION_TEXTO_DUPLICADO` | Regla añadida que no figuraba entre las reglas originales de opciones. |
| `NIVEL_UMBRAL_DUPLICADO`, `CRITERIO_SIN_NIVEL_BASE`, `NIVEL_VALOR_FUERA_DE_ESCALA`, `NIVELES_NO_MONOTONOS` | Son reglas de calificación: se pospusieron para definirlas con el módulo 5. |

#### Niveles de rúbrica: qué se valida y qué se pospone

| Permanece (estructura) | Dónde |
|---|---|
| `niveles` es un arreglo JSON | CHECK de PostgreSQL |
| `nombre` y `valor` son obligatorios; `minAciertosPct` es opcional; no se admiten otros campos | Esquema Pydantic / JSON Schema de CaseDSL |
| `nombre` no vacío (máx. 80); `valor` ≥ 0 con 2 decimales (máx. 99.99); `minAciertosPct`, si se indica, entre 0 y 100 con 2 decimales | Esquema Pydantic |
| Al menos **2 niveles** por criterio, con nombres no repetidos | Cadena de publicación (un borrador puede tener menos) |

| Se pospone al módulo 5 (Evaluación) | Pregunta que deberá responderse |
|---|---|
| Interpretación de `minAciertosPct` | ¿Es el porcentaje mínimo de aciertos en las preguntas del criterio (`pregunta.criterio_id`) o en todo el caso? ¿Cómo se clasifica un nivel que no lo indica? |
| ¿Porcentajes distintos y un nivel con 0? | ¿Cómo se clasifica un resultado que no alcanza ningún porcentaje? |
| ¿`valor` ≤ `escala_max`? | ¿`valor` se expresa en la escala de la rúbrica o en otra unidad? |
| ¿Monotonía porcentaje/valor? | ¿Un nivel con más aciertos puede valer menos? |

El único supuesto que se mantiene sobre `minAciertosPct` es el que expresa su nombre: si se indica, es un porcentaje
entre 0 y 100.

#### Nota para el módulo 5 (Evaluación): varias opciones correctas

- Una pregunta puede tener varias opciones correctas, incluso todas.
- Esto no convierte la pregunta en selección múltiple: la participación (módulo 4) registra una sola opción por estudiante y pregunta.
- Lo previsto es que elegir cualquier opción con `es_correcta = true` cuente como acierto. El módulo 5 debe confirmarlo y definir cómo se agrega por criterio (`pregunta.criterio_id`) usando `peso`, `niveles` y `escala_max`.

### Control de acceso

| Acción | Requisito |
|---|---|
| Crear, editar, eliminar, publicar, archivar | `CaseCreator` (docente activo con `puede_crear_casos`) y ser autor del caso |
| Consultar, listar, validar, exportar DSL | `DocenteUser` y ser autor. Si un docente pierde el permiso, conserva la lectura de sus casos, pero no puede modificarlos. |
| Administrador / estudiante | 403 en todos los endpoints del módulo |
| Caso de otro docente | 404, indistinguible de un caso inexistente |

`autor_id` siempre se toma del usuario autenticado. Los esquemas rechazan campos extra (`autor_id`, `estado`, …) con 422.

### Endpoints del módulo 2 (`/api/v1`)

| Método | Ruta | Descripción | Éxito |
|---|---|---|---|
| POST | `/cases` | Crear borrador | 201 |
| POST | `/cases/full` | Construir caso completo desde CaseDSL (Builder) | 201 |
| GET | `/cases?estado=&skip=&limit=` | Listar mis casos | 200 |
| GET | `/cases/{id}` | Caso completo (escenarios, preguntas, opciones, rúbrica) | 200 |
| PATCH | `/cases/{id}` | Información general (borrador) | 200 |
| DELETE | `/cases/{id}` | Eliminar borrador | 204 |
| POST | `/cases/{id}/validate` | Validar sin cambiar el estado | 200 |
| POST | `/cases/{id}/publish` | Validar y publicar (422 con errores si no es válido) | 200 |
| POST | `/cases/{id}/archive` | Archivar un caso publicado | 200 |
| GET | `/cases/{id}/dsl` | Exportar CaseDSL 1.0 | 200 |
| POST / GET | `/cases/{id}/scenarios` | Crear / listar escenarios | 201 / 200 |
| PUT | `/cases/{id}/scenarios/order` | Reordenar (`{"ids": [...]}` con todos los escenarios) | 200 |
| GET / PATCH / DELETE | `/cases/{id}/scenarios/{scenario_id}` | Consultar / modificar / eliminar escenario | 200 / 200 / 204 |
| POST / GET | `/cases/{id}/scenarios/{scenario_id}/questions` | Crear / listar preguntas | 201 / 200 |
| PUT | `/cases/{id}/scenarios/{scenario_id}/questions/order` | Reordenar preguntas | 200 |
| GET / PATCH / DELETE | `/cases/{id}/questions/{question_id}` | Consultar / modificar (enunciado, `criterio_id`) / eliminar | 200 / 200 / 204 |
| POST / GET | `/cases/{id}/questions/{question_id}/options` | Crear / listar opciones | 201 / 200 |
| PUT | `/cases/{id}/questions/{question_id}/options/order` | Reordenar opciones | 200 |
| PATCH / DELETE | `/cases/{id}/questions/{question_id}/options/{option_id}` | Modificar / eliminar opción | 200 / 204 |
| POST / GET / PATCH | `/cases/{id}/rubric` | Crear (con criterios opcionales) / consultar / cambiar `escala_max` | 201 / 200 / 200 |
| POST / GET | `/cases/{id}/rubric/criteria` | Crear / listar criterios | 201 / 200 |
| PATCH / DELETE | `/cases/{id}/rubric/criteria/{criterion_id}` | Modificar / eliminar criterio | 200 / 204 |

Errores:

| Código | Significado |
|---|---|
| 400 | Regla de negocio: criterio de otro caso, reorden incompleto, campo obligatorio a `null` |
| 401 | No autenticado |
| 403 | Rol o permiso insuficiente |
| 404 | Recurso inexistente, ajeno o que no pertenece al caso indicado |
| 409 | Operación no permitida en el estado actual, orden ocupado, rúbrica duplicada |
| 422 | Esquema inválido, o publicación con errores (`{detail, valido, errores}`) |

### Decisiones y discrepancias documentales

No tuve acceso al documento de Ingeniería de Software II ni a sus reglas OCL. Las reglas se derivaron de los requisitos
de esta etapa y del modelo relacional; estas son las decisiones que conviene confirmar:

1. **Columna `opcion.orden` (agregada al modelo).** Sin ella, el orden de presentación de las opciones sería arbitrario: UUID aleatorio y sin fecha de creación. Es una columna, no una tabla, con unicidad por pregunta.
2. **Criterios sin columna de orden.** Se ordenan por `descripcion` (y `id`). Las claves DSL `C1..Cn` siguen ese orden.
3. **Varias opciones correctas.** Se permiten, incluso todas; ver la nota para el módulo 5.
4. **Niveles.**
   - El campo de porcentaje se llama `minAciertosPct`, como en el JSON Schema del proyecto. Antes se llamaba `umbral_aciertos` y era obligatorio.
   - Ahora es opcional, y solo `nombre` y `valor` son obligatorios.
   - Se exigen al menos 2 niveles al publicar.
   - Las reglas de calificación se pospusieron al módulo 5.
   - Si alguna base de desarrollo guardó niveles con la clave anterior, esos criterios deben volver a guardarse (la validación los marca con `CRITERIO_NIVELES_FORMATO_INVALIDO`).
5. **Tiempo estimado: 1–240 minutos.** El límite está en una sola constante, `TIEMPO_MAXIMO_MIN` en `models/caso.py`, que usan el CHECK de PostgreSQL, los esquemas y el validador. En un borrador puede quedar nulo.
6. **Criterios sin preguntas.** Están permitidos.
7. **Migración 0004 en lugar de editar 0003.** Como 0003 pudo haberse aplicado ya en alguna base, la corrección del CHECK va en una migración nueva. Si se confirma que nadie aplicó 0003, ambas podrían fusionarse antes del commit.
8. **Transiciones.**
   - `BORRADOR → ARCHIVADO` no existe: un borrador se elimina.
   - `ARCHIVADO` es final: no hay vuelta a `PUBLICADO`.
   - Un caso publicado no se edita. Una futura "nueva versión" debería crear un borrador nuevo, por ejemplo exportando e importando la DSL.
9. **409 frente a 400.** Las operaciones rechazadas por el estado del caso devuelven 409 (conflicto con el estado actual del recurso). Los 400 quedan para reglas de datos.
10. **Lectura sin permiso.** Un docente sin `puede_crear_casos` puede leer sus propios casos (los creó cuando tenía el permiso), pero no editarlos.
11. **Administradores.** No acceden a los casos. Si necesitan supervisar el contenido, habría que definir un endpoint de solo lectura.
12. **Cambio en `app/core/exceptions.py`.** `AppError` acepta `extra` (campos adicionales en el JSON) y se añadió `UnprocessableEntityError` (422). Es compatible hacia atrás: el módulo 1 no cambia.

## Pendiente para módulos posteriores

- **Módulo 3:** seleccionar solo casos publicados al programar sesiones (usar `CicloVidaCaso(caso).admite_programacion()`) y añadir la FK sesión → caso con `ON DELETE RESTRICT`.
- **Módulo 4:** una vista de caso para estudiantes **sin** `es_correcta` ni `justificacion`. `OpcionRead` es solo para autores.
- **Módulo 5:** cálculo de calificaciones con `niveles`, `peso` y `escala_max`.
- **Módulo 3:** acceso estudiantil con correo institucional y código de convocatoria. El rol `ESTUDIANTE` y su fila sin contraseña ya están soportados; el endpoint de acceso se añadirá en `routers/auth_router.py` usando la entidad `convocatoria`.
- *Refresh tokens*, "cerrar todas las sesiones" a petición del usuario y limitación de intentos de login. No se pidieron en este módulo.
