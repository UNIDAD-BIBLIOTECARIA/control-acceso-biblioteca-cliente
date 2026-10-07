"""`seudonimo` reemplaza al carnet en los logs del servicio: tiene que ser
estable (para seguir a un estudiante dentro del log) y no dejar ver el carnet."""

from db import cifrado


def test_seudonimo_es_estable_y_no_contiene_el_carnet(db_temporal):
    a = cifrado.seudonimo("AB12345")
    assert a == cifrado.seudonimo("AB12345")
    assert "AB12345" not in a
    assert a != cifrado.seudonimo("AB12346")


def test_seudonimo_depende_de_la_clave_local(db_temporal, monkeypatch):
    # Sin la clave de esta PC no se puede recalcular (y con fuerza bruta sobre
    # el formato AA##### tampoco, a diferencia de un hash simple).
    antes = cifrado.seudonimo("AB12345")
    monkeypatch.setattr(cifrado, "_clave_seudonimo", b"otra-clave")
    assert cifrado.seudonimo("AB12345") != antes


def test_seudonimo_de_sesion_sin_carnet():
    assert cifrado.seudonimo(None) == "invitado"
