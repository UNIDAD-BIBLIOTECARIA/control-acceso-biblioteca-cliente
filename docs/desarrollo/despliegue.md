# Despliegue — Biblioteca Cliente

Documentación de desarrollo, parte 1 de 2. Para cómo está organizado el código, ver [`estructura.md`](./estructura.md). Para la guía de uso del kiosko, ver [`../usuario.md`](../usuario.md).

Este componente se instala **en cada PC de la sala** (kiosko). Requiere que `biblioteca_servidor` ya esté desplegado y accesible por red — ver `biblioteca_servidor/docs/desarrollo/despliegue.md`.

## Requisitos previos

- Linux con systemd y Python 3.10 o superior con el módulo `venv` (`python3-venv` en Debian/Ubuntu). PyQt6 se instala en el venv con pip, pero puede requerir paquetes del sistema para Qt según la distro (p. ej. `libxcb-cursor0`).
- Acceso de red al backend `biblioteca_servidor` (local o remoto).
- Opcional: `smartctl` instalado en el sistema para reportar salud SMART del disco. `smartctl -H` necesita root y el servicio corre como `kiosko-svc`, así que en una instalación con `instalar_linux.sh` el estado SMART se reporta vacío; la temperatura y el resto de la telemetría no se ven afectados.

## Instalación inicial en una PC nueva

Antes de instalar, crear la cuenta sin privilegios con la que el estudiante va a usar la sesión gráfica (p. ej. `sudo adduser estudiante`). **No puede tener sudo**: el instalador se niega a usar una cuenta de los grupos `sudo`, `wheel` o `admin`. En Debian/Ubuntu hace falta además `python3-venv`.

```bash
git clone <repo> && cd control-acceso-biblioteca-cliente/cliente/autostart
sudo ./instalar_linux.sh --usuario-ui estudiante [--ca-cert /ruta/ca.pem]
```

`instalar_linux.sh` instala el código, crea el usuario del servicio y lanza `setup.py` como ese usuario (ver **Instalar** más abajo). `setup.py` es el asistente de configuración inicial, se corre **una sola vez por PC**. Pide:

1. **Nombre de la PC** (default `PC-01`) — identificador legible, se muestra en el panel admin.
2. **URL del servidor** (default `http://localhost:8000`) — apuntar a la IP/dominio real de la PC maestra donde corre `biblioteca_servidor`. Si no es `http://localhost`/`127.0.0.1`, exige `https://` salvo que confirmes explícitamente que asumís el riesgo de usar `http://` sin cifrar — ver sección **TLS** más abajo.
3. **Certificado de la CA interna** (`ca.pem`), solo si elegiste `https://` — necesario para validar el servidor cuando usa un certificado propio (no de una CA pública). Ver sección **TLS**.
4. En este punto genera (o reutiliza) `.pc_id` — UUID4 que identifica a esta PC de forma estable, independiente del hostname/MAC — y lo muestra en pantalla.
5. **API key de esta PC** (`KIOSK_API_KEY`) — se genera desde el panel admin del servidor (pestaña "PCs", botón "Generar API key") usando el `PC_ID` que acaba de mostrar el paso anterior; el valor solo se ve una vez ahí. Sin esto, el login y registro de estudiantes fallan con 401.
6. **PIN de administrador** para la salida administrativa del kiosko (se pide oculto con `getpass`, se guarda como hash SHA-256, nunca en texto plano). Si se deja vacío, la salida administrativa queda bloqueada hasta configurarlo.

Luego `setup.py`:
- Escribe `config.ini` con todo lo anterior.
- Inicializa la base de datos SQLite local.
- Si se corre a mano (desarrollo), ofrece instalar un autostart de usuario. Con `--sin-autostart`, que es como lo llama `instalar_linux.sh`, no lo ofrece.

El kiosko son dos procesos: el servicio (`python -m servicio`), que lee `config.ini` y la base local y habla con el servidor, y la interfaz (`python main.py`), que solo le habla al servicio por un socket local. Tienen que correr con usuarios del sistema distintos para que el estudiante no pueda leer `config.ini` ni la base; ver [`estructura.md`](./estructura.md), sección **Separación entre la UI y el servicio**. `config.ini`, `.pc_id`, `db_key.bin`, la base y los logs del servicio van en `BIBLIOTECA_DATA_DIR`: `/var/lib/biblioteca-kiosko` en una instalación con `instalar_linux.sh`, junto al código en desarrollo.

## Binario empaquetado (PyInstaller, CI)

