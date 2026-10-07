"""Operaciones que el servicio ofrece a la UI.

Todo lo que llega acá viene de un proceso que corre con el usuario del
estudiante, así que se valida como entrada no confiable: tipos, longitudes y
formato del carnet. Además, el servicio decide por su cuenta lo que antes
decidía la UI: genera el id y la hora de cada sesión, toma los datos del
estudiante de su propia caché y solo permite editar la ficha del estudiante
que tiene la sesión abierta en esta PC.
"""
import logging
import threading
import time
import uuid
from collections.abc import Callable
from datetime import date

import core.estado as estado_mod
from core.estudiantes_sync import reemplazar_con_ficha_del_servidor
from core.pin_hash import es_hash_legacy, verificar_pin
from core.tiempo import now_sv
from core.validacion import carnet_valido, normalizar_carnet
from db.cifrado import seudonimo
from db.estudiantes import buscar_estudiante_cache, guardar_estudiante_cache, guardar_estudiante_del_servidor
from db.pin_admin import guardar_estado_pin, obtener_estado_pin
from db.sesiones import actualizar_hora_fin, guardar_sesion
from network.errores import CarnetYaRegistrado, ServidorNoDisponible

log = logging.getLogger("servicio")

# Rate limiting del PIN de administrador local: sin esto, alguien con
# acceso físico prolongado a un kiosko puede probar PINs sin límite ni
# demora. Se persiste en la base local (tabla pin_admin_lockout, ver
# db/pin_admin.py) para que reiniciar el kiosko no resetee el contador, y se
# aplica acá y no en la UI para que tampoco se pueda esquivar hablándole
# directamente al socket.
PIN_ADMIN_MAX_INTENTOS = 5
PIN_ADMIN_BLOQUEO_SEGUNDOS = 5 * 60

CAMPOS_PUBLICOS = ("carnet", "nombre", "carrera", "facultad", "fecha_nacimiento", "sexo")
MAX_LONGITUD_CAMPO = 255
MAX_LONGITUD_PIN = 256


class ErrorOperacion(Exception):
    """Error esperado de una operación; `codigo` es estable para que la UI
    elija el mensaje a mostrar."""

    def __init__(self, codigo: str, mensaje: str = ""):
        super().__init__(mensaje or codigo)
        self.codigo = codigo
        self.mensaje = mensaje


def _texto(valor, campo: str, *, obligatorio: bool = False, max_len: int = MAX_LONGITUD_CAMPO) -> str:
    if valor is None:
        valor = ""
    if not isinstance(valor, str):
        raise ErrorOperacion("datos_invalidos", f"{campo} debe ser texto")
    valor = valor.strip()
    if obligatorio and not valor:
        raise ErrorOperacion("datos_invalidos", f"{campo} es obligatorio")
    if len(valor) > max_len:
        raise ErrorOperacion("datos_invalidos", f"{campo} supera {max_len} caracteres")
    return valor


def _carnet(valor) -> str:
    if not isinstance(valor, str):
        raise ErrorOperacion("carnet_invalido", "carnet debe ser texto")
    carnet = normalizar_carnet(valor)
    if not carnet_valido(carnet):
        raise ErrorOperacion("carnet_invalido", "formato de carnet inválido")
    return carnet


def _publico(est: dict) -> dict:
    """Solo los datos que la UI necesita mostrar, sin las columnas internas
    de la caché (sincronizado, pendiente_modo) ni lo que agregue el servidor."""
    return {campo: est.get(campo) or "" for campo in CAMPOS_PUBLICOS}


