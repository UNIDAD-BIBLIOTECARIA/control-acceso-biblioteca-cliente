# Biblioteca Cliente

Aplicación de escritorio en modo **kiosko** que corre en cada PC de una sala de biblioteca universitaria (Universidad de El Salvador, Facultad Multidisciplinaria de Oriente). Es la mitad "cliente" de un sistema cliente-servidor: se encarga de exigir el "login" con carnet antes de dejar usar la PC, registrar el tiempo de sesión, recolectar telemetría de hardware de esa máquina, y sincronizar todo con el backend FastAPI del proyecto hermano `biblioteca_servidor`, con tolerancia a caídas de red.

Usuario final de la interfaz principal: el **estudiante** (o invitado) que quiere usar la PC — no un administrador. La administración (cerrar la app, ver reportes) se hace desde fuera: el panel web del servidor o un atajo/menú oculto en el propio kiosko.

## Qué hace en cada PC

1. Bloquea la PC detrás de una pantalla de login a pantalla completa hasta que alguien se identifica con su carnet.
2. Si el carnet no existe, ofrece un registro rápido (o acceso anónimo como invitado si el sector no es "Estudiante").
3. Mientras dura la sesión, muestra un widget flotante con el tiempo restante y permite cerrar sesión manualmente o por expiración automática.
4. Registra cada sesión (inicio/fin) en una base SQLite local y la sincroniza por lotes con el servidor.
5. En segundo plano, recolecta specs y salud de hardware (CPU, RAM, disco, temperatura) y las reporta periódicamente al servidor para seguimiento de mantenimiento preventivo.

## Arquitectura y stack

- **UI**: PyQt6 (`QApplication`, `QMainWindow`, `QStackedWidget`), tema visual en `ui/estilos.qss` (paleta institucional UES: rojo `#8B0E13`).
- **DB local**: SQLite puro (sin ORM), `PRAGMA journal_mode=WAL`, `sqlite3.Row` como row factory (`db/connection.py`).
- **Red**: `requests`, HTTP síncrono contra la URL del servidor configurada.
- **Hardware**: `psutil` (opcional, con fallback si no está instalado) + `smartctl` vía `subprocess` para el estado SMART del disco.
- **Procesos**: la UI y un servicio en segundo plano separados, comunicados por un socket UNIX local — ver **Separación entre la UI y el servicio**.
- **Concurrencia** (dentro del servicio): dos hilos daemon independientes — uno de sincronización general (`sync/`) y otro de telemetría de hardware (`hardware/agent.py`) — cada uno despertable bajo demanda vía `threading.Event`.

Dependencias (`cliente/requirements.txt`): `PyQt6`, `requests`, `psutil`.

### Estructura de carpetas

```
cliente/
├── main.py                # entry point de la UI (sin acceso a config.ini ni a la base local)
├── setup.py                # configuración inicial por PC (una sola vez): config.ini + .pc_id
├── core/
│   ├── config.py            # carga de config.ini + overrides por variable de entorno (solo el servicio)
│   ├── rutas.py              # BASE_DIR (código) y DATA_DIR (datos: config.ini, base local, clave, logs)
│   ├── ipc.py                # protocolo UI ↔ servicio y ruta del socket
│   ├── tiempo.py             # zona horaria y now_sv()
│   └── estado.py             # estado en memoria de la sesión activa
├── db/                      # acceso a SQLite local
│   ├── connection.py, schema.py, estudiantes.py, hardware.py, sesiones.py
├── hardware/
│   ├── collector.py           # recolección de specs/salud
│   ├── mantenimiento.py         # acumulación de horas de uso y estado de mantenimiento
│   └── agent.py                  # hilo daemon que orquesta ambos y reporta al servidor
├── network/                 # cliente HTTP delgado por dominio (estudiantes, hardware, sesiones)
├── servicio/                 # proceso en segundo plano: `python -m servicio`
│   ├── __main__.py            # arranque: init_db() → socket → sync → hardware
│   ├── operaciones.py         # lo que la UI puede pedir, con su validación
│   └── servidor.py            # socket UNIX: control de acceso y despacho
├── sync/__init__.py          # hilo daemon de sincronización general (sesiones + estudiantes + heartbeat)
├── ui/                       # pantallas: kiosko, login, registro, bienvenida, flotante
│   └── servicio.py            # cliente del socket del servicio
└── autostart/                # scripts de instalación/desinstalación/actualización en Linux
```

