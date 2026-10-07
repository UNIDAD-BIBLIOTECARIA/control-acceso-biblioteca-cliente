import logging
import os
import threading
from logging.handlers import RotatingFileHandler

from core.config import HARDWARE_INTERVAL_SEGUNDOS, PC_ID, now_sv
from core.rutas import DATA_DIR
from network.hardware import enviar_hardware

from hardware import collector, mantenimiento

LOG_FILE = DATA_DIR / "hardware.log"
log = logging.getLogger("hardware-agent")
log.setLevel(logging.INFO)
log.propagate = False
if not log.handlers:
    _formatter = logging.Formatter("%(asctime)s [HARDWARE] %(message)s")
    # Con rotación, como servicio.log: el servicio corre meses sin reiniciarse y un
    # FileHandler simple crecería sin límite hasta llenar el disco del kiosko.
    _archivo = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    for _handler in (_archivo, logging.StreamHandler()):
        _handler.setFormatter(_formatter)
        log.addHandler(_handler)
    if LOG_FILE.exists():
        os.chmod(LOG_FILE, 0o600)

_wake = threading.Event()


def _ejecutar_ciclo():
    ident = collector.identificacion()
    specs = collector.specs()
    salud = collector.salud()
    horas = mantenimiento.registrar_heartbeat(PC_ID, collector.uptime_sistema_segundos())
    estado = mantenimiento.calcular_estado(horas)

    payload = {
        "pc_id": PC_ID,
        "hostname": ident["hostname"],
        "mac": ident["mac"],
        "cpu": specs["cpu"],
        "ram_total_mb": specs["ram_total_mb"],
        "almacenamiento_total_gb": specs["almacenamiento_total_gb"],
        "sistema_operativo": specs["sistema_operativo"],
        "temperatura_cpu_c": salud["temperatura_cpu_c"],
        "disco_smart_ok": salud["disco_smart_ok"],
        "horas_uso_acumuladas": round(horas, 2),
        "ultima_lectura": now_sv().isoformat(),
    }

    log.info(f"Lectura: {payload['horas_uso_acumuladas']}h acumuladas — estado={estado}")

    respuesta = enviar_hardware(PC_ID, payload)
    if respuesta is None:
        log.warning("No se pudo enviar la lectura al servidor — se reintenta en el próximo ciclo")
        return

    reset = mantenimiento.aplicar_reset_si_corresponde(PC_ID, respuesta.get("ultimo_mantenimiento"))
    if reset is not None:
        log.info("Mantenimiento registrado en el servidor — acumulador de horas reiniciado")


def _ciclo_agente():
    while True:
        _wake.wait(timeout=HARDWARE_INTERVAL_SEGUNDOS)
        _wake.clear()
        try:
            _ejecutar_ciclo()
        except Exception as e:
            log.error(f"Error inesperado: {e}")


def forzar_lectura():
    _wake.set()


def iniciar():
    t = threading.Thread(target=_ciclo_agente, daemon=True, name="hardware-agent")
    t.start()
    log.info(f"Agente de hardware iniciado — intervalo {HARDWARE_INTERVAL_SEGUNDOS}s")
    return t
