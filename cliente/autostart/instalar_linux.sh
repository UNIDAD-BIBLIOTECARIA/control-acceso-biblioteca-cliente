#!/usr/bin/env bash
# Instala el kiosko en esta PC con los privilegios separados:
#
#   - Código en /opt/biblioteca-kiosko, propiedad de root y de solo lectura
#     para los demás: el estudiante no puede modificarlo ni dejar código que
#     se ejecute en la siguiente sesión.
#   - Datos (config.ini con la API key de la PC, db_key.bin, la base local,
#     .pc_id, logs) en /var/lib/biblioteca-kiosko, con permisos 700 y
#     propiedad de un usuario de servicio sin login (kiosko-svc).
#   - El servicio (`python -m servicio`: sync, hardware, acceso a los datos)
#     corre como unidad systemd con ese usuario y atiende a la UI por un
#     socket en /run/biblioteca, accesible solo para el grupo kiosko-ui.
#   - La UI arranca en la sesión gráfica del usuario que usa el estudiante,
#     que es miembro de kiosko-ui y nada más: no puede leer ningún archivo
#     de datos aunque salga del kiosko (navegador con file://, etc.).
#
# Uso (desde un checkout del repo, con sudo):
#   sudo ./instalar_linux.sh --usuario-ui estudiante [--ca-cert /ruta/ca.pem]
#   sudo ./instalar_linux.sh --solo-codigo      # reinstala el código (lo usa actualizar_linux.sh)
#
# Idempotente: se puede volver a correr; no pisa config.ini sin preguntar.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$(dirname "$SCRIPT_DIR")"
PYTHON="${PYTHON:-python3}"

PREFIX="/opt/biblioteca-kiosko"
APP_DIR="$PREFIX/app"
VENV_DIR="$PREFIX/venv"
BIN_DIR="$PREFIX/bin"
LANZADOR_UI="$BIN_DIR/biblioteca-kiosko-ui"
DATA_DIR="/var/lib/biblioteca-kiosko"
SOCKET_DIR="/run/biblioteca"
SOCKET="$SOCKET_DIR/kiosko.sock"
USUARIO_SVC="kiosko-svc"
GRUPO_UI="kiosko-ui"
SERVICE_NAME="biblioteca-kiosko"
SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME.service"
TMPFILES_FILE="/etc/tmpfiles.d/$SERVICE_NAME.conf"
AUTOSTART_FILE="/etc/xdg/autostart/biblioteca-kiosko.desktop"
APPS_FILE="/usr/local/share/applications/biblioteca-kiosko.desktop"

# file:// bloqueado salvo dentro de /home, para que el estudiante pueda abrir
# en el navegador lo que descargó (un PDF, por ejemplo). En Chrome la ruta de
# URLAllowlist es un prefijo; en Firefox, un patrón con comodín.
POLITICA_CHROME='{"URLBlocklist": ["file://*"], "URLAllowlist": ["file:///home/"]}'
POLITICA_FIREFOX='{"policies": {"WebsiteFilter": {"Block": ["file:///*"], "Exceptions": ["file:///home/*"]}}}'
DIRS_POLITICA_CHROME=(/etc/opt/chrome/policies/managed /etc/chromium/policies/managed)
POLITICA_FIREFOX_FILE="/etc/firefox/policies/policies.json"

uso() {
    cat <<EOF
Uso:
  sudo $0 --usuario-ui USUARIO [--ca-cert RUTA]
  sudo $0 --solo-codigo

  --usuario-ui USUARIO  cuenta del sistema con la que el estudiante usa la
                        sesión gráfica. No puede tener sudo.
  --ca-cert RUTA        ca.pem de la CA interna del servidor (si usa https://
                        con certificado propio). Se copia a $DATA_DIR.
  --solo-codigo         reinstala solo el código y las dependencias y
                        reinicia el servicio. Requiere una instalación previa.
EOF
}

