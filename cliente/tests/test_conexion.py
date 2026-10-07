"""`db.connection.conexion` tiene que cerrar la conexión y deshacer la
transacción aunque la consulta falle, para no dejar descriptores ni locks
abiertos en el servicio, que corre semanas sin reiniciarse."""

import sqlite3

import pytest
from db import connection


class _Espia:
    def __init__(self, conn):
        self._conn = conn
        self.cerrada = False

    def __getattr__(self, nombre):
        return getattr(self._conn, nombre)

    def close(self):
        self.cerrada = True
        self._conn.close()


def _espiar(monkeypatch):
    espias = []
    original = connection.get_connection

    def get_connection():
        espia = _Espia(original())
        espias.append(espia)
        return espia

    monkeypatch.setattr(connection, "get_connection", get_connection)
    return espias


def test_cierra_y_deshace_si_la_consulta_falla(db_temporal, monkeypatch):
    espias = _espiar(monkeypatch)
    with pytest.raises(sqlite3.OperationalError):
        with connection.conexion() as conn:
            conn.execute("INSERT INTO hardware_local (pc_id) VALUES ('PC-1')")
            conn.execute("SELECT * FROM tabla_que_no_existe")
    assert espias[0].cerrada

    with connection.conexion() as conn:
        assert conn.execute("SELECT COUNT(*) FROM hardware_local").fetchone()[0] == 0


def test_confirma_y_cierra_si_todo_sale_bien(db_temporal, monkeypatch):
    espias = _espiar(monkeypatch)
    with connection.conexion() as conn:
        conn.execute("INSERT INTO hardware_local (pc_id) VALUES ('PC-1')")
    assert espias[0].cerrada

    with connection.conexion() as conn:
        assert conn.execute("SELECT COUNT(*) FROM hardware_local").fetchone()[0] == 1
