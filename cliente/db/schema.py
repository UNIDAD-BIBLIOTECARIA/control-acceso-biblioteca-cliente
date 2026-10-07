from db.connection import conexion


def _migrar_carnet_nullable(conn):
    """sesiones_pendientes.carnet era NOT NULL; el modo Invitado necesita
    guardar sesiones sin carnet. SQLite no soporta ALTER COLUMN, así que
    se reconstruye la tabla solo si todavía tiene la constraint vieja."""
    cols = conn.execute("PRAGMA table_info(sesiones_pendientes)").fetchall()
    if not cols:
        return
    carnet_col = next((c for c in cols if c["name"] == "carnet"), None)
    if carnet_col is None or carnet_col["notnull"] == 0:
        return
    conn.executescript("""
        ALTER TABLE sesiones_pendientes RENAME TO sesiones_pendientes_old;
        CREATE TABLE sesiones_pendientes (
            id TEXT PRIMARY KEY,
            pc_id TEXT NOT NULL,
            carnet TEXT,
            hora_inicio TEXT NOT NULL,
            hora_fin TEXT,
            fecha TEXT NOT NULL,
            sincronizado INTEGER DEFAULT 0,
            timestamp_sync TEXT
        );
        INSERT INTO sesiones_pendientes SELECT * FROM sesiones_pendientes_old;
        DROP TABLE sesiones_pendientes_old;
    """)
    conn.commit()


def _migrar_estudiantes_pendientes(conn):
    """estudiantes_cache no tenía forma de marcar un registro/actualización
    hecho sin conexión como pendiente de reenviar al servidor."""
    cols = conn.execute("PRAGMA table_info(estudiantes_cache)").fetchall()
    if not cols:
        return
    nombres = {c["name"] for c in cols}
    if "sincronizado" not in nombres:
        conn.execute("ALTER TABLE estudiantes_cache ADD COLUMN sincronizado INTEGER NOT NULL DEFAULT 1")
    if "pendiente_modo" not in nombres:
        conn.execute("ALTER TABLE estudiantes_cache ADD COLUMN pendiente_modo TEXT")
    conn.commit()


def init_db():
    with conexion() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sesiones_pendientes (
                id TEXT PRIMARY KEY,
                pc_id TEXT NOT NULL,
                carnet TEXT,
                hora_inicio TEXT NOT NULL,
                hora_fin TEXT,
                fecha TEXT NOT NULL,
                sincronizado INTEGER DEFAULT 0,
                timestamp_sync TEXT
            );

            CREATE TABLE IF NOT EXISTS estudiantes_cache (
                carnet TEXT PRIMARY KEY,
                nombre TEXT NOT NULL,
                carrera TEXT,
                facultad TEXT,
                fecha_nacimiento TEXT,
                sexo TEXT,
                sincronizado INTEGER NOT NULL DEFAULT 1,
                pendiente_modo TEXT
            );

            CREATE TABLE IF NOT EXISTS hardware_local (
                pc_id TEXT PRIMARY KEY,
                horas_acumuladas REAL NOT NULL DEFAULT 0,
                ultimo_heartbeat TEXT,
                ultimo_mantenimiento_conocido TEXT
            );

            CREATE TABLE IF NOT EXISTS pin_admin_lockout (
                pc_id TEXT PRIMARY KEY,
                intentos_fallidos INTEGER NOT NULL DEFAULT 0,
                bloqueado_hasta REAL NOT NULL DEFAULT 0
            );
        """)

        _migrar_carnet_nullable(conn)
        _migrar_estudiantes_pendientes(conn)