ARGS_ORIGINALES="$*"
USUARIO_UI=""
CA_CERT=""
SOLO_CODIGO=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --usuario-ui) USUARIO_UI="${2:?falta el valor de --usuario-ui}"; shift 2 ;;
        --ca-cert) CA_CERT="${2:?falta el valor de --ca-cert}"; shift 2 ;;
        --solo-codigo) SOLO_CODIGO=1; shift ;;
        -h|--help) uso; exit 0 ;;
        *) echo "Opción desconocida: $1" >&2; uso >&2; exit 2 ;;
    esac
done

error() { echo "ERROR: $*" >&2; exit 1; }

# --- Validaciones -------------------------------------------------------

[[ $EUID -eq 0 ]] || error "ejecutalo con sudo: sudo $0 $ARGS_ORIGINALES"
command -v systemctl >/dev/null 2>&1 || error "este instalador necesita systemd."
"$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 10))' \
    || error "hace falta Python 3.10 o superior ($PYTHON es $("$PYTHON" -V 2>&1))."
"$PYTHON" -c 'import venv, ensurepip' 2>/dev/null \
    || error "falta el módulo venv de Python (en Debian/Ubuntu: apt install python3-venv)."

if [[ $SOLO_CODIGO -eq 1 ]]; then
    id "$USUARIO_SVC" >/dev/null 2>&1 && [[ -f "$SERVICE_FILE" ]] \
        || error "no hay una instalación previa; corré primero sudo $0 --usuario-ui USUARIO."
else
    if [[ -z "$USUARIO_UI" ]]; then
        read -rp "Usuario del sistema con el que el estudiante usa la sesión gráfica: " USUARIO_UI || true
    fi
    [[ -n "$USUARIO_UI" ]] || error "hace falta --usuario-ui."
    id "$USUARIO_UI" >/dev/null 2>&1 || error "el usuario '$USUARIO_UI' no existe (crealo antes con adduser)."
    [[ "$(id -u "$USUARIO_UI")" -ne 0 ]] || error "el usuario de la UI no puede ser root."
    [[ "$USUARIO_UI" != "$USUARIO_SVC" ]] || error "el usuario de la UI no puede ser $USUARIO_SVC."
    # Con sudo, el estudiante podría leer los datos del servicio igual que
    # root: la separación de usuarios no serviría de nada.
    for g in sudo wheel admin; do
        if id -nG "$USUARIO_UI" | tr ' ' '\n' | grep -qx "$g"; then
            error "el usuario '$USUARIO_UI' está en el grupo '$g' (tiene sudo). Usá una cuenta sin privilegios para los estudiantes."
        fi
    done
    if [[ -n "$CA_CERT" && ! -f "$CA_CERT" ]]; then
        error "no existe el archivo $CA_CERT."
    fi
fi

# Datos de una ejecución en modo desarrollo dentro del checkout: no se
# copian a /opt, pero quedan legibles para el dueño del checkout.
for f in config.ini .pc_id db_key.bin biblioteca_local.db; do
    if [[ -e "$SRC_DIR/$f" ]]; then
        echo "AVISO: $SRC_DIR/$f es de una ejecución en modo desarrollo y no se usa en esta instalación."
        echo "       Si contiene una API key o datos reales, borralo."
    fi
done

# --- 1. Usuario de servicio y grupo de la UI ---------------------------

if [[ $SOLO_CODIGO -eq 0 ]]; then
    echo "=== 1/7: usuarios y grupos ==="
    if ! getent group "$GRUPO_UI" >/dev/null; then
        groupadd --system "$GRUPO_UI"
        echo "Grupo $GRUPO_UI creado."
    fi
    if ! id "$USUARIO_SVC" >/dev/null 2>&1; then
        NOLOGIN="$(command -v nologin || echo /usr/sbin/nologin)"
        useradd --system --user-group --home-dir "$DATA_DIR" --no-create-home \
            --shell "$NOLOGIN" --comment "Servicio del kiosko de la biblioteca" "$USUARIO_SVC"
        echo "Usuario de servicio $USUARIO_SVC creado."
    fi
    # El servicio necesita el grupo de la UI para asignárselo al socket.
    usermod -aG "$GRUPO_UI" "$USUARIO_SVC"
    usermod -aG "$GRUPO_UI" "$USUARIO_UI"
    echo "$USUARIO_UI y $USUARIO_SVC son miembros de $GRUPO_UI."
