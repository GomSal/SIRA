"""Modelo B: contrato de datos + no regresión frente a reports/dron/corrector_dron_v2.csv. Corre en segundos."""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

RAIZ = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("job_modelo_b", RAIZ / "cloud" / "modelo_b" / "job_modelo_b.py")
J = importlib.util.module_from_spec(spec)
sys.modules["job_modelo_b"] = J
spec.loader.exec_module(J)


@pytest.fixture(scope="module")
def tablas():
    t, cal, d0 = J.construir_tablas(None)
    return t, cal, d0


def test_contrato_de_datos(tablas):
    t, _, _ = tablas
    C = J.contrato(t)  # lanza ErrorEstado si algo no cuadra
    assert C.set_index("indice")[["fechas", "pares"]].to_dict("index") == {
        "NDVI": {"fechas": 23, "pares": 141}, "NDRE": {"fechas": 14, "pares": 42}, "OSAVI": {"fechas": 23, "pares": 141}}
    for ind, T in t.items():
        cand = J.candidatas(ind, T)
        assert not set(cand) & J.PROHIBIDAS
        assert not any(c.startswith("vwc") for c in cand)
        assert np.isfinite(T.y).all()
    assert "ndre" not in J.candidatas("NDVI", t["NDVI"])  # Landsat no tiene borde rojo


def test_calidad_descarta_las_fechas_conocidas(tablas):
    _, cal, _ = tablas
    malas = {(str(f.date()), s) for f, s in zip(cal[cal.motivo != "ok"].fecha, cal[cal.motivo != "ok"].sensor)}
    assert malas == {("2021-07-07", "S2"), ("2021-08-11", "S2"), ("2021-09-15", "S2"), ("2022-01-03", "S2"),
                     ("2022-09-20", "L")}


def test_no_regresion(tablas):
    t, _, _ = tablas
    R = J.no_regresion(t)
    assert R.pasa.all() and (R.dif <= 0.01).all()


def test_osavi_y_rojo_despejado(tablas):
    t, _, _ = tablas
    T = t["NDVI"]
    assert T.osavi.between(0, 1).all()
    assert (T.osavi < T.ndvi).all()  # el término 0,16 siempre baja el índice en vegetación


def test_metricas_y_pca():
    fid = np.array(["a", "a", "a", "b"])
    y, p = np.array([0., 0, 0, 1]), np.array([0., 0, 0, 0])
    assert J.rmse_fecha(y, p, fid) == pytest.approx(np.sqrt(0.5))  # peso igual por fecha, no por fila
    X = np.repeat(np.random.default_rng(0).normal(size=(3, J.NPRI)), 4, axis=0)
    pca = J.PCAPrithvi(k=20).fit(X)
    assert pca.k_ == 2  # 3 fechas distintas → como máximo 2 componentes