`.github/workflows/pip-audit.yml` (job `build-cliente`) empaqueta `main.py` con PyInstaller (`cliente/build.spec`) en cada push/PR y sube el resultado como artifact (`biblioteca-kiosko-linux`, 30 días de retención) — sirve como verificación automática de que el kiosko sigue empaquetando y arrancando (smoke test headless), y como forma de bajar un build ya armado sin instalar Python en la PC destino.

**Sigue siendo `onedir`, no `onefile`**: el resultado es una carpeta (`biblioteca-kiosko/` con el ejecutable y `_internal/` al lado), no un solo archivo. Es a propósito — `config.ini`, `.pc_id` y la base SQLite local se guardan junto al código (`_internal/`), y en un bundle `onefile` esa carpeta se recrearía vacía en un directorio temporal distinto cada vez que se abre la app, perdiendo la identidad de la PC y el caché local en cada reinicio. Para usarlo hay que copiar la carpeta `biblioteca-kiosko/` completa, no solo el ejecutable.

**Limitación actual: `setup.py` no está empaquetado**, solo `main.py`. El binario no tiene el asistente de primera configuración — antes de usarlo en una PC hace falta generar su `config.ini`/`.pc_id` (con `python setup.py` desde un checkout del código, ver más abajo) y copiar esos dos archivos dentro de `_internal/` de la carpeta empaquetada. Como cada PC necesita su propio `.pc_id`/API key (ver siguiente sección), **no se puede copiar la misma carpeta ya configurada a las 16 PCs** — cada una necesita su propio `setup.py` + su propia copia de `_internal/config.ini`/`_internal/.pc_id`, o (más simple hoy) seguir instalando desde código fuente como abajo. Este binario es, por ahora, sobre todo una verificación de CI, no todavía el método de despliegue recomendado. Además, solo empaqueta la interfaz: el servicio (`python -m servicio`) no está incluido en el binario.

## Instalación en Linux (`cliente/autostart/`)

### Instalar

```bash
cd cliente/autostart
sudo ./instalar_linux.sh --usuario-ui estudiante [--ca-cert /ruta/ca.pem]
```

Separa los privilegios del servicio y de la sesión del estudiante:

| Qué | Dónde | Dueño y permisos |
|---|---|---|
| Código (copia del checkout, sin `tests/` ni datos) | `/opt/biblioteca-kiosko/app` | `root`, `755`/`644`: nadie más puede modificarlo |
| Dependencias | `/opt/biblioteca-kiosko/venv` (venv propio, sin `--break-system-packages`) | `root` |
| Datos: `config.ini`, `.pc_id`, `db_key.bin`, `biblioteca_local.db`, `ca.pem`, logs | `/var/lib/biblioteca-kiosko` | `kiosko-svc`, `700` |
| Socket del servicio | `/run/biblioteca/kiosko.sock` (directorio creado por `/etc/tmpfiles.d/biblioteca-kiosko.conf`) | directorio `kiosko-svc:kiosko-ui 750`, socket `660` |
| Servicio | `/etc/systemd/system/biblioteca-kiosko.service`, `User=kiosko-svc`, con `ProtectSystem=strict`, `ProtectHome` y `NoNewPrivileges` | — |
| UI | `/etc/xdg/autostart/biblioteca-kiosko.desktop` → `/opt/biblioteca-kiosko/bin/biblioteca-kiosko-ui` | solo arranca para miembros de `kiosko-ui` |

Pasos:
1. Crea el grupo `kiosko-ui` y el usuario de sistema `kiosko-svc` (sin shell ni home), y añade a los dos (`kiosko-svc` y el usuario de la UI) al grupo.
2. Copia el código a `/opt`, crea el venv, instala `requirements.txt` y precompila los `.pyc`.
3. Crea `/var/lib/biblioteca-kiosko`, copia ahí el `ca.pem` si se pasó `--ca-cert` y ejecuta `setup.py --sin-autostart` como `kiosko-svc` (si ya hay `config.ini`, pregunta antes de reconfigurar). Si hay `ca.pem`, a la pregunta de la CA se responde `ca.pem`.
4. Instala y arranca el servicio systemd.
5. Instala el autostart de la UI y borra los `.desktop` de instalaciones anteriores en el home del usuario de la UI, porque tendrían prioridad sobre el de `/etc/xdg/autostart`.
6. Opcional (por defecto sí): políticas de Chrome, Chromium y Firefox que bloquean `file://` fuera de `/home`. Dentro de `/home` se permite, para que el estudiante pueda abrir en el navegador lo que descargó (un PDF, por ejemplo). Lo que protege los datos del kiosko son los permisos, no esta política: aunque se la salten, `/var/lib/biblioteca-kiosko` no es legible para el estudiante.
7. Opcional: el bloqueo de escritorio a nivel de sistema (`bloquear_sistema_linux.sh`).