fi

# --- 2. Código en /opt, propiedad de root ------------------------------

echo ""
echo "=== 2/7: código en $APP_DIR ==="
systemctl stop "$SERVICE_NAME" 2>/dev/null || true
install -d -o root -g root -m 755 "$PREFIX" "$BIN_DIR"
rm -rf "$APP_DIR.nuevo"
install -d -o root -g root -m 755 "$APP_DIR.nuevo"
tar -C "$SRC_DIR" \
    --exclude='./config.ini' --exclude='./.pc_id' --exclude='./db_key.bin' --exclude='./ca.pem' \
    --exclude='*.db' --exclude='*.db-wal' --exclude='*.db-shm' \
    --exclude='*.log' --exclude='*.log.*' \
    --exclude='__pycache__' --exclude='.pytest_cache' \
    --exclude='./tests' --exclude='./docker' --exclude='./autostart' \
    -cf - . | tar -C "$APP_DIR.nuevo" --no-same-owner -xf -
chown -R root:root "$APP_DIR.nuevo"
find "$APP_DIR.nuevo" -type d -exec chmod 755 {} +
find "$APP_DIR.nuevo" -type f -exec chmod 644 {} +
rm -rf "$APP_DIR.anterior"
[[ -d "$APP_DIR" ]] && mv "$APP_DIR" "$APP_DIR.anterior"
mv "$APP_DIR.nuevo" "$APP_DIR"
rm -rf "$APP_DIR.anterior"
echo "Código copiado desde $SRC_DIR."

# --- 3. Entorno virtual con las dependencias ---------------------------

echo ""
echo "=== 3/7: dependencias en $VENV_DIR ==="
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    "$PYTHON" -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/python" -m pip install -q --disable-pip-version-check -r "$APP_DIR/requirements.txt"
# Los .pyc se generan ahora, como root: en ejecución ni el servicio ni la
# UI pueden escribir en /opt.
"$VENV_DIR/bin/python" -m compileall -q "$APP_DIR" >/dev/null
chown -R root:root "$VENV_DIR"
chmod -R go-w "$PREFIX"
echo "Dependencias instaladas."

cat > "$LANZADOR_UI" <<EOF
#!/bin/sh
# Lanza la UI del kiosko. El autostart de /etc/xdg/autostart aplica a todas
# las cuentas; solo se abre para las del grupo $GRUPO_UI (las del
# estudiante), no para las de administración.
id -Gn | tr ' ' '\n' | grep -qx '$GRUPO_UI' || exit 0
export BIBLIOTECA_SOCKET='$SOCKET'
cd '$APP_DIR' || exit 1
exec '$VENV_DIR/bin/python' main.py "\$@"
EOF
chmod 755 "$LANZADOR_UI"

# --- 4. Directorio de datos y configuración de la PC -------------------

if [[ $SOLO_CODIGO -eq 0 ]]; then
    echo ""
    echo "=== 4/7: datos en $DATA_DIR ==="
    install -d -o "$USUARIO_SVC" -g "$USUARIO_SVC" -m 700 "$DATA_DIR"
    if [[ -n "$CA_CERT" ]]; then
        install -o "$USUARIO_SVC" -g "$USUARIO_SVC" -m 644 "$CA_CERT" "$DATA_DIR/ca.pem"
        echo "ca.pem copiado a $DATA_DIR/ca.pem (en setup.py, respondé 'ca.pem')."
    fi

    configurar="s"
    if [[ -f "$DATA_DIR/config.ini" ]]; then
        read -rp "Ya existe $DATA_DIR/config.ini. ¿Volver a configurar esta PC? [s/N]: " resp || resp=""
        [[ "${resp,,}" == "s" ]] || configurar="n"
    fi
    if [[ "$configurar" == "s" ]]; then
        # setup.py corre como el usuario del servicio para que todo lo que
        # crea (config.ini, .pc_id, la base, db_key.bin) sea suyo.
        (cd "$APP_DIR" && runuser -u "$USUARIO_SVC" -- \
            env BIBLIOTECA_DATA_DIR="$DATA_DIR" "$VENV_DIR/bin/python" setup.py --sin-autostart)
    fi
    chown -R "$USUARIO_SVC:$USUARIO_SVC" "$DATA_DIR"
    chmod -R go-rwx "$DATA_DIR"
