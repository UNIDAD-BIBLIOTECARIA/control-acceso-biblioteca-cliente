from db.connection import conexion


def obtener_estado_pin(pc_id: str) -> tuple[int, float]:
    """(intentos_fallidos, bloqueado_hasta) persistidos para este kiosko.
    bloqueado_hasta es un timestamp de time.time(); (0, 0.0) si nunca hubo
    intentos fallidos registrados."""
    with conexion() as conn:
        row = conn.execute(
            "SELECT intentos_fallidos, bloqueado_hasta FROM pin_admin_lockout WHERE pc_id = ?",
            (pc_id,)
        ).fetchone()
    if row is None:
        return 0, 0.0
    return row["intentos_fallidos"], row["bloqueado_hasta"]


def guardar_estado_pin(pc_id: str, intentos_fallidos: int, bloqueado_hasta: float):
    with conexion() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO pin_admin_lockout (pc_id, intentos_fallidos, bloqueado_hasta)
            VALUES (?, ?, ?)
        """, (pc_id, intentos_fallidos, bloqueado_hasta))