## Flujo de uso completo

1. **Arranque**: el servicio (`python -m servicio`) inicializa la DB local, abre el socket y lanza los hilos de sync y del agente de hardware. La UI (`main.py`) le pregunta al servicio si debe bloquear los atajos de escritorio y crea la `VentanaKiosko`.
2. **`VentanaKiosko`** (`ui/kiosko.py`): pantalla completa, sin bordes, siempre encima (`FramelessWindowHint | WindowStaysOnTopHint`). Contiene un `QStackedWidget` con login, registro y bienvenida/sesión, más un ícono de bandeja del sistema (menú: "Cerrar sesión" / "Salir (admin)") y un atajo oculto **`Ctrl+Shift+Alt+Q`** para salida administrativa.
3. **Login** (`ui/login.py`): el usuario escribe su carnet y la UI se lo pasa al servicio (`buscar_estudiante`). El servicio lo busca primero en la caché local (`estudiantes_cache`); si no está y hay conexión, se consulta al servidor (`GET /estudiantes/{carnet}`) y se cachea el resultado. Si no existe, invita a registrarse.
4. **Registro** (`ui/registro.py`): formulario con nombre, carnet, año de nacimiento, sector (Estudiante/Administrativo/Docente/Visitante), sede, facultad/departamento y carrera (listas en cascada), y género. Si el sector no es "Estudiante", no se piden datos personales y se otorga acceso anónimo (modo invitado, sesión sin carnet). Si hay datos de estudiante, la UI se los pasa al servicio (`guardar_estudiante`), que valida duplicado e intenta enviarlos al servidor (`POST`/`PUT /estudiantes`); si falla o no hay red, se guarda localmente marcado como pendiente de sincronizar.
5. **Sesión iniciada**: el servicio (`abrir_sesion`) genera un `uuid4` como id de sesión, lo guarda en `sesiones_pendientes` local y fija la duración; la UI se muestra una pantalla de bienvenida (`ui/bienvenida.py`, ~3s) y luego la ventana se oculta a la bandeja mostrando un **widget flotante** (`ui/flotante.py`) — un botón circular expandible con el tiempo restante, botón de "Actualizar mis datos" (oculto para invitados) y "Cerrar sesión". Arranca un temporizador de duración de sesión configurable.
6. **Cierre de sesión**: manual o por expiración automática. En ambos casos la UI llama a `cerrar_sesion` y el servicio registra `hora_fin`, se limpia el estado en memoria, se fuerza una sincronización inmediata (sin esperar el intervalo periódico), y se vuelve a mostrar el login en pantalla completa.
7. **Salida administrativa**: solo posible con el atajo secreto o el ítem "Salir (admin)" del menú de bandeja, y ambos exigen ingresar el PIN de administrador, que verifica el servicio (hash PBKDF2 en `[admin] pin_hash` de `config.ini`, con bloqueo tras 5 intentos fallidos) antes de cerrar cualquier sesión activa y terminar la aplicación. Si no hay PIN configurado, la salida queda bloqueada (falla cerrado, no hay bypass por defecto). El botón de cerrar normal del sistema operativo está interceptado: solo oculta la ventana a la bandeja, nunca cierra la app (comportamiento de kiosko real).

## Separación entre la UI y el servicio

El kiosko son **dos procesos**, pensados para correr con **usuarios del sistema distintos**:

