from db.connection import conexion


def obtener_estado_local(pc_id: str) -> dict | None:
    with conexion() as conn:
        row = conn.execute(
            "SELECT * FROM hardware_local WHERE pc_id = ?", (pc_id,)
        ).fetchone()
    return dict(row) if row else None


def guardar_estado_local(pc_id: str, horas_acumuladas: float, ultimo_heartbeat: str,
                          ultimo_mantenimiento_conocido: str | None):
    with conexion() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO hardware_local
                (pc_id, horas_acumuladas, ultimo_heartbeat, ultimo_mantenimiento_conocido)
            VALUES (?, ?, ?, ?)
        """, (pc_id, horas_acumuladas, ultimo_heartbeat, ultimo_mantenimiento_conocido))
