"""Semáforo de humedad: UN solo clasificador de 3 clases, elegido en validación y medido una vez en prueba.

Clases (umbrales del artículo, Martínez-Vega et al. 2025, caracterización del suelo):
  verde     humedad TDR ≥ 30 %            (capacidad de campo ≈ 30 %)
  amarillo  27,1 % ≤ humedad < 30 %       (bajo capacidad de campo, antes del déficit crítico)
  rojo      humedad < 27,1 %              (déficit crítico 4,34 mm en 150 mm de raíz = 2,9 puntos bajo 30 %)
Datos: GS1 + GS2 + GS3 como un solo cultivo, 198 mediciones TDR; entradas de reports/humedad_unificada/
  (riego, lluvia CHIRPS, clima NASA POWER, NDVI Sentinel-2, balance de agua S y Dr, días desde siembra y etapa).

Separación entrenamiento / validación / prueba (por bloques de 10 días; las 3 franjas de un bloque van juntas):
  - Prueba: 4 particiones estratificadas por bloques. Cada una queda SELLADA mientras se elige el modelo con las otras 3.
  - Validación: dentro de esas 3, validación cruzada interna por bloques (5 particiones) → elige modelo e hiperparámetros.
  - El ganador se reentrena con entrenamiento + validación y se mide UNA vez en la prueba sellada.
  Así cada medición se prueba exactamente una vez, siempre con un modelo que nunca la vio.
Candidatos: regresión logística (multinomial), Random Forest, Gradient Boosting (sklearn), XGBoost y ARIMAX.
  ARIMAX: SARIMAX(p,0,0) con entradas exógenas sobre la humedad diaria de cada franja (series apiladas con huecos);
  en la app no hay TDR, así que predice solo con las entradas (el AR no puede usar humedad medida) y la clase sale de
  su distribución normal predicha (probabilidad de cada intervalo), corregida por la frecuencia de cada clase.
  Los demás se entrenan con clases balanceadas. Clase predicha = la de mayor probabilidad.
Criterio de selección (fijado antes de ver resultados): mayor AUC-ROC macro (cada clase contra las demás) en validación.
Métricas en prueba: AUC-ROC por clase y macro, recall y precisión por clase, exactitud balanceada, matriz de confusión;
  IC 95 % por bootstrap de bloques (1000). Modelo de uso: misma selección con todos los datos.
Salida: reports/semaforo_clasificador/
"""
import itertools
import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm
from sklearn.base import clone
from sklearn.decomposition import PCA
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import confusion_matrix, roc_auc_score
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.utils.class_weight import compute_sample_weight
from xgboost import XGBClassifier

warnings.filterwarnings("ignore")
RAIZ = Path(__file__).resolve().parents[1]
import sys  # noqa: E402

# "ndvi" (base) | "ndvi_ndmi" | "ndvi_prithvi" (cada candidato con y sin Prithvi; la validación decide)
# | "prithvi_obligatorio" (esquema cabeza sobre Prithvi: todos los candidatos llevan Prithvi)
VARIANTE = sys.argv[1] if len(sys.argv) > 1 else "ndvi"
OUT = RAIZ / "reports" / ("semaforo_clasificador" if VARIANTE == "ndvi" else f"semaforo_clasificador_{VARIANTE}")
OUT.mkdir(parents=True, exist_ok=True)
SEM = 0
CLASES = ["verde", "amarillo", "rojo"]
CORTES_HUM = (27.1, 30.0)
CON_PRITHVI = VARIANTE in ("ndvi_prithvi", "prithvi_obligatorio")
K_PCA = 3  # componentes de Prithvi (PCA ajustado solo con el entrenamiento de cada partición)

M = pd.read_csv(RAIZ / ("reports/humedad_unificada/tabla_entrenamiento_ndmi.csv" if VARIANTE == "ndvi_ndmi"
                       else "reports/humedad_unificada/tabla_entrenamiento.csv"))