- **Servicio** (`python -m servicio`, paquete `servicio/`): el único que lee `config.ini` (API key de la PC, hash del PIN de administrador), la clave de cifrado `db_key.bin` y la base local, y el único que habla con el servidor. Corre los hilos de sync y de hardware y atiende a la UI por un socket UNIX local.
- **UI** (`python main.py`): la interfaz PyQt. Corre con el usuario de la sesión gráfica, que es el mismo que usa el estudiante cuando el kiosko se oculta en la bandeja. No lee ningún archivo de datos: le pide al servicio operaciones concretas (`ui/servicio.py` → `core/ipc.py`).

Así el estudiante no puede leer la API key, descifrar la caché ni leer la base aunque salga del kiosko (navegador con `file://`, un gestor de archivos...): esos archivos pertenecen a otro usuario. Separar los procesos solo protege si además los usuarios son distintos y los datos viven fuera del alcance del usuario de la sesión (`BIBLIOTECA_DATA_DIR` con permisos `700`).

**Protocolo** (`core/ipc.py`): una petición JSON por conexión (`{"op": ..., "args": {...}}`) y una respuesta JSON, con un límite de 64 KiB por mensaje. Operaciones disponibles (`servicio/servidor.py::OPERACIONES`): `info`, `buscar_estudiante`, `guardar_estudiante`, `abrir_sesion`, `cerrar_sesion`, `estado_pin_admin`, `verificar_pin_admin`. No hay operaciones genéricas para leer la base ni la configuración.

**Controles del servicio sobre lo que pide la UI**, que se trata como entrada no confiable:
- Solo acepta conexiones del propio usuario del servicio o de miembros del grupo `[servicio] grupo_ui` (por defecto `kiosko-ui`), según las credenciales que da el kernel (`SO_PEERCRED`). El socket se crea con modo `0660`.
- Valida tipos, longitudes y el formato del carnet de cada argumento.
- Genera él mismo el id y la hora de inicio de cada sesión y toma los datos del estudiante de su propia caché, no de la UI. Si se abre una sesión con otra ya abierta, cierra la anterior.
- `guardar_estudiante` en modo `actualizar` solo acepta el carnet con la sesión activa en esta PC.
- El contador de intentos fallidos del PIN de administrador se aplica en el servicio, así que tampoco se esquiva hablándole directamente al socket.
- Al recibir `SIGTERM` (apagado, reinicio, `systemctl stop`) cierra la sesión activa con su hora real de fin.

**Ubicación del socket** (`core/ipc.py::ruta_socket`): `BIBLIOTECA_SOCKET` si está definida; si no, `/run/biblioteca/kiosko.sock` cuando existe ese directorio (instalación con systemd) o, en desarrollo, `$XDG_RUNTIME_DIR/biblioteca-kiosko.sock`. La ruta debe tener menos de 108 caracteres (límite de los sockets UNIX).

**Si el servicio no responde**, la UI no se cierra: el login y el registro muestran "El sistema no está disponible", la salida de administrador queda bloqueada (falla cerrado) y los atajos de escritorio se bloquean igual.

**Desarrollo con un solo usuario**, desde `cliente/`, en dos terminales:

```bash
python -m servicio   # 1) servicio: config.ini, base local, sync, hardware
python main.py       # 2) interfaz
```

Con un solo usuario se prueba el funcionamiento, pero no el aislamiento: para eso hacen falta los dos usuarios del sistema.

## Modelo de datos local (`db/schema.py`)

Tres tablas SQLite, con migraciones idempotentes que corren en cada arranque:

| Tabla | Campos principales | Propósito |
|---|---|---|
| `sesiones_pendientes` | `id` (PK, uuid), `pc_id`, `carnet` (nullable → modo invitado), `hora_inicio`, `hora_fin`, `fecha`, `sincronizado`, `timestamp_sync` | Cola de sesiones de uso a enviar al servidor vía `/sync` |
| `estudiantes_cache` | `carnet` (PK), `nombre`, `carrera`, `facultad`, `fecha_nacimiento`, `sexo`, `sincronizado`, `pendiente_modo` ("crear"/"actualizar") | Caché local de estudiantes consultados o registrados sin conexión |
| `hardware_local` | `pc_id` (PK), `horas_acumuladas`, `ultimo_heartbeat`, `ultimo_mantenimiento_conocido` | Acumulador de horas de encendido de esta PC para el agente de hardware |

