"""Script de configuración inicial — ejecutar una vez por PC hija."""
import argparse
import configparser
import getpass
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).parent

# Hace falta antes de poder importar core.* (paquetes del propio proyecto).
sys.path.insert(0, str(BASE_DIR))
from core.pin_hash import LONGITUD_MINIMA_PIN, generar_hash_pin  # noqa: E402
from core.rutas import DATA_DIR  # noqa: E402

# config.ini y .pc_id van al directorio de datos del servicio
# (BIBLIOTECA_DATA_DIR, o junto al código si no está definida), que es el
# único proceso que los lee — ver core/rutas.py.
CONFIG_FILE = DATA_DIR / "config.ini"
PC_ID_FILE = DATA_DIR / ".pc_id"


def preguntar(prompt: str, default: str = "") -> str:
    if default:
        respuesta = input(f"{prompt} [{default}]: ").strip()
        return respuesta or default
    respuesta = input(f"{prompt}: ").strip()
    return respuesta


def preguntar_server_url() -> tuple[str, bool]:
    """Devuelve (url, permitir_http_inseguro). PII de estudiantes y
    KIOSK_API_KEY viajan en cada request a esta URL; sin TLS, cualquiera en
    la misma LAN puede leerlas/alterarlas. Solo se acepta http:// hacia
    localhost sin preguntar — para cualquier otro host hace falta https://,
    o confirmar explícitamente que se asume el riesgo."""
    while True:
        url = preguntar("URL del servidor", "http://localhost:8000")
        host = urlparse(url).hostname
        if urlparse(url).scheme == "http" and host not in ("localhost", "127.0.0.1"):
            print(
                f"  ⚠️  '{url}' usa http:// hacia un host que no es localhost: la PII "
                "de estudiantes y KIOSK_API_KEY viajarían en texto plano por la red."
            )
            resp = preguntar(
                "  ¿Continuar de todas formas con http:// (no recomendado — usa "
                "https:// si el servidor ya tiene TLS configurado, ver "
                "docs/desarrollo/despliegue.md)? (s/N)",
                "n",
            ).strip().lower()
            if resp == "s":
                return url, True
            continue
        return url, False


def preguntar_ca_cert(server_url: str) -> str:
    """Si el servidor usa https:// con un certificado de una CA interna
    propia (no una CA pública reconocida — el caso normal en la LAN del
    laboratorio, ver servidor/scripts/generar_ca.sh), hace falta su ca.pem
    para validar la conexión. Vacío si el certificado ya es de una CA
    pública, o si se está usando http:// (desarrollo/riesgo asumido)."""
    if urlparse(server_url).scheme != "https":
        return ""
    # instalar_linux.sh copia el ca.pem al directorio de datos antes de
    # llamar a este script: el usuario del servicio no puede leer uno que
    # esté en el home de quien instala.
    ca_en_datos = DATA_DIR / "ca.pem"
    return preguntar(
        "Ruta al certificado de la CA interna (ca.pem) para validar el "
        "servidor — dejalo vacío si el servidor usa un certificado de una "
        "CA pública reconocida",
        ca_en_datos.name if ca_en_datos.exists() else "",
    )


def preguntar_admin_pin() -> str:
    """Devuelve el hash PBKDF2 del PIN de administrador (ver core/pin_hash.py),
    o "" si se deja sin configurar (bloquea 'Salir
    (admin)' hasta que se configure). Exige una longitud mínima: un PIN de
    1-3 dígitos es trivial de adivinar por fuerza bruta en la UI misma, sin
    ni siquiera necesitar el hash filtrado."""
    while True:
        admin_pin = getpass.getpass(
            "PIN de administrador para 'Salir (admin)' del kiosko "
            f"(mínimo {LONGITUD_MINIMA_PIN} caracteres, vacío para dejarlo sin configurar; "
            "no se muestra en pantalla): "
        ).strip()
        if not admin_pin:
            return ""
        if len(admin_pin) < LONGITUD_MINIMA_PIN:
            print(f"  ⚠️  El PIN debe tener al menos {LONGITUD_MINIMA_PIN} caracteres.")
            continue
        return generar_hash_pin(admin_pin)


def generar_pc_id() -> str:
    if PC_ID_FILE.exists():
        pc_id = PC_ID_FILE.read_text().strip()
        print(f"  PC_ID existente: {pc_id}")
        return pc_id
    pc_id = str(uuid.uuid4())
    PC_ID_FILE.write_text(pc_id)
    print(f"  PC_ID generado: {pc_id}")
    return pc_id


