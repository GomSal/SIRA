"""Piezas compartidas del Modelo B en operación: las dos ecuaciones que propagan y el ARIMAX guardado.

Ecuaciones (las mismas de scripts/modelo_humedad_unificado.py, con las que se entrenó el ARIMAX):
  S_τ(t) = e^(−1/τ)·S_τ(t−1) + L(t) + R(t) − Kc(t)·ETo(t)             τ = 20 y 5 días, S = 0 el día de siembra
  Dr(t)  = clip[Dr(t−1) − L(t) − R(t) + Ks·Kc(t)·ETo(t)], drena k = 0,5 si Dr < 0, límites −26,5 a 6,7 mm
  Kc = clip(1,0·NDVI + 0,45; 0,6; 1,3)
Entradas del ARIMAX (mismo orden que en el entrenamiento):
  S_tau20, S_tau5, Dr_k0.5, riego_5d, dias_desde_riego, eto_5d, ndvi
Salida del ARIMAX: μ = humedad volumétrica esperada (%, unidades del TDR de Nataima) y σ (su error típico).
"""
import json
from pathlib import Path

import numpy as np

RAIZ = Path(__file__).resolve().parents[2]
MODELO_B = RAIZ / "models/semaforo_b/arimax.json"
TAW, P_AG, ZR = 6.7, 0.65, 150.0
RAW, DSAT, K_DREN = P_AG * TAW, ZR * (0.4765 - 0.30), 0.5
ENTRADAS = ["S_tau20", "S_tau5", "Dr_k0.5", "riego_5d", "dias_desde_riego", "eto_5d", "ndvi"]
CC, DR_ROJO, MM_POR_PUNTO = 30.0, 4.34, ZR / 100  # capacidad de campo (%), déficit crítico (mm), mm por punto de humedad


def propagar(L, ETO, R, NDVI, activo, estado=None):
    """Recorre los días (eje 0) y devuelve (entradas del ARIMAX por día y serie, estado del último día).

    L, ETO, R, NDVI, activo: arreglos (días, ...). activo = el cultivo ya está sembrado ese día; antes de la siembra
    los estados se reinician (S = 0, Dr = 0, días desde riego = 60) como el día 1 del entrenamiento.
    estado: el que devolvió un tramo anterior (se transmite a la forma de este tramo), para seguir sin cortar.
    """
    forma = L.shape[1:]
    if estado is None:
        s20, s5, dr = np.zeros(forma), np.zeros(forma), np.zeros(forma)
        n = np.full(forma, 60.0)
        r_ult, e_ult = [], []  # últimos 5 días (riego y ETo) desde la siembra
    else:
        b = lambda x: np.broadcast_to(x, forma).copy()  # noqa: E731
        s20, s5, dr, n = (b(estado[c]) for c in ("s20", "s5", "dr", "n"))
        r_ult, e_ult = [b(x) for x in estado["r_ult"]], [b(x) for x in estado["e_ult"]]
    f20, f5 = np.exp(-1 / 20), np.exp(-1 / 5)
    out = {c: np.zeros(L.shape) for c in ENTRADAS}
    for t in range(L.shape[0]):
        a = activo[t]
        kc = np.clip(1.0 * NDVI[t] + 0.45, 0.6, 1.3)
        entra, sale = L[t] + R[t], kc * ETO[t]
        s20 = np.where(a, s20 * f20 + entra - sale, 0.0)
        s5 = np.where(a, s5 * f5 + entra - sale, 0.0)
        d = dr - entra
        ks = np.where(d <= RAW, 1.0, np.clip((TAW - d) / ((1 - P_AG) * TAW), 0, None))
        d = d + ks * sale
        d = np.where(d < 0, d * (1 - K_DREN), d)
        dr = np.where(a, np.clip(d, -DSAT, TAW), 0.0)
        n = np.where(a, np.where(R[t] > 0, 0.0, np.minimum(n + 1, 60)), 60.0)
        # antes de la siembra suman 0: la ventana de 5 días arranca el día 1, como en el entrenamiento
        r_ult = (r_ult + [np.where(a, R[t], 0.0)])[-5:]
        e_ult = (e_ult + [np.where(a, ETO[t], 0.0)])[-5:]
        for c, v in (("S_tau20", s20), ("S_tau5", s5), ("Dr_k0.5", dr), ("dias_desde_riego", n),
                     ("riego_5d", sum(r_ult)), ("eto_5d", sum(e_ult)), ("ndvi", NDVI[t])):
            out[c][t] = v
    return out, {"s20": s20, "s5": s5, "dr": dr, "n": n, "r_ult": r_ult, "e_ult": e_ult}


def propagar_dr(dr0, L, ETO, NDVI):
    """Tramo 2: déficit FAO-56 (k = 0,5) desde Dr0 = 1,5·(30 − μ de hoy), sin riego. Ejes: (días, ...). Devuelve Dr por día."""
    dr = np.clip(np.broadcast_to(dr0, L.shape[1:]).astype(float), -DSAT, TAW)
    out = np.zeros(L.shape)
    for t in range(L.shape[0]):
        kc = np.clip(1.0 * NDVI[t] + 0.45, 0.6, 1.3)
        d = dr - L[t]
        ks = np.where(d <= RAW, 1.0, np.clip((TAW - d) / ((1 - P_AG) * TAW), 0, None))
        d = d + ks * kc * ETO[t]
        d = np.where(d < 0, d * (1 - K_DREN), d)
        dr = np.clip(d, -DSAT, TAW)
        out[t] = dr
    return out


class ArimaxB:
    """ARIMAX(1) de uso guardado en models/semaforo_b/arimax.json: μ = c/(1 − φ) + Σ β·(x − media)/desviación."""

    def __init__(self, ruta=MODELO_B):
        m = json.load(open(ruta, encoding="utf-8"))
        assert m["entradas"] == ENTRADAS, "el orden de entradas no coincide con el del entrenamiento"
        self.m = m
        self.media, self.desv = np.array(m["media_x"]), np.array(m["desv_x"])
        self.beta, self.c, self.phi = np.array(m["beta"]), m["intercepto"], m["phi"]
        self.sigma = m["sigma_pct"]

    def mu(self, X):
        """X: diccionario de entradas (mismos ejes) → μ (%)."""
        z = sum(b * (X[c] - mu_) / sd for b, c, mu_, sd in zip(self.beta, ENTRADAS, self.media, self.desv))
        return z + self.c / (1 - self.phi)


def deficit_y_color(mu, sigma):
    """Regla aprobada (22-nov-2026): déficit = 1,5·(30 − μ) mm y se pinta con déficit + margen, margen = 1,5·σ.
    Intervalos de Martínez-Vega et al. 2025: suficiente ≤ 0 · media 0–4,34 mm · baja > 4,34 mm."""
    dr = MM_POR_PUNTO * (CC - mu)
    dr_pintar = dr + MM_POR_PUNTO * sigma
    clase = np.select([dr_pintar > DR_ROJO, dr_pintar > 0], [2, 1], 0)
    return dr, dr_pintar, clase