class ServicioKiosko:
    def __init__(
        self,
        *,
        pc_id: str,
        duracion_sesion_ms: int,
        admin_pin_hash: str,
        bloquear_atajos: bool,
        red,
        forzar_sync: Callable[[], None],
    ):
        """`red` expone hay_conexion(), obtener_estudiante(carnet),
        registrar_estudiante(datos) y actualizar_estudiante(carnet, datos),
        con el mismo contrato que network/ (ver servicio/__main__.py)."""
        self._pc_id = pc_id
        self._duracion_sesion_ms = duracion_sesion_ms
        self._admin_pin_hash = admin_pin_hash
        self._bloquear_atajos = bloquear_atajos
        self._red = red
        self._forzar_sync = forzar_sync
        # El socket atiende cada conexión en su propio hilo: el lock serializa
        # los cambios de sesión y los intentos de PIN.
        self._lock = threading.Lock()
        self._sesion: dict | None = None

    # ── Consulta ────────────────────────────────────────────────────────

    def info(self) -> dict:
        return {
            "bloquear_atajos": self._bloquear_atajos,
            "duracion_sesion_ms": self._duracion_sesion_ms,
        }

    def _buscar(self, carnet: str) -> dict | None:
        """Caché local primero; si no está y hay conexión, el servidor (y se
        cachea la respuesta para la próxima vez o para cuando no haya red).

        Si hay conexión pero el servidor no contesta (timeout, 429, 5xx) se
        lanza "servidor_no_disponible" en vez de devolver None: un «no
        existe» falso llevaba al estudiante a registrar de nuevo un carnet
        que ya estaba en el servidor."""
        est = buscar_estudiante_cache(carnet)
        if est:
            return est
        if not self._red.hay_conexion():
            return None
        try:
            datos = self._red.obtener_estudiante(carnet)
        except ServidorNoDisponible as exc:
            log.warning("No se pudo consultar el carnet %s en el servidor: %s", seudonimo(carnet), exc)
            raise ErrorOperacion("servidor_no_disponible", "no se pudo consultar el servidor") from exc
        if not datos:
            return None
        return guardar_estudiante_del_servidor(carnet, datos)

    def buscar_estudiante(self, carnet) -> dict | None:
        carnet = _carnet(carnet)
        est = self._buscar(carnet)
        if est:
            log.info("Carnet %s encontrado", seudonimo(carnet))
            return _publico(est)
        log.info("Carnet %s no encontrado", seudonimo(carnet))
        return None

    # ── Registro / actualización de datos ───────────────────────────────

    def guardar_estudiante(self, modo, datos) -> dict:
        """Registra (modo "crear") o actualiza (modo "actualizar") un
        estudiante en el servidor, o lo deja pendiente en la caché local si
        no se pudo enviar. Devuelve {"estudiante", "estado"}, con estado
        "sincronizado", "pendiente_error" o "pendiente_sin_conexion"."""
        if modo not in ("crear", "actualizar"):
            raise ErrorOperacion("datos_invalidos", "modo debe ser 'crear' o 'actualizar'")
        if not isinstance(datos, dict):
            raise ErrorOperacion("datos_invalidos", "datos debe ser un objeto")

        carnet = _carnet(datos.get("carnet"))
        est = {
            "carnet": carnet,
            "nombre": _texto(datos.get("nombre"), "nombre", obligatorio=True),
            "carrera": _texto(datos.get("carrera"), "carrera"),
            "facultad": _texto(datos.get("facultad"), "facultad"),
            "sexo": _texto(datos.get("sexo"), "sexo"),
            "fecha_nacimiento": _texto(datos.get("fecha_nacimiento"), "fecha_nacimiento"),
        }
        anio = est["fecha_nacimiento"]
        if anio and not (len(anio) == 4 and anio.isdigit()):
            raise ErrorOperacion("datos_invalidos", "fecha_nacimiento debe ser un año (AAAA)")

        if modo == "actualizar":
            # Solo la ficha de quien tiene la sesión abierta en esta PC: la UI
            # nunca ofrece editar otra, así que cualquier otro carnet es un
            # intento de modificar datos ajenos.
            with self._lock:
                carnet_activo = self._sesion["carnet"] if self._sesion else None
            if carnet != carnet_activo:
                raise ErrorOperacion(
                    "sin_permiso", "solo se pueden actualizar los datos del estudiante con la sesión activa"
                )
        elif self._buscar(carnet):
            raise ErrorOperacion("carnet_duplicado", "este carnet ya está registrado")

        envio = {**est, "id": str(uuid.uuid4()), "fecha_registro": date.today().isoformat()}
        if self._red.hay_conexion():
            if modo == "actualizar":
                ok = self._red.actualizar_estudiante(carnet, envio)
            else:
                try:
                    ok = self._red.registrar_estudiante(envio)
                except CarnetYaRegistrado:
                    # Se registró desde otra PC entre la consulta y el alta:
                    # se cachea la ficha del servidor, no la que se tecleó.
                    reemplazar_con_ficha_del_servidor(self._red, carnet)
                    raise ErrorOperacion("carnet_duplicado", "este carnet ya está registrado") from None
            estado = "sincronizado" if ok else "pendiente_error"
        else:
            estado = "pendiente_sin_conexion"

        if estado == "sincronizado":
            guardar_estudiante_cache(est)
        else:
            guardar_estudiante_cache(est, sincronizado=0, pendiente_modo=modo)
        log.info("Estudiante %s (%s): %s", seudonimo(carnet), modo, estado)

        if modo == "actualizar":
            with self._lock:
                if self._sesion and self._sesion["carnet"] == carnet:
                    self._publicar_estado_sesion(est)
            self._forzar_sync()

        return {"estudiante": _publico(est), "estado": estado}

    # ── Sesión ──────────────────────────────────────────────────────────

    def _publicar_estado_sesion(self, est: dict | None, sector: str = ""):
        """Datos de la sesión activa que el hilo de sync reporta al servidor
        en cada heartbeat (POST /estado). Llamar con self._lock tomado."""
        estado_mod.set_sesion_activa(
            carnet=est["carnet"] if est else None,
            nombre=est.get("nombre", "") if est else sector,
            hora_inicio=self._sesion["hora_inicio"],
            carrera=est.get("carrera") if est else None,
            facultad=est.get("facultad") if est else None,
            sexo=est.get("sexo") if est else None,
            fecha_nacimiento=est.get("fecha_nacimiento") if est else None,
        )

    def _cerrar_sesion_activa(self) -> bool:
        """Llamar con self._lock tomado."""
        if not self._sesion:
            return False
        actualizar_hora_fin(self._sesion["id"], now_sv().isoformat())
        log.info("Sesión cerrada — %s", seudonimo(self._sesion["carnet"]))
        self._sesion = None
        estado_mod.set_sesion_inactiva()
        return True

    def abrir_sesion(self, carnet=None, sector=None) -> dict:
        """Abre una sesión para `carnet` (estudiante ya registrado, que tiene
        que estar en la caché local: login y registro lo dejan ahí) o, sin
        carnet, un acceso sin registro etiquetado con `sector`. Si había una
        sesión abierta en esta PC, la cierra antes."""
        if (carnet is None) == (sector is None):
            raise ErrorOperacion("datos_invalidos", "indicar carnet o sector, no ambos")
        est = None
        if carnet is not None:
            carnet = _carnet(carnet)
            est = buscar_estudiante_cache(carnet)
            if not est:
                raise ErrorOperacion("carnet_no_encontrado", "el carnet no está registrado")
        else:
            sector = _texto(sector, "sector", obligatorio=True, max_len=100)

        with self._lock:
            self._cerrar_sesion_activa()
            ahora = now_sv()
            self._sesion = {
                "id": str(uuid.uuid4()),
                "carnet": carnet,
                "hora_inicio": ahora.isoformat(),
            }
            guardar_sesion({
                "id": self._sesion["id"],
                "pc_id": self._pc_id,
                "carnet": carnet,
                "hora_inicio": self._sesion["hora_inicio"],
                "hora_fin": None,
                "fecha": date.today().isoformat(),
            })
            self._publicar_estado_sesion(est, sector or "")
            # Heartbeat inmediato: el servidor rechaza editar los datos del
            # estudiante hasta que sabe que tiene la sesión abierta en esta PC.
            self._forzar_sync()
            if carnet:
                log.info("Sesión iniciada — carnet %s", seudonimo(carnet))
            else:
                log.info("Sesión iniciada — invitado (%s)", sector)
            return {
                "sesion_id": self._sesion["id"],
                "hora_inicio": self._sesion["hora_inicio"],
                "duracion_sesion_ms": self._duracion_sesion_ms,
                "estudiante": _publico(est) if est else None,
            }

    def cerrar_sesion(self) -> dict:
        with self._lock:
            cerrada = self._cerrar_sesion_activa()
        if cerrada:
            self._forzar_sync()
        return {"cerrada": cerrada}

    # ── PIN de administrador ────────────────────────────────────────────

    def _estado_pin(self) -> dict:
        """Llamar con self._lock tomado."""
        fallos, bloqueado_hasta = obtener_estado_pin(self._pc_id)
        restante = bloqueado_hasta - time.time()
        if restante > 0:
            return {"estado": "bloqueado", "minutos": int(restante // 60) + 1, "intentos": fallos}
        if not self._admin_pin_hash:
            return {"estado": "sin_pin"}
        if es_hash_legacy(self._admin_pin_hash):
            # Hash del formato viejo (SHA-256 plano sin sal): no se puede
            # migrar sin conocer el PIN en texto plano, así que se bloquea y
            # se pide reconfigurar.
            return {"estado": "legacy"}
        return {"estado": "disponible"}

    def estado_pin_admin(self) -> dict:
        """Permite a la UI avisar de un bloqueo o de un PIN sin configurar
        antes de pedirlo."""
        with self._lock:
            return self._estado_pin()

    def verificar_pin_admin(self, pin) -> dict:
        # Sin _texto(): el PIN se compara tal cual, sin recortar espacios.
        if not isinstance(pin, str) or len(pin) > MAX_LONGITUD_PIN:
            raise ErrorOperacion("datos_invalidos", "pin inválido")
        with self._lock:
            estado = self._estado_pin()
            if estado["estado"] != "disponible":
                log.warning("PIN de administrador no verificado: %s", estado["estado"])
                return estado
            if verificar_pin(pin, self._admin_pin_hash):
                log.info("PIN de administrador correcto")
                guardar_estado_pin(self._pc_id, 0, 0.0)
                return {"estado": "ok"}
            fallos, _ = obtener_estado_pin(self._pc_id)
            fallos += 1
            log.warning("PIN de administrador incorrecto (intento %d/%d)", fallos, PIN_ADMIN_MAX_INTENTOS)
            if fallos >= PIN_ADMIN_MAX_INTENTOS:
                guardar_estado_pin(self._pc_id, fallos, time.time() + PIN_ADMIN_BLOQUEO_SEGUNDOS)
                log.warning("PIN de administrador bloqueado por %d minutos", PIN_ADMIN_BLOQUEO_SEGUNDOS // 60)
                return {"estado": "bloqueado", "minutos": PIN_ADMIN_BLOQUEO_SEGUNDOS // 60, "intentos": fallos}
            guardar_estado_pin(self._pc_id, fallos, 0.0)
            return {"estado": "incorrecto", "intentos": fallos, "max_intentos": PIN_ADMIN_MAX_INTENTOS}