M = M.sort_values(["temporada", "tratamiento", "das"]).reset_index(drop=True)
PCOLS = []
if CON_PRITHVI:
    # Prithvi-EO-2.0-300M-TL sobre HLS (bloque de 480 m que contiene el lote; un vector de 1024 por fecha, el mismo
    # para las 3 franjas). Se lleva a diario por interpolación lineal en días desde siembra, como el NDVI.
    E = pd.read_csv(RAIZ / "reports/modelo_b/prithvi_embeddings.csv")
    PCOLS = [c for c in E if c.startswith("p") and c[1:].isdigit()]
    E = E.groupby(["temporada", "das"], as_index=False)[PCOLS].mean()
    dia = []
    for t, g in M.groupby("temporada"):
        rejilla = pd.DataFrame({"temporada": t, "das": np.arange(min(g.das.min(), E[E.temporada == t].das.min()),
                                                                 max(g.das.max(), E[E.temporada == t].das.max()) + 1)})
        r = rejilla.merge(E[E.temporada == t], on=["temporada", "das"], how="left")
        r[PCOLS] = r[PCOLS].interpolate(limit_direction="both")
        dia.append(r)
    M = M.merge(pd.concat(dia), on=["temporada", "das"], how="left")
    assert M[PCOLS].notna().all().all()
hum = M.vwc_pct.to_numpy()
y = np.select([hum < CORTES_HUM[0], hum < CORTES_HUM[1]], [2, 1], 0)  # 0 verde, 1 amarillo, 2 rojo
G = M.grupo.to_numpy()

RIEGO = ["riego", "riego_3d", "riego_5d", "riego_10d", "dias_desde_riego"]
LLUVIA = ["lluvia", "lluvia_3d", "lluvia_5d", "lluvia_10d", "dias_desde_lluvia"]
CLIMA = ["eto", "eto_3d", "eto_5d", "eto_10d", "T2M_MAX", "RH2M", "ALLSKY_SFC_SW_DWN"]
PLANTA = ["ndvi", "kc"]
BAL = [f"S_tau{t}" for t in (2, 5, 10, 20)] + [f"Dr_k{k}" for k in (0.2, 0.5, 0.8)]
TIEMPO = ["das", "etapa_vegetativa", "etapa_reproductiva", "etapa_maduracion"]
COLS = RIEGO + LLUVIA + CLIMA + PLANTA + BAL + TIEMPO
EXOG_ARIMAX = ["S_tau20", "S_tau5", "Dr_k0.5", "riego_5d", "dias_desde_riego", "eto_5d", "ndvi"]
if VARIANTE == "ndvi_ndmi":  # NDMI de Sentinel-2 por franja, mismo procesamiento que el NDVI
    COLS = COLS + ["ndmi"]
    EXOG_ARIMAX = EXOG_ARIMAX + ["ndmi"]