Migraciones destacadas: `_migrar_carnet_nullable` (reconstruye `sesiones_pendientes` si `carnet` aún tiene la vieja restricción `NOT NULL`, necesaria para el modo invitado) y `_migrar_estudiantes_pendientes` (añade columnas `sincronizado`/`pendiente_modo` si faltan).

El daemon de sincronización lee lo pendiente de ambas tablas, lo envía al servidor y marca como sincronizado tras éxito; las sesiones se enriquecen con datos del estudiante desde la caché antes de enviarse.

## Agente de hardware

- **`hardware/collector.py`**:
  - `identificacion()`: hostname y MAC — solo para inventario legible; la clave real de la PC es siempre `.pc_id`.
  - `specs()`: CPU, RAM total, almacenamiento total, sistema operativo.
  - `salud()`: temperatura promedio de CPU y estado SMART del disco (vía `smartctl -H` sobre discos candidatos como `/dev/sda`, `/dev/nvme0n1`, `/dev/vda`). Todo best-effort: nunca lanza excepción, devuelve `None` si no puede leer un dato.
  - `uptime_sistema_segundos()`: uptime del sistema operativo, usado solo para "sembrar" el acumulador de horas la primera vez.
- **`hardware/mantenimiento.py`**:
  - `calcular_estado(horas)`: `"optimo"` (&lt;300h), `"pendiente"` (≥300h), `"critico"` (&gt;400h) — mismos umbrales que usa el servidor.
  - `registrar_heartbeat(pc_id, uptime_seed_segundos)`: acumula horas reales transcurridas desde el último heartbeat.
  - `aplicar_reset_si_corresponde(pc_id, ultimo_mantenimiento_servidor)`: resetea el acumulador local a 0 si el servidor indica un mantenimiento más reciente que el conocido localmente.
- **`hardware/agent.py`**: hilo daemon que, cada `HARDWARE_INTERVAL_SEGUNDOS` (default 300s), arma un payload completo (identificación + specs + salud + horas acumuladas) y lo envía al servidor; aplica el posible reset de mantenimiento según la respuesta. Loguea en `hardware.log`. Expone `forzar_lectura()` para disparar un ciclo bajo demanda.

## Capa de red (`network/`)

- **`client.py`**: `hay_conexion(timeout=5)` — `GET /health` al servidor, usado como chequeo previo antes de cualquier sincronización.
- **`estudiantes.py`**: `obtener_estudiante(carnet)` (`GET /estudiantes/{carnet}`), `registrar_estudiante(datos)` (`POST /estudiantes`, acepta 200/201/409 como éxito), `actualizar_estudiante(carnet, datos)` (`PUT /estudiantes/{carnet}`).
- **`hardware.py`**: `enviar_hardware(pc_id, payload)` (`POST /pcs/{pc_id}/hardware`), devuelve el JSON de respuesta del servidor (incluye `ultimo_mantenimiento`) o `None` si falla.
- **`sesiones.py`**: `enviar_estado(payload)` (`POST /estado`, heartbeat de sesión activa) y `enviar_sesiones(payload)` (`POST /sync`, batch de sesiones cerradas pendientes).

Todas las funciones de red capturan cualquier excepción y devuelven `None`/`False` en caso de fallo, sin propagar errores — esto hace trivial el patrón de reintento en el siguiente ciclo del daemon correspondiente.

## Sincronización general (`sync/__init__.py`)