Hay que reiniciar la PC (o cerrar la sesión del estudiante) para que tome el grupo `kiosko-ui`. Comprobación, las dos deben fallar con `Permiso denegado`:

```bash
sudo -u estudiante cat /var/lib/biblioteca-kiosko/config.ini
sudo -u estudiante touch /opt/biblioteca-kiosko/app/main.py
```

Es idempotente: se puede volver a correr sin perder la configuración. Logs del servicio: `journalctl -u biblioteca-kiosko` y `/var/lib/biblioteca-kiosko/*.log`.

### Actualizar

```bash
cd cliente/autostart
./actualizar_linux.sh
```

Se corre desde tu usuario, sin sudo. Valida que no haya cambios locales sin commit, hace `git fetch` + `git merge --ff-only` y, si confirmás, ejecuta `sudo ./instalar_linux.sh --solo-codigo`, que vuelve a copiar el código a `/opt`, actualiza las dependencias del venv y reinicia el servicio. Reiniciar el servicio cierra la sesión activa del estudiante. La UI toma el código nuevo en el siguiente inicio de sesión gráfica.

### Desinstalar

```bash
cd cliente/autostart
sudo ./desinstalar_linux.sh
```

Detiene y elimina el servicio (al detenerse cierra la sesión activa), el autostart de la UI, `/opt/biblioteca-kiosko` y las políticas del navegador que puso el instalador. Borra los logs y pregunta si además borrar:
- `config.ini`/`.pc_id`/`ca.pem` (para reconfigurar la PC desde cero).
- `biblioteca_local.db` y `db_key.bin` (**advierte** sobre posibles sesiones no sincronizadas — no borrar si hay sospecha de sesiones pendientes de enviar al servidor).
- Si ya no quedan datos, el usuario `kiosko-svc` y el grupo `kiosko-ui`.

El bloqueo de escritorio no se revierte: para eso está `desbloquear_sistema_linux.sh`.

## Configuración (`config.ini` + variables de entorno)

`config.ini` y `.pc_id` están en `.gitignore` (específicos de cada máquina) — se generan con `setup.py`, no se versionan.

| Variable | Fuente en `config.ini` | Variable de entorno (override) | Default |
|---|---|---|---|
| `SERVER_URL` | `[servidor] url` | `BIBLIOTECA_SERVER_URL` | `http://localhost:8000` |
| `PERMITIR_HTTP_INSEGURO` | `[servidor] permitir_http_inseguro` | `BIBLIOTECA_PERMITIR_HTTP` | `false` — con `SERVER_URL` en `http://` hacia un host que no es localhost, la app rehúsa arrancar salvo que esto sea `true` |
| `CA_CERT_PATH` | `[servidor] ca_cert` | `BIBLIOTECA_CA_CERT` | `""` (vacío → usa el almacén de CAs del sistema; poner acá el `ca.pem` de la CA interna si el servidor no tiene un certificado público) |
| `KIOSK_API_KEY` | `[servidor] kiosk_key` | `BIBLIOTECA_KIOSK_KEY` | `""` (vacío → login/registro falla con 401). Key propia de esta PC (generada desde el panel para su `PC_ID`, no compartida con las demás) — se manda junto con `X-PC-Id` en cada request. |
| `ADMIN_PIN_HASH` | `[admin] pin_hash` | `BIBLIOTECA_ADMIN_PIN_HASH` | `""` (vacío → salida admin bloqueada) |
| `PC_ID` | archivo `.pc_id` | — (solo por archivo) | uuid4 generado |
| `PC_NOMBRE` | `[pc] nombre` | `BIBLIOTECA_PC_NOMBRE` | `PC-00` |
| `SYNC_INTERVAL` | `[sync] intervalo_segundos` | — | 30 |
| `DURACION_SESION_MINUTOS` | `[sesion] duracion_minutos` | — | 60 |
| `HARDWARE_INTERVAL_SEGUNDOS` | `[hardware] intervalo_segundos` | — | 300 |
| `BLOQUEAR_ATAJOS_ESCRITORIO` | `[escritorio] bloquear_atajos` | `BIBLIOTECA_BLOQUEAR_ATAJOS` | `true` |
| `GRUPO_UI` | `[servicio] grupo_ui` | `BIBLIOTECA_GRUPO_UI` | `kiosko-ui` (grupo del sistema cuyos miembros pueden usar el socket del servicio; si no existe, solo el propio usuario del servicio) |

