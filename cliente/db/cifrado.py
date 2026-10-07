"""Cifrado en reposo de los campos PII de `estudiantes_cache`.

`biblioteca_local.db` guarda nombre/carrera/facultad/fecha_nacimiento/sexo
de cada estudiante que ha pasado por el kiosko. El `chmod 600` de
`connection.py` protege contra otra cuenta local del mismo equipo, pero no
contra la fuga más común en la práctica: que el archivo `.db` viaje solo
(un backup mal armado, una copia a USB, una carpeta sincronizada a la
nube) y quien lo reciba pueda abrirlo con cualquier lector de SQLite sin
darse cuenta siquiera de que contiene PII.

Este módulo cifra esos campos con Fernet (AES-128-CBC + HMAC-SHA256, del
paquete `cryptography`) usando una clave que se guarda en un archivo
separado (`db_key.bin`, `chmod 600`) junto al `.db`. Esto **no reemplaza**
el cifrado de disco completo recomendado en la auditoría (B6): si alguien
copia el `.db` y `db_key.bin` juntos —o clona el disco completo mientras el
proceso corre— puede seguir descifrando, exactamente igual que sin este
módulo. Lo que sí evita es que el `.db` por sí solo (sin la clave) sea
legible.

`carnet` se deja sin cifrar a propósito: es la clave primaria de la tabla
y se usa en `WHERE carnet = ?`; Fernet es no determinístico (mismo texto
plano produce cifrados distintos cada vez), así que cifrarlo rompería esas
búsquedas.
"""
import hashlib
import hmac
import os

from core.rutas import DATA_DIR
from cryptography.fernet import Fernet, InvalidToken

_KEY_PATH = DATA_DIR / "db_key.bin"

CAMPOS_CIFRADOS = ("nombre", "carrera", "facultad", "fecha_nacimiento", "sexo")


def _cargar_clave() -> bytes:
    """Carga la clave Fernet desde `db_key.bin`, generándola si no existe.
    Reafirma el `chmod 600` en cada carga (mismo criterio que
    `connection.py::_restringir_permisos`), por si los permisos se
    perdieron al copiar/desplegar el archivo."""
    if _KEY_PATH.exists():
        os.chmod(_KEY_PATH, 0o600)
        return _KEY_PATH.read_bytes()
    clave = Fernet.generate_key()
    _KEY_PATH.write_bytes(clave)
    os.chmod(_KEY_PATH, 0o600)
    return clave


_fernet_instancia: Fernet | None = None


def _fernet() -> Fernet:
    """Carga la clave de forma perezosa (no al importar el módulo), para
    que los tests puedan redirigir `_KEY_PATH` a un directorio temporal
    antes de que se toque el archivo real, igual que `db_connection.DB_PATH`
    en `tests/conftest.py`."""
    global _fernet_instancia
    if _fernet_instancia is None:
        _fernet_instancia = Fernet(_cargar_clave())
    return _fernet_instancia


def cifrar_campo(valor: str | None) -> str | None:
    if valor is None:
        return None
    return _fernet().encrypt(valor.encode("utf-8")).decode("ascii")


def descifrar_campo(valor: str | None) -> str | None:
    """Descifra `valor`. Si no es un token Fernet válido —dato legacy
    guardado en texto plano antes de esta corrección, o valor corrupto—
    lo devuelve tal cual en vez de fallar: los registros legacy quedan
    re-cifrados solos la próxima vez que `guardar_estudiante_cache` los
    reciba (login/registro los reescriben con los datos del servidor)."""
    if valor is None:
        return None
    try:
        return _fernet().decrypt(valor.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return valor


def cifrar_estudiante(est: dict) -> dict:
    """Devuelve una copia de `est` con los campos PII cifrados."""
    return {
        **est,
        **{campo: cifrar_campo(est[campo]) for campo in CAMPOS_CIFRADOS if campo in est},
    }


def descifrar_estudiante(est: dict) -> dict:
    """Devuelve una copia de `est` con los campos PII descifrados."""
    return {
        **est,
        **{campo: descifrar_campo(est[campo]) for campo in CAMPOS_CIFRADOS if campo in est},
    }


def seudonimo(carnet: str | None) -> str:
    """Identificador estable de `carnet` para los logs, que no revela el carnet.

    Los logs del servicio rotan y se conservan semanas; con el carnet en claro,
    cualquier copia de esos archivos (soporte, un backup) expone quién usó qué PC
    y cuándo. Un hash simple no sirve: el formato `AA#####` tiene pocos millones
    de valores y se revierte por fuerza bruta en segundos. Con HMAC y la clave
    local de `db_key.bin`, el mismo carnet da siempre el mismo seudónimo en esta
    PC (se pueden seguir sus eventos en el log), pero sin la clave no se puede
    revertir. Para buscar a un estudiante concreto en el log, calcular su
    seudónimo en la misma PC con esta función."""
    if not carnet:
        return "invitado"
    digest = hmac.new(_cargar_clave_cacheada(), carnet.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"est-{digest[:12]}"


_clave_seudonimo: bytes | None = None


def _cargar_clave_cacheada() -> bytes:
    global _clave_seudonimo
    if _clave_seudonimo is None:
        _clave_seudonimo = _cargar_clave()
    return _clave_seudonimo
