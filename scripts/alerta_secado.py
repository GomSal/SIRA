"""Alerta de secado del suelo: AUC-ROC, recall y precisión, con GS1 + GS2 + GS3 como un solo cultivo.

Dos alertas (respuesta = TDR medido, 198 mediciones):
  "seco"     humedad < 30 % (por debajo de capacidad de campo)      61 positivos
  "déficit"  humedad < 27,1 % (bajo el agua fácilmente aprovechable) 17 positivos (todas en AWD+MIRI)
Entradas: las de scripts/modelo_humedad_unificado.py (tabla reports/humedad_unificada/tabla_entrenamiento.csv):
  completo = riego + lluvia + clima + NDVI + balance de agua + etapa; y "sin riego".
Modelos: regresión logística (balanceada), logística solo con el balance de agua, Random Forest (balanceado),
  XGBoost (scale_pos_weight) y, como referencia, el puntaje de la regresión de humedad (Random Forest regresor).
Validación: bloques de 10 días (10 particiones; las 3 franjas de un bloque salen juntas) y una temporada afuera.
Umbral de alerta: se elige DENTRO del entrenamiento de cada partición, con predicciones fuera de muestra de una
  validación interna por bloques (5 particiones), maximizando el índice de Youden (recall + especificidad − 1).
  La prueba nunca toca el umbral.
Métricas sobre las predicciones fuera de muestra: AUC-ROC, AUC-PR, recall, precisión, especificidad, F1, exactitud
  balanceada; IC 95 % por bootstrap de bloques de 10 días (1000 repeticiones).
Salida: reports/alerta_secado/
"""
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score, roc_curve
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

warnings.filterwarnings("ignore")
RAIZ = Path(__file__).resolve().parents[1]
OUT = RAIZ / "reports" / "alerta_secado"
OUT.mkdir(parents=True, exist_ok=True)
SEM = 0
M = pd.read_csv(RAIZ / "reports/humedad_unificada/tabla_entrenamiento.csv")
G = M.grupo.to_numpy()

RIEGO = ["riego", "riego_3d", "riego_5d", "riego_10d", "dias_desde_riego"]
LLUVIA = ["lluvia", "lluvia_3d", "lluvia_5d", "lluvia_10d", "dias_desde_lluvia"]
CLIMA = ["eto", "eto_3d", "eto_5d", "eto_10d", "T2M_MAX", "RH2M", "ALLSKY_SFC_SW_DWN"]
PLANTA = ["ndvi", "kc"]
BAL = [f"S_tau{t}" for t in (2, 5, 10, 20)] + [f"Dr_k{k}" for k in (0.2, 0.5, 0.8)]
BAL_SR = [f"S_tau{t}_sin_riego" for t in (2, 5, 10, 20)] + [f"Dr_k{k}_sin_riego" for k in (0.2, 0.5, 0.8)]
TIEMPO = ["das", "etapa_vegetativa", "etapa_reproductiva", "etapa_maduracion"]
CONJ = {"completo": RIEGO + LLUVIA + CLIMA + PLANTA + BAL + TIEMPO,
        "sin riego": LLUVIA + CLIMA + PLANTA + BAL_SR + TIEMPO}
ALERTAS = {"seco (< 30 %)": 30.0, "déficit (< 27,1 %)": 27.1}


def modelos(conj, pos_ratio):
    cols = CONJ[conj]
    bal = [c for c in cols if c in BAL + BAL_SR] + ["riego_5d", "riego_10d"] * (conj == "completo")
    return {
        "logística": (make_pipeline(StandardScaler(), LogisticRegression(C=0.3, class_weight="balanced", max_iter=5000)), cols, "clf"),
        "logística solo balance de agua": (make_pipeline(StandardScaler(), LogisticRegression(C=0.3, class_weight="balanced",
                                                                                              max_iter=5000)), bal, "clf"),
        "Random Forest": (RandomForestClassifier(n_estimators=500, min_samples_leaf=5, max_depth=5, class_weight="balanced",
                                                 random_state=SEM, n_jobs=-1), cols, "clf"),
        "XGBoost": (XGBClassifier(n_estimators=300, max_depth=2, learning_rate=0.05, min_child_weight=3, subsample=0.8,
                                  scale_pos_weight=pos_ratio, random_state=SEM, n_jobs=4, verbosity=0), cols, "clf"),
        "regresión de humedad (RF) como puntaje": (RandomForestRegressor(n_estimators=500, min_samples_leaf=3, max_depth=5,
                                                                         random_state=SEM, n_jobs=-1), cols, "reg"),
    }


def puntaje(est, tipo, X):
    return est.predict_proba(X)[:, 1] if tipo == "clf" else -est.predict(X)


def ajustar(est, tipo, X, y, hum):
    return clone(est).fit(X, y if tipo == "clf" else hum)