fi

# --- 5. Servicio systemd -----------------------------------------------

echo ""
echo "=== 5/7: servicio systemd ($SERVICE_NAME) ==="
# /run/biblioteca lo crea tmpfiles.d en cada arranque, no RuntimeDirectory=:
# systemd le vuelve a poner el grupo del servicio antes de cada Exec*, y el
# directorio tiene que ser del grupo de la UI para que esta llegue al socket.
cat > "$TMPFILES_FILE" <<EOF
# Generado por autostart/instalar_linux.sh.
d $SOCKET_DIR 0750 $USUARIO_SVC $GRUPO_UI -
EOF
systemd-tmpfiles --create "$TMPFILES_FILE"

cat > "$SERVICE_FILE" <<EOF
# Generado por autostart/instalar_linux.sh.
[Unit]
Description=Biblioteca Kiosko (servicio: sync, hardware y datos locales)
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=$USUARIO_SVC
Group=$USUARIO_SVC
SupplementaryGroups=$GRUPO_UI
Environment=BIBLIOTECA_DATA_DIR=$DATA_DIR
Environment=BIBLIOTECA_SOCKET=$SOCKET
Environment=PYTHONDONTWRITEBYTECODE=1
WorkingDirectory=$APP_DIR
ExecStart=$VENV_DIR/bin/python -m servicio
Restart=always
RestartSec=5
# servicio/__main__.py convierte SIGTERM en exit 128+15 tras cerrar la sesión activa.
SuccessExitStatus=143
UMask=0077

StateDirectory=$(basename "$DATA_DIR")
StateDirectoryMode=0700
# Directorio del socket, creado por $TMPFILES_FILE.
ReadWritePaths=$SOCKET_DIR

NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ProtectControlGroups=yes
ProtectKernelModules=yes
ProtectKernelTunables=yes
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK
RestrictSUIDSGID=yes
LockPersonality=yes

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null
systemctl restart "$SERVICE_NAME"
sleep 2
if systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "Servicio activo."
else
    echo "AVISO: el servicio no quedó activo. Revisá: journalctl -u $SERVICE_NAME -n 50"
fi

# --- 6. Autostart de la UI ---------------------------------------------

echo ""
echo "=== 6/7: autostart de la UI ==="
install -d -m 755 "$(dirname "$AUTOSTART_FILE")" "$(dirname "$APPS_FILE")"
cat > "$AUTOSTART_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Biblioteca Kiosko
Exec=$LANZADOR_UI
Icon=$APP_DIR/assets/logo_icono.png
StartupWMClass=biblioteca-kiosko
X-GNOME-Autostart-enabled=true
NoDisplay=false
Hidden=false
Comment=Sistema de control de biblioteca universitaria
EOF
# Entrada del menú de aplicaciones: GNOME la usa para el ícono del dock.
# main.py llama a app.setDesktopFileName("biblioteca-kiosko"), que debe
# coincidir con el nombre de este archivo (sin ".desktop").
cat > "$APPS_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Biblioteca Kiosko
Exec=$LANZADOR_UI
Icon=$APP_DIR/assets/logo_icono.png
StartupWMClass=biblioteca-kiosko
Terminal=false
Categories=Utility;
Comment=Sistema de control de biblioteca universitaria
EOF
chmod 644 "$AUTOSTART_FILE" "$APPS_FILE"
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$(dirname "$APPS_FILE")" 2>/dev/null || true
fi
echo "UI en $AUTOSTART_FILE (solo arranca para miembros de $GRUPO_UI)."