Hilo daemon que, cada `SYNC_INTERVAL` (default 30s), en cada ciclo:
1. Verifica conexión (`hay_conexion()`); si no hay, salta el ciclo.
2. Reenvía estudiantes pendientes de sincronizar (registros/actualizaciones hechos offline).
3. Envía un heartbeat del estado actual de la sesión (`POST /estado`).
4. Envía las sesiones cerradas pendientes (`POST /sync`), enriquecidas con datos del estudiante desde la caché; marca como sincronizado lo que el servidor confirma.

Loguea todo en `sync.log`. Expone `forzar_sync()`, invocado tras login, logout o actualización de datos, para no esperar el intervalo completo.

## Configuración

### `setup.py` — configuración inicial (una vez por PC)
Pide nombre de PC (default `PC-01`), URL del servidor (default `http://localhost:8000`), genera (o reutiliza) el `PC_ID` de esta PC y lo muestra en pantalla, pide la API key de esta PC (`KIOSK_API_KEY`, generada desde el panel admin del servidor para ese `PC_ID` puntual — pestaña "PCs", botón "Generar API key"; se ve una sola vez ahí) y un PIN de administrador para "Salir (admin)" (se pide oculto con `getpass` y se guarda como hash SHA-256, nunca en texto plano); escribe `config.ini` e inicializa la base de datos local. Corrido a mano (desarrollo) ofrece instalar un autostart de usuario; en producción lo ejecuta `autostart/instalar_linux.sh` como el usuario del servicio, con `--sin-autostart`. Si se deja el PIN vacío, avisa que la salida administrativa quedará bloqueada hasta configurarlo.

```bash
cd cliente
pip install -r requirements.txt
# Si falla con "externally-managed-environment" (PEP 668, común en distros
# recientes con Python del sistema): pip install --break-system-packages -r requirements.txt
# — justificado porque la PC es de uso dedicado (kiosko), ver docs/desarrollo/despliegue.md.
python setup.py
```

### `core/config.py` — carga de configuración
Lee `config.ini` (secciones `[pc]`, `[servidor]`, `[sync]`, `[admin]`) y permite **override por variable de entorno** en cada valor:

| Variable | Fuente en `config.ini` | Variable de entorno | Default |
|---|---|---|---|
| `SERVER_URL` | `[servidor] url` | `BIBLIOTECA_SERVER_URL` | `http://localhost:8000` |
| `KIOSK_API_KEY` | `[servidor] kiosk_key` | `BIBLIOTECA_KIOSK_KEY` | `""` (vacío → login/registro fallará con 401). Es la key propia de esta PC (generada desde el panel para su `PC_ID`), no un valor compartido con las demás — el cliente la manda junto con `X-PC-Id` en cada request. |
| `ADMIN_PIN_HASH` | `[admin] pin_hash` | `BIBLIOTECA_ADMIN_PIN_HASH` | `""` (vacío → "Salir (admin)" queda bloqueado) |
| `PC_ID` | archivo `.pc_id` | — (solo por archivo) | uuid4 generado |
| `PC_NOMBRE` | `[pc] nombre` | `BIBLIOTECA_PC_NOMBRE` | `PC-00` |
| `SYNC_INTERVAL` | `[sync] intervalo_segundos` | — | 30 |
| `DURACION_SESION_MINUTOS` | `[sesion] duracion_minutos` | — | 60 |
| `HARDWARE_INTERVAL_SEGUNDOS` | `[hardware] intervalo_segundos` | — | 300 |
| `BLOQUEAR_ATAJOS_ESCRITORIO` | `[escritorio] bloquear_atajos` | `BIBLIOTECA_BLOQUEAR_ATAJOS` | `true` |
| `GRUPO_UI` | `[servicio] grupo_ui` | `BIBLIOTECA_GRUPO_UI` | `kiosko-ui` (grupo del sistema cuyos miembros pueden usar el socket del servicio) |

Fuera de `config.ini`, porque también las necesita la UI o se leen antes que él:

