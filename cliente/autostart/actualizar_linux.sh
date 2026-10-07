#!/usr/bin/env bash
# Descarga la última versión del repo y la instala en /opt con
# instalar_linux.sh --solo-codigo. Se corre desde tu usuario (no con sudo):
# el git se hace con el dueño del checkout y solo la instalación pide sudo.
#
# Verificación de firmas (opcional): con
#   git config biblioteca.verificarFirmas true
# en el checkout de la PC, solo se aplica la actualización si el commit nuevo
# lleva una firma GPG/SSH válida de una clave en la que confía el usuario que
# corre el script (`git verify-commit`). Así, quien tome control del remoto o
# de la red no puede colar código en los kioscos solo con un push. Requiere
# que el equipo firme sus commits e importar sus claves públicas en cada PC
# (o configurar gpg.ssh.allowedSignersFile); por eso no viene activada.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"
REPO_DIR="$(cd "$APP_DIR/.." && pwd)"
SERVICE_NAME="biblioteca-kiosko"

if [[ $EUID -eq 0 ]]; then
    echo "No corras este script con sudo: el git se hace con tu usuario y la instalación pide sudo cuando hace falta."
    exit 1
fi

echo "=== Actualizando Biblioteca Kiosko ==="

if [[ ! -d "$REPO_DIR/.git" ]]; then
    echo "ERROR: $REPO_DIR no es un repositorio git. No se puede actualizar."
    exit 1
fi

cd "$REPO_DIR"

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
echo "Rama actual: $BRANCH"

if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    echo "ERROR: hay cambios locales sin confirmar en el repositorio."
    echo "Resuélvelos antes de actualizar (git stash / git checkout -- .)."
    exit 1
fi

echo "Descargando cambios..."
git fetch origin "$BRANCH"

LOCAL_REV="$(git rev-parse HEAD)"
REMOTE_REV="$(git rev-parse "origin/$BRANCH")"

if [[ "$LOCAL_REV" == "$REMOTE_REV" ]]; then
    echo "Ya está en la última versión ($LOCAL_REV)."
else
    if [[ "$(git config --bool --get biblioteca.verificarFirmas || echo false)" == "true" ]]; then
        echo "Verificando la firma de $REMOTE_REV..."
        if ! git verify-commit "$REMOTE_REV"; then
            echo "ERROR: el commit $REMOTE_REV no tiene una firma válida de una clave de confianza."
            echo "No se aplica la actualización."
            exit 1
        fi
    fi
    echo "Aplicando actualización ($LOCAL_REV -> $REMOTE_REV)..."
    git merge --ff-only "origin/$BRANCH"
fi

echo ""
if ! systemctl list-unit-files 2>/dev/null | grep -q "^${SERVICE_NAME}.service"; then
    echo "El kiosko no está instalado en esta PC. Para instalarlo:"
    echo "  sudo $SCRIPT_DIR/instalar_linux.sh --usuario-ui USUARIO"
    exit 0
fi

# Instalar reinicia el servicio, que cierra la sesión activa del estudiante.
if [[ -t 0 ]]; then
    read -r -p "¿Instalar ahora la nueva versión? Reinicia el servicio y cierra la sesión activa. (s/n) " RESPUESTA
else
    RESPUESTA="n"
    echo "Ejecución sin terminal (p. ej. cron) — no se instala automáticamente."
fi
if [[ "$RESPUESTA" == "s" || "$RESPUESTA" == "S" ]]; then
    sudo "$SCRIPT_DIR/instalar_linux.sh" --solo-codigo
else
    echo "Instalación pendiente. Ejecuta 'sudo $SCRIPT_DIR/instalar_linux.sh --solo-codigo' cuando quieras aplicarla."
fi

echo ""
echo "=== Actualización completada ==="