if [[ $SOLO_CODIGO -eq 0 ]]; then
    # Un .desktop con el mismo nombre en el home tiene prioridad sobre el de
    # /etc/xdg/autostart: los de instalaciones anteriores apuntan al código
    # del checkout y no arrancan el servicio.
    HOME_UI="$(getent passwd "$USUARIO_UI" | cut -d: -f6)"
    for f in "$HOME_UI/.config/autostart/biblioteca-kiosko.desktop" \
             "$HOME_UI/.local/share/applications/biblioteca-kiosko.desktop"; do
        if [[ -f "$f" ]]; then
            rm -f "$f"
            echo "Eliminado $f (instalación anterior)."
        fi
    done
fi

# --- 7. Políticas del navegador y bloqueo de escritorio ----------------

if [[ $SOLO_CODIGO -eq 0 ]]; then
    echo ""
    echo "=== 7/7: navegador y escritorio ==="
    read -rp "¿Bloquear file:// fuera de /home en Chrome, Chromium y Firefox (políticas de sistema)? [S/n]: " resp || resp=""
    if [[ "${resp,,}" != "n" ]]; then
        for d in "${DIRS_POLITICA_CHROME[@]}"; do
            install -d -m 755 "$d"
            echo "$POLITICA_CHROME" > "$d/biblioteca-kiosko.json"
            chmod 644 "$d/biblioteca-kiosko.json"
        done
        # Firefox lee un único policies.json: no se pisa uno que no sea nuestro.
        if [[ -f "$POLITICA_FIREFOX_FILE" && "$(cat "$POLITICA_FIREFOX_FILE")" != "$POLITICA_FIREFOX" ]]; then
            echo "AVISO: ya existe $POLITICA_FIREFOX_FILE con otras políticas; no se modificó."
            echo "       Agregá a mano: \"WebsiteFilter\": {\"Block\": [\"file:///*\"], \"Exceptions\": [\"file:///home/*\"]}"
        else
            install -d -m 755 "$(dirname "$POLITICA_FIREFOX_FILE")"
            echo "$POLITICA_FIREFOX" > "$POLITICA_FIREFOX_FILE"
            chmod 644 "$POLITICA_FIREFOX_FILE"
        fi
        echo "file:// bloqueado en los navegadores fuera de /home."
    fi

    # Bloqueo de escritorio a nivel de sistema (dconf con locks + TTY). El
    # script se niega a correr como root y pide sudo por su cuenta.
    read -rp "¿Aplicar también el bloqueo de escritorio a nivel de sistema (dconf con locks + TTY — recomendado para producción)? [s/N]: " resp || resp=""
    if [[ "${resp,,}" == "s" ]]; then
        if [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != "root" ]]; then
            runuser -u "$SUDO_USER" -- "$SCRIPT_DIR/bloquear_sistema_linux.sh"
        else
            echo "Corré sin sudo, desde tu usuario: $SCRIPT_DIR/bloquear_sistema_linux.sh"
        fi
    fi
fi

echo ""
echo "=== Instalación completa ==="
if [[ $SOLO_CODIGO -eq 0 ]]; then
    echo "Reiniciá la PC (o cerrá la sesión de $USUARIO_UI) para que tome el grupo $GRUPO_UI."
    echo "Comprobación: estas dos órdenes deben fallar con 'Permiso denegado':"
    echo "  sudo -u $USUARIO_UI cat $DATA_DIR/config.ini"
    echo "  sudo -u $USUARIO_UI touch $APP_DIR/main.py"
else
    echo "La UI usará el código nuevo la próxima vez que se inicie la sesión gráfica."
fi
echo "Logs del servicio: journalctl -u $SERVICE_NAME  y  $DATA_DIR/*.log"