Fuera de `config.ini`: `BIBLIOTECA_DATA_DIR` (directorio de datos del servicio), `BIBLIOTECA_SOCKET` (ruta del socket, la misma para el servicio y la UI) y `BIBLIOTECA_UI_LOG` (archivo de log opcional de la UI). Ver `estructura.md`.

## TLS (cifrado entre el kiosko y el servidor)

`SERVER_URL` hacia cualquier host que no sea `localhost`/`127.0.0.1` tiene que ser `https://` — si no, `core/config.py` lanza `RuntimeError` al arrancar (ver `_validar_server_url`). Es intencional: sin TLS, la PII de los estudiantes y el header `X-Kiosk-Key` viajan en texto plano por la red del laboratorio.

Como el servidor normalmente no tiene un dominio público (solo una IP de LAN), no aplica una CA pública tipo Let's Encrypt — `biblioteca_servidor/servidor/scripts/generar_ca.sh` genera una **CA interna propia** y un certificado para la IP del servidor. Pasos:

1. En la PC maestra, generar la CA y el certificado del servidor (ver `biblioteca_servidor/docs/desarrollo/despliegue.md`, sección TLS) — produce, entre otros, `ca.pem`.
2. Copiar ese `ca.pem` a cada PC hija y pasarlo al instalador con `--ca-cert /ruta/ca.pem`, que lo deja en `/var/lib/biblioteca-kiosko/ca.pem`.
3. En `setup.py`, al elegir `https://`, responder `ca.pem` cuando pregunte por la CA (es la respuesta por defecto si el instalador ya lo copió) — o completarlo a mano después en `config.ini`:
   ```ini
   [servidor]
   url = https://192.168.x.x:8000
   ca_cert = ca.pem
   ```
4. Si el servidor certificado por la CA interna todavía no está listo y hace falta seguir operando en `http://` mientras tanto, hay que asumirlo a propósito con `permitir_http_inseguro = true` en `config.ini` — nunca es el comportamiento por defecto.

Certificado del servidor con vencimiento ~825 días (ver script) — calendarizar su renovación, no hay renovación automática como con una CA pública.

## Bloqueo de escritorio para producción (evita fuga de `config.ini` y del código)

`core/bloqueo_escritorio.py` deshabilita atajos de GNOME (Activities, Alt+Tab, dock, terminal) escribiendo dconf **de usuario** (`gsettings set`) en cada arranque del kiosko. Es best-effort a propósito y tiene un límite conocido, documentado en su propio docstring: son claves reversibles por cualquiera que consiga una terminal en esa misma sesión (`gsettings set ...` las pisa de nuevo, sin esperar al próximo arranque del kiosko). Y una vez con terminal en esa cuenta, el problema deja de ser leer `config.ini` (`KIOSK_API_KEY` de esta PC + hash del PIN admin; comprometerla solo afecta a este equipo, ver `KIOSK_API_KEY` en la tabla de variables arriba) — ya hay acceso de red y al código fuente completos, con o sin el archivo. Verificado además que la propia app (`ui/`, `core/`) no expone ningún `QFileDialog` ni diálogo de impresión: toda la superficie de escape viene del entorno de escritorio, no de la app.

Para que el bloqueo sobreviva a una terminal abierta como ese mismo usuario, hace falta reforzarlo a nivel de sistema — esto **complementa** a `bloqueo_escritorio.py`, no lo reemplaza. Está automatizado en `cliente/autostart/bloquear_sistema_linux.sh` (requiere sudo, idempotente):

```bash
cd cliente/autostart
./bloquear_sistema_linux.sh
```

El script aplica, en orden:

1. **dconf de sistema, con locks** (en vez de solo dconf de usuario) — mismas claves que deshabilita `bloqueo_escritorio.py` (Activities, Alt+Tab, dock, atajo de terminal), pero escritas en `/etc/dconf/db/local.d/` + `/etc/dconf/db/local.d/locks/` y aplicadas con `dconf update`. A diferencia del dconf de usuario, estas quedan fijadas para cualquier usuario del sistema — `gsettings set` desde una terminal ya no las puede revertir.
2. **Bloqueo de cambio de terminal virtual** (`Ctrl+Alt+F2`), vía un drop-in en `/etc/systemd/logind.conf.d/90-kiosko.conf` (`NAutoVTs=1`, `ReserveVT=1`).
3. **Desinstalar terminal y explorador de archivos** (`gnome-terminal`, `xterm`, `nautilus`) — opcional, se pregunta antes de ejecutar porque en algunas distros puede arrastrar otros paquetes del entorno GNOME. Si no están instalados, ningún atajo (cubierto o no por el punto 1) puede alcanzarlos.