def instalar_autostart_linux(app_path: str):
    python = sys.executable
    icon = BASE_DIR / "assets" / "logo_icono.png"

    desktop_dir = Path.home() / ".config" / "autostart"
    desktop_dir.mkdir(parents=True, exist_ok=True)
    desktop_file = desktop_dir / "biblioteca-kiosko.desktop"
    desktop_file.write_text(f"""[Desktop Entry]
Type=Application
Name=Biblioteca Kiosko
Exec={python} {app_path}
Icon={icon}
StartupWMClass=biblioteca-kiosko
X-GNOME-Autostart-enabled=true
NoDisplay=false
Hidden=false
""")
    print(f"  Autostart creado: {desktop_file}")

    # Entrada del menú de aplicaciones: es la que el dock/barra de apps del
    # entorno de escritorio (GNOME, etc.) usa para el ícono, no la de
    # autostart de arriba (esa solo controla el arranque de sesión).
    # main.py llama a app.setDesktopFileName("biblioteca-kiosko"), que debe
    # coincidir con el nombre de este archivo (sin ".desktop").
    apps_dir = Path.home() / ".local" / "share" / "applications"
    apps_dir.mkdir(parents=True, exist_ok=True)
    apps_desktop_file = apps_dir / "biblioteca-kiosko.desktop"
    apps_desktop_file.write_text(f"""[Desktop Entry]
Type=Application
Name=Biblioteca Kiosko
Exec={python} {app_path}
Icon={icon}
StartupWMClass=biblioteca-kiosko
Terminal=false
Categories=Utility;
""")
    print(f"  Entrada de aplicación creada: {apps_desktop_file}")
    # Best-effort: solo refresca la caché de íconos/menú. En algunos entornos
    # mínimos el binario ni siquiera existe, así que se verifica antes en vez
    # de dejar que un FileNotFoundError tumbe el resto del setup.
    update_desktop_db_bin = shutil.which("update-desktop-database")
    if update_desktop_db_bin:
        subprocess.run(
            [update_desktop_db_bin, str(apps_dir)],
            capture_output=True, check=False,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sin-autostart", action="store_true",
        help="no ofrecer el autostart de usuario (lo usa instalar_linux.sh, "
             "que instala el servicio y la UI a nivel de sistema)",
    )
    args = parser.parse_args()

    print("=== Configuración de PC Biblioteca ===\n")
    DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)

    nombre = preguntar("Nombre de esta PC (ej: PC-01)", "PC-01")
    server_url, permitir_http_inseguro = preguntar_server_url()
    ca_cert = preguntar_ca_cert(server_url)

    # El PC_ID identifica a esta PC ante el servidor: hace falta generarlo (o
    # leer el existente) antes de pedir la API key, porque el admin la genera
    # desde el panel para este PC_ID puntual (pestaña "PCs" -> "Generar API
    # key" -> PUT /pcs/{pc_id}/api-key), no un valor compartido con las demás.
    pc_id = generar_pc_id()
    print(
        f"\n  Este PC_ID ({pc_id}) es el que hay que darle a quien administra "
        "el panel para que genere la API key de esta PC específica."
    )
    kiosk_key = preguntar(
        "API key de esta PC (generada desde el panel admin para el PC_ID de "
        "arriba; se ve una sola vez ahí, así que copiala ahora)",
        "",
    )
    admin_pin_hash = preguntar_admin_pin()

    config = configparser.ConfigParser()
    config["pc"] = {"nombre": nombre}
    config["servidor"] = {
        "url": server_url,
        "kiosk_key": kiosk_key,
        "ca_cert": ca_cert,
        "permitir_http_inseguro": "true" if permitir_http_inseguro else "false",
    }
    config["sync"] = {"intervalo_segundos": "30"}
    config["admin"] = {"pin_hash": admin_pin_hash}

    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        config.write(f)
    os.chmod(CONFIG_FILE, 0o600)  # KIOSK_API_KEY y pin_hash: solo el dueño del proceso
    print(f"\nConfig guardada: {CONFIG_FILE}")

    # Crear DB local (sys.path ya tiene BASE_DIR, insertado arriba para core.pin_hash)
    from db import init_db
    init_db()
    print("  Base de datos local inicializada")

    # Autostart
    instalar = "n" if args.sin_autostart else preguntar("\n¿Instalar autostart? (s/n)", "s").lower()
    if instalar == "s":
        app_main = str(BASE_DIR / "main.py")
        instalar_autostart_linux(app_main)

    print("\n=== Configuración completa ===")
    print(f"  PC: {nombre}")
    print(f"  ID: {pc_id}")
    print(f"  Servidor: {server_url}")
    if not kiosk_key:
        print("  ⚠️  Sin API key de kiosko: el login/registro de estudiantes fallará (401) hasta que la configures en config.ini")
    if not admin_pin_hash:
        print("  ⚠️  Sin PIN de administrador: 'Salir (admin)' quedará bloqueado hasta que configures [admin] pin_hash en config.ini")
    if args.sin_autostart:
        return
    print("\nEjecutar, en este orden y desde cliente/:")
    print("  python -m servicio   # servicio en segundo plano (config.ini, base local, sync)")
    print("  python main.py       # interfaz del kiosko")


if __name__ == "__main__":
    main()