| Variable de entorno | Uso | Default |
|---|---|---|
| `BIBLIOTECA_DATA_DIR` | Directorio de `config.ini`, `.pc_id`, `db_key.bin`, `biblioteca_local.db` y los logs del servicio | junto al código (`cliente/`); `/var/lib/biblioteca-kiosko` con `instalar_linux.sh` |
| `BIBLIOTECA_SOCKET` | Ruta del socket UI ↔ servicio (la misma en los dos procesos) | ver **Separación entre la UI y el servicio** |
| `BIBLIOTECA_UI_LOG` | Archivo de log opcional de la UI | sin archivo (solo stderr) |

`core/config.py` solo lo importa el servicio. `TZ_SV = ZoneInfo("America/El_Salvador")` y `now_sv()`, usados en toda la app para timestamps consistentes, están en `core/tiempo.py`.

`config.ini` y `.pc_id` están en `.gitignore` (son específicos de cada máquina) — se generan con `setup.py`.

## Instalación en Linux (`cliente/autostart/`)

- **`instalar_linux.sh`** (`sudo ./instalar_linux.sh --usuario-ui <usuario> [--ca-cert ca.pem]`): instala el kiosko con los privilegios separados. El código va a `/opt/biblioteca-kiosko` (propiedad de root, con su propio venv), y los datos a `/var/lib/biblioteca-kiosko` (propiedad del usuario de sistema `kiosko-svc`, permisos `700`). El servicio se instala como unidad systemd con ese usuario, y su socket queda en `/run/biblioteca`, accesible solo para el grupo `kiosko-ui`. La UI va a `/etc/xdg/autostart` para las cuentas de ese grupo. Ejecuta `setup.py` como `kiosko-svc` y, opcionalmente, bloquea `file://` fuera de `/home` en los navegadores y aplica el bloqueo de escritorio. El usuario de la UI no puede tener sudo. `--solo-codigo` reinstala solo el código y las dependencias.
- **`actualizar_linux.sh`** (sin sudo): valida que no haya cambios sin commit, hace `git fetch` + `git merge --ff-only` y, si se confirma, ejecuta `sudo ./instalar_linux.sh --solo-codigo`, que reinicia el servicio. Con `git config biblioteca.verificarFirmas true` en el checkout de la PC, antes del merge exige que el commit nuevo tenga una firma válida (`git verify-commit`); requiere que el equipo firme sus commits y que las claves públicas estén importadas en la PC.
- **`desinstalar_linux.sh`** (con sudo): elimina el servicio, el autostart, `/opt/biblioteca-kiosko` y las políticas del navegador. Pregunta si además borrar la configuración, la base local (advirtiendo sobre posibles sesiones no sincronizadas) y el usuario `kiosko-svc`.

Detalle completo en [`docs/desarrollo/despliegue.md`](docs/desarrollo/despliegue.md).

## Detalles importantes / peculiaridades

