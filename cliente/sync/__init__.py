import logging
import os
import threading
from logging.handlers import RotatingFileHandler

import core.estado as estado_mod
from core.config import PC_ID, PC_NOMBRE, SERVER_URL, SYNC_INTERVAL
from core.estudiantes_sync import sincronizar_pendientes
from core.lotes_sync import enviar_por_lotes
from core.rutas import DATA_DIR
from db.cifrado import seudonimo
from db.estudiantes import buscar_estudiante_cache
from db.sesiones import marcar_rechazada, marcar_sincronizado, obtener_pendientes
from network import estudiantes as red_estudiantes
from network.client import hay_conexion
from network.sesiones import enviar_estado, enviar_sesiones

LOG_FILE = DATA_DIR / "sync.log"
log = logging.getLogger("sync")
log.setLevel(logging.INFO)
log.propagate = False
if not log.handlers:
    _formatter = logging.Formatter("%(asctime)s [SYNC] %(message)s")
    # Con rotación, como servicio.log: el servicio corre meses sin reiniciarse y un
    # FileHandler simple crecería sin límite hasta llenar el disco del kiosko.
    _archivo = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    for _handler in (_archivo, logging.StreamHandler()):
        _handler.setFormatter(_formatter)
        log.addHandler(_handler)
    if LOG_FILE.exists():
        os.chmod(LOG_FILE, 0o600)

_wake = threading.Event()


def _sincronizar_estudiantes_pendientes():
    """Reenvía al servidor los registros/actualizaciones de estudiantes que
    se guardaron localmente sin conexión (ver ui/registro.py)."""
    resultado = sincronizar_pendientes(red_estudiantes)
    if not resultado.pendientes:
        return
    for carnet in resultado.reemplazados:
        log.warning(f"El carnet {seudonimo(carnet)} ya estaba registrado en el servidor; se descartan los datos locales")
    log.info(
        f"Estudiantes pendientes: {resultado.sincronizados}/{resultado.pendientes} sincronizados, "
        f"{len(resultado.reemplazados)} reemplazados por la ficha del servidor"
    )


def _ejecutar_ciclo():
    if not hay_conexion():
        log.info("Sin conexión — skip")
        return

    # El estado va antes que los estudiantes pendientes: el servidor solo
    # acepta que un kiosko edite la ficha del estudiante con sesión activa en
    # esa PC, y lo sabe por este heartbeat.
    estado_actual = estado_mod.get_estado()
    enviar_estado({
        "pc_id": PC_ID,
        "pc_nombre": PC_NOMBRE,
        "sesion_activa": estado_actual["activa"],
        "carnet": estado_actual["carnet"],
        "nombre": estado_actual["nombre"],
        "hora_inicio": estado_actual["hora_inicio"],
        "carrera": estado_actual["carrera"],
        "facultad": estado_actual["facultad"],
        "sexo": estado_actual["sexo"],
        "fecha_nacimiento": estado_actual["fecha_nacimiento"],
    })

    _sincronizar_estudiantes_pendientes()

    resultado = enviar_por_lotes(
        obtener_lote=lambda n: [_enriquecer(s) for s in obtener_pendientes(limite=n)],
        enviar=lambda sesiones: enviar_sesiones({
            "pc_id": PC_ID,
            "pc_nombre": PC_NOMBRE,
            "sesiones": sesiones,
        }),
        marcar_enviadas=marcar_sincronizado,
        marcar_rechazadas=_marcar_rechazadas,
    )
    if resultado.enviadas or resultado.rechazadas:
        log.info(f"Sincronizadas {resultado.enviadas} sesiones, rechazadas {resultado.rechazadas}")
    elif resultado.completo:
        log.info("Sin pendientes")
    if not resultado.completo:
        log.warning("Envío de sesiones interrumpido (sin red o límite del servidor) — reintentará")


def _enriquecer(s: dict) -> dict:
    sesion = dict(s)
    if s["carnet"]:
        est = buscar_estudiante_cache(s["carnet"])
        if est:
            sesion["nombre"] = est.get("nombre")
            sesion["carrera"] = est.get("carrera")
            sesion["facultad"] = est.get("facultad")
            sesion["sexo"] = est.get("sexo")
            sesion["fecha_nacimiento"] = est.get("fecha_nacimiento")
    return sesion


def _marcar_rechazadas(ids: list):
    marcar_rechazada(ids)
    log.warning(f"El servidor rechazó por datos inválidos las sesiones {ids}; quedan guardadas localmente")


def _ciclo_sync():
    while True:
        _wake.wait(timeout=SYNC_INTERVAL)
        _wake.clear()
        try:
            _ejecutar_ciclo()
        except Exception as e:
            log.error(f"Error inesperado: {e}")


def forzar_sync():
    """Despierta el daemon para sincronizar ahora sin esperar el intervalo."""
    _wake.set()


def iniciar():
    t = threading.Thread(target=_ciclo_sync, daemon=True, name="sync-worker")
    t.start()
    log.info(f"Sync daemon iniciado — intervalo {SYNC_INTERVAL}s → {SERVER_URL}")
    return t