def umbral_youden(y, s):
    if len(np.unique(y)) < 2:
        return np.median(s)
    fpr, tpr, thr = roc_curve(y, s)
    return thr[np.argmax(tpr - fpr)]


def cortes(esquema):
    if esquema == "bloques de 10 días":
        return list(GroupKFold(n_splits=10).split(M, groups=G))
    return [(np.where(M.temporada != t)[0], np.where(M.temporada == t)[0]) for t in (1, 2, 3)]


def correr(alerta, conj, nombre, esquema):
    y = (M.vwc_pct < ALERTAS[alerta]).astype(int).to_numpy()
    hum = M.vwc_pct.to_numpy()
    est, cols, tipo = modelos(conj, (y == 0).sum() / max(y.sum(), 1))[nombre]
    X = M[cols]
    s_out, alerta_out = np.full(len(M), np.nan), np.zeros(len(M), int)
    for tr, te in cortes(esquema):
        # umbral: predicciones fuera de muestra DENTRO del entrenamiento (bloques), Youden
        s_in = np.full(len(tr), np.nan)
        for a, b in GroupKFold(n_splits=5).split(tr, groups=G[tr]):
            if tipo == "clf" and len(np.unique(y[tr][a])) < 2:
                continue
            m = ajustar(est, tipo, X.iloc[tr[a]], y[tr][a], hum[tr][a])
            s_in[b] = puntaje(m, tipo, X.iloc[tr[b]])
        ok = ~np.isnan(s_in)
        thr = umbral_youden(y[tr][ok], s_in[ok])
        m = ajustar(est, tipo, X.iloc[tr], y[tr], hum[tr])
        s_out[te] = puntaje(m, tipo, X.iloc[te])
        alerta_out[te] = (s_out[te] >= thr).astype(int)
    return y, s_out, alerta_out


def metricas(y, s, a):
    tp, fp = int(((a == 1) & (y == 1)).sum()), int(((a == 1) & (y == 0)).sum())
    fn, tn = int(((a == 0) & (y == 1)).sum()), int(((a == 0) & (y == 0)).sum())
    rec = tp / (tp + fn) if tp + fn else np.nan
    pre = tp / (tp + fp) if tp + fp else np.nan
    esp = tn / (tn + fp) if tn + fp else np.nan
    return {"auc_roc": roc_auc_score(y, s) if len(np.unique(y)) > 1 else np.nan,
            "auc_pr": average_precision_score(y, s) if y.sum() else np.nan, "prevalencia": y.mean(),
            "recall": rec, "precision": pre, "especificidad": esp,
            "f1": 2 * pre * rec / (pre + rec) if pre and rec and pre + rec > 0 else 0.0,
            "exactitud_balanceada": np.nanmean([rec, esp]), "vp": tp, "fp": fp, "fn": fn, "vn": tn}


def ic_bloques(y, s, a, n=1000):
    rng = np.random.default_rng(SEM)
    u = np.unique(G)
    idx = {g: np.where(G == g)[0] for g in u}
    B = []
    for _ in range(n):
        ii = np.concatenate([idx[g] for g in rng.choice(u, size=len(u), replace=True)])
        if len(np.unique(y[ii])) < 2:
            continue
        mm = metricas(y[ii], s[ii], a[ii])
        B.append((mm["auc_roc"], mm["recall"], mm["precision"]))
    B = np.array(B, float)
    f = lambda k: f"{np.nanpercentile(B[:, k], 2.5):.2f}–{np.nanpercentile(B[:, k], 97.5):.2f}"  # noqa: E731
    return {"auc_roc_IC95": f(0), "recall_IC95": f(1), "precision_IC95": f(2)}


res, preds = [], []
for alerta in ALERTAS:
    for conj in CONJ:
        for esquema in ("bloques de 10 días", "una temporada afuera"):
            for nombre in modelos(conj, 1.0):
                y, s, a = correr(alerta, conj, nombre, esquema)
                mm = metricas(y, s, a)
                fila = {"alerta": alerta, "entradas": conj, "validacion": esquema, "modelo": nombre, **mm}
                if esquema == "bloques de 10 días":
                    fila.update(ic_bloques(y, s, a))
                res.append(fila)
                preds.append(M[["temporada", "tratamiento", "das", "etapa", "vwc_pct"]].assign(
                    alerta=alerta, entradas=conj, validacion=esquema, modelo=nombre, y=y, puntaje=s, alerta_emitida=a))
                print(f"{alerta:18s} | {conj:9s} | {esquema:20s} | {nombre:38s} | AUC {mm['auc_roc']:.2f} "
                      f"recall {mm['recall']:.2f} precisión {mm['precision']:.2f} especif. {mm['especificidad']:.2f} "
                      f"(VP {mm['vp']} FN {mm['fn']} FP {mm['fp']})", flush=True)
R = pd.DataFrame(res)
R.round(3).to_csv(OUT / "resultados.csv", index=False)
pd.concat(preds).to_csv(OUT / "predicciones.csv", index=False)