- **Modo kiosko real**: la ventana está siempre encima y sin bordes, y el botón de cerrar del sistema operativo está interceptado (solo oculta a la bandeja). Esto es control a nivel de aplicación Qt — no evita atajos que el propio *compositor* de GNOME captura antes de que el evento llegue a la ventana (p. ej. la tecla **Super**, que abre el resumen de Actividades, desde donde además se puede lanzar cualquier icono fijado en el dock — Firefox, Files, Terminal, etc. — sin pasar por el login). `core/bloqueo_escritorio.py` deshabilita vía `gsettings`, en cada arranque de `main.py`: tecla Super/`overlay-key`, Alt+Tab y variantes, "mostrar escritorio", el atajo de terminal (`Ctrl+Alt+T`) y el dock entero (`favorite-apps`, así no quedan iconos para lanzar otra app desde el resumen de Actividades). Es best-effort (solo actúa si detecta GNOME por `XDG_CURRENT_DESKTOP` y si hay `gsettings` disponible) y se puede desactivar con `[escritorio] bloquear_atajos = false` en `config.ini` (o `BIBLIOTECA_BLOQUEAR_ATAJOS=false`). Es una restricción de usuario, no de sistema: son claves dconf del usuario que ejecuta el kiosko — alguien con una terminal como ese mismo usuario podría revertirlas con `gsettings set` dentro de una sesión ya abierta (se reaplican en el próximo arranque del kiosko, no al instante).
- **`.pc_id`**: archivo de texto plano con un UUID4, generado una sola vez. Es el identificador **estable y persistente** de la PC física usado en todos los payloads hacia el servidor — independiente del hostname/MAC, que solo sirven para inventario legible.
- **Tolerancia a fallos de red**: cualquier excepción en la capa `network/` se traga y los datos quedan marcados como pendientes en SQLite, reintentándose automáticamente en el siguiente ciclo del daemon de sync, sin intervención del usuario.
- **Modo invitado**: si el sector elegido no es "Estudiante", no se piden datos personales; la sesión se guarda con `carnet=None`. Requiere la migración `_migrar_carnet_nullable` en bases de datos creadas antes de que existiera este modo.
- **Logs**: el servicio escribe `servicio.log`, `sync.log` y `hardware.log` en `DATA_DIR` (regenerables, no versionados en git), con rotación (1 MB × 3 copias cada uno). En lugar del carnet registran un seudónimo (`est-…`, HMAC con la clave de `db_key.bin`, ver `db/cifrado.py::seudonimo`): es estable dentro de la misma PC, así que se puede seguir a un estudiante en el log, pero no revela el carnet. La UI solo escribe en stderr, sin carnets, porque su cuenta la comparten todos los estudiantes; `BIBLIOTECA_UI_LOG=<ruta>` activa además un archivo para desarrollo.
- Los estilos y el catálogo de sedes/facultades/carreras en `ui/registro.py` replican la identidad visual y la oferta académica real de la UES (Facultad Multidisciplinaria de Oriente).

## Mapa del código (`.codebase-memory/`)

El repo mantiene un **grafo de conocimiento del código** (funciones, clases, archivos, imports, llamadas entre símbolos) indexado con `codebase-memory-mcp`, más un dashboard HTML que lo visualiza. Sirve para explorar la arquitectura real del proyecto (quién llama a quién, qué archivos concentran más lógica, etc.) sin tener que leer todo el código.

```
.codebase-memory/
├── graph.db.zst              # snapshot comprimido del grafo (nodos + aristas), para compartir sin reindexar
├── artifact.json              # metadata del último snapshot: commit, fecha, nº de nodos/aristas, tamaño
├── generate_dashboard.py     # script generador del dashboard (lee la DB del grafo, escribe dashboard.html)
├── dashboard.template.html    # plantilla del dashboard (CSS/JS embebido, no cambia entre regeneraciones)
└── dashboard.html             # dashboard generado — no versionado, se regenera solo
```

### Ver el dashboard
Abre `.codebase-memory/dashboard.html` directamente en el navegador (doble clic). Muestra nodos/aristas por tipo, los archivos con más símbolos, las funciones/métodos más conectados (fan-in/fan-out) y un explorador de símbolos con búsqueda y filtros.

### (Re)indexar el proyecto
El índice se genera con la herramienta MCP `codebase-memory-mcp` (`index_repository`), normalmente invocada por Claude Code al explorar o modificar el código. Para forzar una reindexación completa manualmente, pídeselo a Claude ("indexa el proyecto de nuevo") o usa la skill `codebase-memory`.

### Actualización automática del dashboard
Hay un hook (`PostToolUse`) configurado en `.claude/settings.json` que, tras cada reindexación, corre `generate_dashboard.py` automáticamente para que `dashboard.html` siempre refleje el último estado del código. Para regenerarlo a mano en cualquier momento:

```bash
python3 .codebase-memory/generate_dashboard.py
```

## Requisitos previos

- Python 3.x con PyQt6 disponible (en Linux, puede requerir paquetes del sistema para Qt según la distro).
- Acceso de red al backend `biblioteca_servidor` (local o remoto).
- Opcional: `smartctl` instalado en el sistema para reportar salud SMART del disco.
