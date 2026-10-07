from db.cifrado import cifrar_estudiante, descifrar_estudiante
from db.connection import conexion


def guardar_estudiante_cache(est: dict, sincronizado: int = 1, pendiente_modo: str | None = None):
    with conexion() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO estudiantes_cache
                (carnet, nombre, carrera, facultad, fecha_nacimiento, sexo, sincronizado, pendiente_modo)
            VALUES (:carnet, :nombre, :carrera, :facultad, :fecha_nacimiento, :sexo, :sincronizado, :pendiente_modo)
        """, {**cifrar_estudiante(est), "sincronizado": sincronizado, "pendiente_modo": pendiente_modo})


def buscar_estudiante_cache(carnet: str) -> dict | None:
    with conexion() as conn:
        row = conn.execute(
            "SELECT * FROM estudiantes_cache WHERE carnet = ?", (carnet,)
        ).fetchone()
    return descifrar_estudiante(dict(row)) if row else None


def obtener_estudiantes_pendientes() -> list:
    with conexion() as conn:
        rows = conn.execute(
            "SELECT * FROM estudiantes_cache WHERE sincronizado = 0"
        ).fetchall()
    return [descifrar_estudiante(dict(r)) for r in rows]


def marcar_estudiante_sincronizado(carnet: str):
    with conexion() as conn:
        conn.execute(
            "UPDATE estudiantes_cache SET sincronizado = 1, pendiente_modo = NULL WHERE carnet = ?",
            (carnet,)
        )


CAMPOS_ESTUDIANTE = ("carnet", "nombre", "carrera", "facultad", "fecha_nacimiento", "sexo")


def guardar_estudiante_del_servidor(carnet: str, datos: dict) -> dict:
    """Cachea la ficha tal como la tiene el servidor, marcada como
    sincronizada, y descarta lo que hubiera en local para ese carnet."""
    est = {campo: datos.get(campo) or "" for campo in CAMPOS_ESTUDIANTE}
    est["carnet"] = datos.get("carnet") or carnet
    guardar_estudiante_cache(est)
    return est
