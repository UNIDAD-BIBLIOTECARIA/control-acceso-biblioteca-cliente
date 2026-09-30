#!/usr/bin/env bash
# Deshace lo que instala instalar_linux.sh. Pregunta antes de borrar la
# configuración de la PC, la base local y el usuario de servicio.
# Uso: sudo ./desinstalar_linux.sh
set -euo pipefail

PREFIX="/opt/biblioteca-kiosko"
DATA_DIR="/var/lib/biblioteca-kiosko"
USUARIO_SVC="kiosko-svc"
GRUPO_UI="kiosko-ui"
SERVICE_NAME="biblioteca-kiosko"
SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME.service"
TMPFILES_FILE="/etc/tmpfiles.d/$SERVICE_NAME.conf"
SOCKET_DIR="/run/biblioteca"
AUTOSTART_FILE="/etc/xdg/autostart/biblioteca-kiosko.desktop"
APPS_FILE="/usr/local/share/applications/biblioteca-kiosko.desktop"
POLITICA_FIREFOX='{"policies": {"WebsiteFilter": {"Block": ["file:///*"], "Exceptions": ["file:///home/*"]}}}'
POLITICA_FIREFOX_FILE="/etc/firefox/policies/policies.json"
POLITICAS_CHROME=(
    /etc/opt/chrome/policies/managed/biblioteca-kiosko.json
    /etc/chromium/policies/managed/biblioteca-kiosko.json
)

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: ejecutalo con sudo: sudo $0" >&2
    exit 1
fi

echo "=== Desinstalando Biblioteca Kiosko (Linux) ==="

# Servicio: al detenerse cierra la sesión activa con su hora real de fin.
if [[ -f "$SERVICE_FILE" ]]; then
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    systemctl disable "$SERVICE_NAME" 2>/dev/null || true
    rm -f "$SERVICE_FILE"
    systemctl daemon-reload
    echo "Servicio systemd eliminado."
else
    echo "No había servicio systemd instalado."
fi
rm -f "$TMPFILES_FILE"
rm -rf "$SOCKET_DIR"

# La UI que siga abierta en una sesión gráfica.
pkill -f "$PREFIX/venv/bin/python main.py" 2>/dev/null || true

rm -f "$AUTOSTART_FILE" "$APPS_FILE"
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$(dirname "$APPS_FILE")" 2>/dev/null || true
fi
echo "Autostart de la UI eliminado."

rm -rf "$PREFIX"
echo "Código eliminado ($PREFIX)."

rm -f "${POLITICAS_CHROME[@]}"
# Solo se borra el policies.json de Firefox si es el que escribió el instalador.
if [[ -f "$POLITICA_FIREFOX_FILE" && "$(cat "$POLITICA_FIREFOX_FILE")" == "$POLITICA_FIREFOX" ]]; then
    rm -f "$POLITICA_FIREFOX_FILE"
fi
echo "Políticas del navegador eliminadas."

if [[ -d "$DATA_DIR" ]]; then
    rm -f "$DATA_DIR"/*.log "$DATA_DIR"/*.log.[0-9]*

    echo ""
    read -rp "¿Borrar la configuración de esta PC (config.ini, .pc_id, ca.pem) para poder reconfigurarla desde cero? [s/N]: " resp || resp=""
    if [[ "${resp,,}" == "s" ]]; then
        rm -f "$DATA_DIR/config.ini" "$DATA_DIR/.pc_id" "$DATA_DIR/ca.pem"
        echo "Configuración eliminada."
    else
        echo "Configuración conservada."
    fi

    DB_FILE="$DATA_DIR/biblioteca_local.db"
    if [[ -f "$DB_FILE" ]]; then
        echo ""
        echo "AVISO: la base de datos local puede tener sesiones aún no sincronizadas con el servidor."
        read -rp "¿Borrar la base de datos local y su clave de cifrado (biblioteca_local.db, db_key.bin)? [s/N]: " resp_db || resp_db=""
        if [[ "${resp_db,,}" == "s" ]]; then
            rm -f "$DB_FILE" "$DB_FILE-wal" "$DB_FILE-shm" "$DATA_DIR/db_key.bin"
            echo "Base de datos local eliminada."
        else
            echo "Base de datos local conservada."
        fi
    fi

    rmdir "$DATA_DIR" 2>/dev/null && echo "Directorio de datos eliminado ($DATA_DIR)." \
        || echo "Datos conservados en $DATA_DIR."
fi

if [[ ! -d "$DATA_DIR" ]] && id "$USUARIO_SVC" >/dev/null 2>&1; then
    echo ""
    read -rp "¿Borrar también el usuario $USUARIO_SVC y el grupo $GRUPO_UI? [s/N]: " resp || resp=""
    if [[ "${resp,,}" == "s" ]]; then
        userdel "$USUARIO_SVC" || true
        groupdel "$GRUPO_UI" 2>/dev/null || true
        echo "Usuario y grupo eliminados."
    fi
fi

echo ""
echo "El bloqueo de escritorio (dconf/TTY) no se toca: para revertirlo, ./desbloquear_sistema_linux.sh"
echo "Para volver a instalar: sudo ./instalar_linux.sh --usuario-ui USUARIO"
echo "=== Listo ==="