# ---------------------------------------------------------------- ARIMAX como clasificador
class ARIMAXClasificador:
    """SARIMAX(p,0,0) + exógenas sobre las series diarias de humedad de cada franja, apiladas con 30 días vacíos entre
    series. Se ajusta con la humedad solo en las mediciones de entrenamiento (las demás, NaN: el filtro de Kalman las
    salta). Para predecir NO usa humedad medida (en la app no hay TDR): media = exógenas·β, varianza del proceso."""

    def __init__(self, p=1):
        self.p = p

    def fit(self, X, y_cls, hum_obs):
        from statsmodels.tsa.statespace.sarimax import SARIMAX
        self.mu_x, self.sd_x = X.mean(0), X.std(0) + 1e-9
        Z = (X - self.mu_x) / self.sd_x
        # serie apilada: una fila por medición de entrenamiento, con huecos de días entre mediciones (NaN)
        endog, exog = self._serie(Z, hum_obs, self._clave)
        self.res = SARIMAX(endog, exog=exog, order=(self.p, 0, 0), trend="c").fit(disp=False, maxiter=200)
        self.prior = np.bincount(y_cls, minlength=3) / len(y_cls)
        return self

    def _serie(self, Z, hum_obs, clave):
        # clave = (temporada, tratamiento, das) de cada fila; se arma la serie diaria completa con NaN en días sin dato
        cols = [f"x{i}" for i in range(Z.shape[1])]
        df = pd.DataFrame(Z, columns=cols)
        df["hum"] = hum_obs
        df[["t", "tr", "das"]] = pd.DataFrame(list(clave), index=df.index)
        endog, exog = [], []
        for (_, _), g in df.groupby(["t", "tr"]):
            g = g.sort_values("das")
            full = pd.DataFrame({"das": np.arange(g.das.min(), g.das.max() + 1)}).merge(g, on="das", how="left")
            full[cols] = full[cols].interpolate(limit_direction="both")
            endog += list(full.hum) + [np.nan] * 30
            exog += full[cols].values.tolist() + [[0.0] * len(cols)] * 30
        return np.array(endog, float), np.array(exog, float)

    def predict_proba(self, X):
        Z = (X - self.mu_x) / self.sd_x
        par = dict(zip(self.res.model.param_names, np.asarray(self.res.params)))
        beta = np.array([par[f"x{i + 1}"] for i in range(Z.shape[1])])
        phi = np.array([par.get(f"ar.L{i + 1}", 0.0) for i in range(self.p)])
        c = par.get("intercept", 0.0)
        mu = Z @ beta + c / max(1 - phi.sum(), 1e-3)  # regresión con errores AR: E[y] = x·β + c/(1 − Σφ)
        # varianza estacionaria de un AR(p) aproximada con AR(1) equivalente
        s2 = par["sigma2"] / max(1 - (phi ** 2).sum(), 1e-3)
        sd = np.sqrt(s2)
        p_rojo = norm.cdf((CORTES_HUM[0] - mu) / sd)
        p_am = norm.cdf((CORTES_HUM[1] - mu) / sd) - p_rojo
        P = np.c_[1 - p_rojo - p_am, p_am, p_rojo]
        P = P / self.prior  # corrección por frecuencia de clase (equivale a balancear)
        return P / P.sum(1, keepdims=True)


def candidatos():
    c = []
    for C in (0.03, 0.1, 0.3, 1.0):
        c.append(("regresión logística", {"C": C},
                  lambda C=C: make_pipeline(StandardScaler(), LogisticRegression(C=C, class_weight="balanced", max_iter=5000))))
    for d, l in itertools.product((3, 5, None), (3, 8)):
        c.append(("Random Forest", {"max_depth": d, "min_samples_leaf": l},
                  lambda d=d, l=l: RandomForestClassifier(n_estimators=500, max_depth=d, min_samples_leaf=l,
                                                          class_weight="balanced", random_state=SEM, n_jobs=-1)))
    for d, lr in itertools.product((1, 2, 3), (0.03, 0.1)):
        c.append(("Gradient Boosting", {"max_depth": d, "learning_rate": lr},
                  lambda d=d, lr=lr: GradientBoostingClassifier(n_estimators=200, max_depth=d, learning_rate=lr,
                                                                subsample=0.8, random_state=SEM)))
    for d, lr, w in itertools.product((1, 2, 3), (0.03, 0.1), (3, 8)):
        c.append(("XGBoost", {"max_depth": d, "learning_rate": lr, "min_child_weight": w},
                  lambda d=d, lr=lr, w=w: XGBClassifier(n_estimators=300, max_depth=d, learning_rate=lr, min_child_weight=w,
                                                        subsample=0.8, objective="multi:softprob", random_state=SEM,
                                                        n_jobs=4, verbosity=0)))
    for p in (1, 2):
        c.append(("ARIMAX", {"p": p}, lambda p=p: ARIMAXClasificador(p)))
    if VARIANTE == "ndvi_prithvi":  # cada candidato, también con Prithvi
        c = c + [(n + " + Prithvi", {**hp, "prithvi_pca": K_PCA}, f) for n, hp, f in c]
    elif VARIANTE == "prithvi_obligatorio":
        c = [(n + " + Prithvi", {**hp, "prithvi_pca": K_PCA}, f) for n, hp, f in c]
    return c


CLAVE = list(zip(M.temporada, M.tratamiento, M.das))