Ya está enganchado como paso opcional al final de `instalar_linux.sh` (se pregunta después de las políticas del navegador). Para revertir dconf + TTY (no la desinstalación de paquetes): `./desbloquear_sistema_linux.sh`.

Lo único que el script **no puede automatizar**, porque requiere acceso físico a la BIOS/UEFI de cada PC:

4. **BIOS/UEFI con contraseña de administrador**: deshabilitar boot por USB/medios externos y el modo recovery/single-user de GRUB. Sin esto, alguien arranca un live USB y monta el disco directamente — ningún bloqueo de la sesión gráfica importa en ese escenario.

Con las cuatro capas aplicadas no queda, dentro de la sesión del kiosko, ninguna ruta hacia una terminal ni un explorador de archivos. Aun si el estudiante consiguiera una, `config.ini`, la clave de cifrado y la base pertenecen a `kiosko-svc` (permisos `700`) y el código es de `root` (ver **Instalar**), así que no puede leerlos ni modificarlos. El bloqueo de escritorio evita que se salga del kiosko; la separación de usuarios protege las credenciales si aun así se sale.

Mejora futura (no bloqueante): reemplazar la sesión GNOME completa por un compositor mínimo dedicado (p. ej. `cage`) que solo lance la app del kiosko, eliminando la superficie de escape por construcción en vez de ir deshabilitando atajos de GNOME uno por uno.

## Checklist antes de poner una PC en producción

- [ ] `KIOSK_API_KEY` generada desde el panel del servidor específicamente para el `PC_ID` de esta PC (pestaña "PCs" → "Generar API key"), no una key reutilizada de otra PC.
- [ ] PIN de administrador configurado (no vacío) — de lo contrario nadie puede hacer la salida administrativa.
- [ ] `SERVER_URL` apunta a la IP/dominio correcto de la PC maestra, no a `localhost`.
- [ ] `SERVER_URL` usa `https://` con el `ca.pem` de la CA interna configurado en `[servidor] ca_cert` (ver sección **TLS**) — o, si se decidió operar en `http://` a propósito, `permitir_http_inseguro = true` está fijado y el riesgo fue aceptado conscientemente, no por omisión.
- [ ] Instalado con `sudo ./instalar_linux.sh --usuario-ui <usuario>`, con un usuario de UI sin sudo, y probado con un reinicio real de la PC (servicio activo y UI en la sesión del estudiante).
- [ ] Comprobado que el usuario de la UI no puede leer `/var/lib/biblioteca-kiosko/config.ini` ni modificar `/opt/biblioteca-kiosko/app` (ver **Instalar**).
- [ ] No queda ningún `config.ini`, `db_key.bin` ni `biblioteca_local.db` de pruebas en el checkout del repo.
- [ ] Probado con 2-3 PCs antes de desplegar las 16 (o el total de la sala).
- [ ] Verificado en el panel admin del servidor que la PC aparece y llegan sus sesiones + heartbeat de hardware.
- [ ] Si el compositor es GNOME, aplicado el bloqueo de atajos **a nivel de sistema** (ver sección **Bloqueo de escritorio para producción** arriba) — el `core/bloqueo_escritorio.py` por sí solo es best-effort a nivel de usuario y no sobrevive a una terminal abierta en esa misma sesión.

## Orden de despliegue del sistema completo

Ver la guía paso a paso completa (servidor + todas las PCs + cómo se conectan entre sí) en `biblioteca_servidor/docs/desarrollo/despliegue.md`, sección **Orden de despliegue del sistema completo**, o en `DESPLIEGUE.md` en la raíz del workspace si ambos repos están junto a él. Resumen mínimo:

```
1. Desplegar biblioteca_servidor en la PC maestra (backend + MySQL)
2. Anotar la IP LAN de la PC maestra (ip a) y decidir si se usa TLS (recomendado)
3. En cada PC hija (repetir para las 16, empezando por 2-3 de prueba):
   a. Crear la cuenta del estudiante sin sudo (sudo adduser estudiante)
   b. sudo cliente/autostart/instalar_linux.sh --usuario-ui estudiante
      [--ca-cert ca.pem] → setup.py pide nombre de PC, URL del servidor, CA
   c. En el panel del servidor (pestaña "PCs"), generar la API key para el
      PC_ID que muestra setup.py, y pegarla cuando setup.py la pida
   d. Reiniciar la PC
4. Verificar en el panel admin (pestaña "PCs") que cada PC aparece y llegan
   su heartbeat de estado y sus sesiones
5. (Opcional) túnel Cloudflare para acceso externo al panel — ver biblioteca_servidor
```
