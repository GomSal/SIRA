"""Pruebas de la digitalización.

- Unitarias: piezas puras (ejes, tramos, lámina diaria, deduplicación).
- De validación: el dataset final contra los números publicados en el artículo. Corren sobre
  data/processed y data/interim, así que primero hay que ejecutar `python -m sira.digitalizar.ensamblar`.
"""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from sira.digitalizar.comun import EjeLineal, corridas
from sira.digitalizar.ensamblar import (ETAPAS_FIG4, EVENTOS_TABLA2, LLUVIA_TABLA2, RIEGO_TABLA2,
                                        TRATAMIENTOS, VWC_TEXTO, cortes_etapa)
from sira.digitalizar.fig3 import deduplicar
from sira.digitalizar.fig4 import riego_diario

RAIZ = Path(__file__).resolve().parents[1]


# ---------- unitarias ----------

def test_eje_lineal_ida_y_vuelta() -> None:
    eje = EjeLineal.por_dos_puntos(651.0, 0.0, 143.0, 100.0)
    assert eje.valor(651.0) == pytest.approx(0.0)
    assert eje.valor(143.0) == pytest.approx(100.0)
    assert eje.pixel(eje.valor(400.0)) == pytest.approx(400.0)


def test_corridas() -> None:
    v = np.array([0, 1, 1, 0, 1, 0, 1, 1, 1], bool)
    assert corridas(v) == [(1, 3), (4, 5), (6, 9)]


def test_riego_diario_devuelve_la_cola_al_dia_del_riego_sin_cambiar_total() -> None:
    acum = np.array([0, 0, 40, 44, 44, 44, 70, 70])  # riego de 40 (+4 de cola) y de 26
    diario = riego_diario(acum)
    assert diario.tolist() == [0, 0, 44, 0, 0, 0, 26, 0]
    assert diario.sum() == pytest.approx(acum[-1])


def test_riego_diario_no_fusiona_riegos_consecutivos_reales() -> None:
    acum = np.array([0, 21, 43, 83])  # tres riegos seguidos de tamaño parecido
    assert riego_diario(acum).tolist() == [0, 21, 22, 40]


def test_deduplicar_un_marcador_por_tratamiento_y_columna() -> None:
    df = pd.DataFrame({
        "temporada": 1, "dia_x": [30.20, 30.25, 30.30, 40.2], "tratamiento": ["CD"] * 4,
        "vwc_pct": [41.0, 40.2, 39.5, 35.0], "ee_pct": [np.nan, 0.7, np.nan, 0.5],
        "fuente": ["rombo", "barra", "rombo", "barra"], "ancho_rombo": 8,
        "_fila": [100.0, 112.0, 124.0, 200.0], "_err_barra": [np.inf, 0.5, np.inf, 0.2],
    })
    out = deduplicar(df, tol_dias=0.6)
    assert len(out) == 2
    assert out.vwc_pct.tolist() == [40.2, 35.0]


# ---------- validación contra el artículo ----------

@pytest.fixture(scope="module")
def diario() -> pd.DataFrame:
    ruta = RAIZ / "data" / "processed" / "nataima_diario.csv"
    if not ruta.exists():
        pytest.skip("falta data/processed/nataima_diario.csv: ejecutar sira.digitalizar.ensamblar")
    return pd.read_csv(ruta)


@pytest.fixture(scope="module")
def riego() -> pd.DataFrame:
    return pd.read_csv(RAIZ / "data" / "interim" / "fig4_riego.csv")


def test_lluvia_total_dentro_de_2_por_ciento_de_tabla2(diario: pd.DataFrame) -> None:
    dia = diario.drop_duplicates(["temporada", "das"])
    for t, ref in LLUVIA_TABLA2.items():
        tot = dia[dia.temporada == t].lluvia_mm.sum()
        assert abs(tot - ref) / ref < 0.02, (t, tot, ref)


def test_riego_total_coincide_con_tabla2(diario: pd.DataFrame) -> None:
    for t, refs in RIEGO_TABLA2.items():
        for s, ref in zip(TRATAMIENTOS, refs):
            tot = diario[(diario.temporada == t) & (diario.tratamiento == s)].riego_mm.sum()
            assert tot == pytest.approx(ref, abs=5), (t, s, tot, ref)


def test_sumas_por_etapa_de_fig4_dentro_de_10_mm(riego: pd.DataFrame) -> None:
    cortes = cortes_etapa(riego)
    for t, g in riego.groupby("temporada"):
        g = g.set_index("das")
        b1, b2, _ = cortes[t]
        for k, s in enumerate(list(TRATAMIENTOS) + ["lluvia"]):
            v = g[f"riego_acum_{s}" if k < 3 else "lluvia_acum_fig4"]
            sumas = (v.loc[b1], v.loc[b2] - v.loc[b1], v.iloc[-1] - v.loc[b2])
            for dig, ref in zip(sumas, ETAPAS_FIG4[t][k]):
                assert abs(dig - ref) <= 10, (t, s, dig, ref)


def test_numero_de_riegos_cerca_de_tabla2(diario: pd.DataFrame) -> None:
    for t, refs in EVENTOS_TABLA2.items():
        for s, ref in zip(TRATAMIENTOS, refs):
            n = (diario[(diario.temporada == t) & (diario.tratamiento == s)].riego_mm >= 5).sum()
            assert abs(n - ref) <= 4, (t, s, n, ref)


def test_humedad_min_max_como_dice_el_texto(diario: pd.DataFrame) -> None:
    for (t, s), (vmin, vmax) in VWC_TEXTO.items():
        h = diario[(diario.temporada == t) & (diario.tratamiento == s)].vwc_pct.dropna()
        assert abs(h.min() - vmin) <= 1.0, (t, s, h.min(), vmin)
        if vmax is not None:
            assert abs(h.max() - vmax) <= 1.0, (t, s, h.max(), vmax)


def test_humedad_en_rango_fisico_y_sin_duplicados(diario: pd.DataFrame) -> None:
    h = diario.dropna(subset=["vwc_pct"])
    assert h.vwc_pct.between(20, 47.65).all()
    assert not h.duplicated(["temporada", "tratamiento", "das"]).any()