def matriz(nombre, m, idx, ajustar_pca=False):
    """Entradas del candidato; con Prithvi agrega K_PCA componentes (PCA ajustado solo con fechas de entrenamiento)."""
    base = nombre.replace(" + Prithvi", "")
    cols = EXOG_ARIMAX if base == "ARIMAX" else COLS
    X = M.loc[idx, cols].to_numpy(float)
    if nombre.endswith(" + Prithvi"):
        if ajustar_pca:
            u = M.loc[idx].drop_duplicates(["temporada", "das"])[PCOLS].to_numpy()
            m._pca = make_pipeline(StandardScaler(), PCA(K_PCA, random_state=SEM)).fit(u)
        X = np.hstack([X, m._pca.transform(M.loc[idx, PCOLS].to_numpy())])
    return base, X


def ajustar(nombre, fabrica, idx):
    m = fabrica()
    base, X = matriz(nombre, m, idx, ajustar_pca=True)
    if base == "ARIMAX":
        m._clave = [CLAVE[i] for i in idx]
        return m.fit(X, y[idx], hum[idx])
    if base in ("Gradient Boosting", "XGBoost"):
        return m.fit(X, y[idx], sample_weight=compute_sample_weight("balanced", y[idx]))
    return m.fit(X, y[idx])


def proba(nombre, m, idx):
    base, X = matriz(nombre, m, idx)
    if base == "ARIMAX":
        return m.predict_proba(X)
    P = m.predict_proba(X)
    out = np.zeros((len(idx), 3))
    out[:, m.classes_ if hasattr(m, "classes_") else m[-1].classes_] = P
    return out


def auc_macro(yy, P):
    aucs = [roc_auc_score(yy == k, P[:, k]) for k in range(3) if 0 < (yy == k).sum() < len(yy)]
    return float(np.mean(aucs)) if aucs else np.nan


def seleccionar(idx_dev, semilla):
    """Validación cruzada interna por bloques sobre los datos de desarrollo; gana el mayor AUC-ROC macro."""
    inner = StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=semilla)
    filas = []
    for nombre, hp, fab in candidatos():
        P = np.full((len(idx_dev), 3), np.nan)
        for a, b in inner.split(idx_dev, y[idx_dev], G[idx_dev]):
            try:
                m = ajustar(nombre, fab, idx_dev[a])
                P[b] = proba(nombre, m, idx_dev[b])
            except Exception as e:  # noqa: BLE001
                print("fallo", nombre, hp, e)
        ok = ~np.isnan(P[:, 0])
        filas.append({"modelo": nombre, "hiperparametros": json.dumps(hp), "auc_macro_val": auc_macro(y[idx_dev][ok], P[ok]),
                      "recall_rojo_val": float(np.mean(P[ok][y[idx_dev][ok] == 2].argmax(1) == 2)) if (y[idx_dev][ok] == 2).any() else np.nan,
                      "_fab": fab})
    V = pd.DataFrame(filas)
    g = V.loc[V.auc_macro_val.idxmax()]
    return V, g


def metricas(yy, P):
    pred = P.argmax(1)
    out = {"auc_macro": auc_macro(yy, P), "exactitud_balanceada": float(np.mean([np.mean(pred[yy == k] == k) for k in range(3) if (yy == k).any()]))}
    for k, c in enumerate(CLASES):
        out[f"auc_{c}"] = roc_auc_score(yy == k, P[:, k]) if 0 < (yy == k).sum() < len(yy) else np.nan
        out[f"recall_{c}"] = float(np.mean(pred[yy == k] == k)) if (yy == k).any() else np.nan
        out[f"precision_{c}"] = float(np.mean(yy[pred == k] == k)) if (pred == k).any() else np.nan
        out[f"n_{c}"] = int((yy == k).sum())
    return out


# ---------------------------------------------------------------- separación externa: prueba sellada
outer = StratifiedGroupKFold(n_splits=4, shuffle=True, random_state=SEM)
P_test = np.full((len(M), 3), np.nan)
reg, todas_val = [], []
for f, (dev, te) in enumerate(outer.split(M, y, G)):
    V, g = seleccionar(dev, SEM + f + 1)
    todas_val.append(V.drop(columns="_fab").assign(particion_prueba=f + 1))
    m = ajustar(g.modelo, g._fab, dev)  # reentrena con entrenamiento + validación
    P_test[te] = proba(g.modelo, m, te)  # prueba: una sola vez
    mt = metricas(y[te], P_test[te])
    reg.append({"particion_prueba": f + 1, "n_prueba": len(te), "elegido": g.modelo, "hiperparametros": g.hiperparametros,
                "auc_macro_validacion": g.auc_macro_val, **mt})
    print(f"prueba {f + 1}: elegido {g.modelo} {g.hiperparametros} | AUC val {g.auc_macro_val:.2f} | "
          f"AUC prueba {mt['auc_macro']:.2f} | recall verde/amarillo/rojo {mt['recall_verde']:.2f}/"
          f"{mt['recall_amarillo']:.2f}/{mt['recall_rojo']:.2f}", flush=True)
VAL = pd.concat(todas_val)
VAL.round(4).to_csv(OUT / "validacion.csv", index=False)
print("\nMejor AUC macro de validación por familia (promedio de las 4 particiones):")
print(VAL.groupby(["particion_prueba", "modelo"]).auc_macro_val.max().unstack(0).mean(1).sort_values(ascending=False).round(3).to_string())
REG = pd.DataFrame(reg)
REG.round(4).to_csv(OUT / "prueba_por_particion.csv", index=False)

# ---------------------------------------------------------------- prueba agregada (cada medición probada una vez)
tot = metricas(y, P_test)
rng = np.random.default_rng(SEM)
u = np.unique(G)
idx = {g_: np.where(G == g_)[0] for g_ in u}
B = []
for _ in range(1000):
    ii = np.concatenate([idx[g_] for g_ in rng.choice(u, len(u), replace=True)])
    if len(np.unique(y[ii])) == 3:
        B.append(metricas(y[ii], P_test[ii]))
B = pd.DataFrame(B)
ic = {f"{k}_IC95": f"{B[k].quantile(.025):.2f}–{B[k].quantile(.975):.2f}" for k in B.columns if not k.startswith("n_")}
CM = pd.DataFrame(confusion_matrix(y, P_test.argmax(1), labels=[0, 1, 2]), index=[f"real {c}" for c in CLASES],
                  columns=[f"pred {c}" for c in CLASES])
CM.to_csv(OUT / "matriz_confusion_prueba.csv")
pd.DataFrame([{**tot, **ic}]).round(4).to_csv(OUT / "prueba_total.csv", index=False)
M[["temporada", "tratamiento", "das", "etapa", "vwc_pct"]].assign(clase=[CLASES[k] for k in y],
    **{f"p_{c}": P_test[:, k] for k, c in enumerate(CLASES)}, pred=[CLASES[k] for k in P_test.argmax(1)]).to_csv(
    OUT / "predicciones_prueba.csv", index=False)
print("\nPRUEBA (cada medición una vez):")
for k in ["auc_macro", "auc_verde", "auc_amarillo", "auc_rojo", "recall_verde", "recall_amarillo", "recall_rojo",
          "precision_verde", "precision_amarillo", "precision_rojo", "exactitud_balanceada"]:
    print(f"  {k:22s} {tot[k]:.2f}  [{ic.get(k + '_IC95', '')}]")
print(CM.to_string())

# ---------------------------------------------------------------- modelo de uso: misma selección con todos los datos
Vall, gall = seleccionar(np.arange(len(M)), SEM + 99)
Vall.drop(columns="_fab").round(4).to_csv(OUT / "validacion_modelo_de_uso.csv", index=False)
(OUT / "modelo_de_uso.json").write_text(json.dumps({
    "elegido": gall.modelo, "hiperparametros": json.loads(gall.hiperparametros), "auc_macro_validacion": gall.auc_macro_val,
    "entradas": (EXOG_ARIMAX if gall.modelo.startswith("ARIMAX") else COLS)
                + ([f"prithvi_pc{i + 1}" for i in range(K_PCA)] if gall.modelo.endswith(" + Prithvi") else []), "clases": {"verde": "≥ 30 %", "amarillo": "27,1–30 %", "rojo": "< 27,1 %"},
    "error_esperado": "el de la prueba sellada (prueba_total.csv)"}, indent=1, ensure_ascii=False), encoding="utf-8")
print("\nModelo de uso (selección con todos los datos):", gall.modelo, gall.hiperparametros, round(gall.auc_macro_val, 3))
