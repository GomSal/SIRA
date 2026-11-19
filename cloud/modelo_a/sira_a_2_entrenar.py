# %% [markdown]
# # SIRA · Modelo predictor (A) · Notebook 2: entrenamiento (8 pasos)
#
# Pronostica el **NDVI y el NDMI de cada pixel de 1 a 30 días adelante**, en la escala de Sentinel-2 que usa el
# semáforo (B). Entrada: la salida del notebook 1 (`datos_a/`). **Acelerador: GPU.** Internet: encendido (para bajar
# Prithvi de Hugging Face y 4 archivos del repositorio SIRA en GitHub).
#
# Pasos: 1 exploración · 2 calidad (bruma, calibración Landsat → S2, siembras, Prithvi frente a interpolación con
# nubes falsas) · 3 partición (ventana deslizante; prueba sellada desde 2025-11-18) · 4 métricas (RMSE, MAE, sesgo y
# skill frente a persistencia y climatología, por horizonte) · 5 estandarización (solo con el entrenamiento de cada
# partición) · 6 selección de variables (entra un grupo solo si el IC 95 % de su mejora excluye el 0) y curva de
# aprendizaje · 7 ARIMAX, Random Forest, Gradient Boosting, XGBoost y GRU, elegidos en validación · 8 prueba sellada
# una vez, con clima observado y con GEOS-S2S corregido, y semáforo B hacia adelante contra el TDR de Nataima.
#
# Salida: `/kaggle/working/resultados_a/` (en local: `./salida_a2/`). Log: `resultados_a/log_entrenar.txt`.

# %%
import os
import subprocess
import sys

QA = os.environ.get("SIRA_QA", "0") == "1"
EN_KAGGLE = os.path.exists("/kaggle/working")
if EN_KAGGLE:
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "einops", "timm", "huggingface_hub"], check=False)

# %%
import datetime as dt
import glob
import json
import time
import traceback
import urllib.request
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
T0 = time.time()
PRESUPUESTO_S = (11.3 if not QA else 2.0) * 3600  # Kaggle corta a las 12 h
if EN_KAGGLE:
    cands = glob.glob("/kaggle/input/**/cubo.npz", recursive=True)
    assert cands, "no encuentro cubo.npz: agregue la salida del notebook 1 como entrada (Add data → Notebook output)"
    IN = Path(cands[0]).parent
    OUT = Path("/kaggle/working/resultados_a")
else:
    IN = Path(os.environ.get("SIRA_IN_A2", "salida_a1"))
    OUT = Path(os.environ.get("SIRA_OUT_A2", "salida_a2"))
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "modelos").mkdir(exist_ok=True)
LOG = open(OUT / "log_entrenar.txt", "a", encoding="utf-8")


def log(m):
    s = f"[{dt.datetime.utcnow():%H:%M:%S} +{(time.time() - T0) / 60:6.1f} min] {m}"
    print(s, flush=True)
    LOG.write(s + "\n")
    LOG.flush()


def queda():
    return PRESUPUESTO_S - (time.time() - T0)


def guardar(df, nombre):
    df.to_csv(OUT / nombre, index=False)


import torch  # noqa: E402

DEV = "cuda" if torch.cuda.is_available() else "cpu"
NUC = os.cpu_count() or 4
SEM = 0
rng = np.random.default_rng(SEM)
torch.manual_seed(SEM)
REPO = "https://raw.githubusercontent.com/GomSal/SIRA/bda96a946a4b97bcb1c7710017327959c3f065c0/"  # commit fijo con los datos de B
BASE = pd.Timestamp("2000-01-01")
SELLO = pd.Timestamp("2025-11-18")
ANIOS = 10
FOLDS = [(pd.Timestamp("2022-11-18"), pd.Timestamp("2023-11-18")), (pd.Timestamp("2023-11-18"), pd.Timestamp("2024-11-18")),
         (pd.Timestamp("2024-11-18"), SELLO)]
BINS = [(1, 2, "1"), (3, 5, "3"), (6, 10, "7"), (11, 20, "15"), (21, 30, "30")]  # horizonte nominal 1, 3, 7, 15, 30 días
N_PIX_MAX = 300 if QA else 6000
N_PIX_EVAL = 150 if QA else 1500
SIEMBRAS_NATAIMA = {1: "2021-05-27", 2: "2021-12-04", 3: "2022-07-09"}
log(f"inicio · QA={QA} · dispositivo {DEV} · {NUC} núcleos · entrada {IN}")
json.dump({"dispositivo": DEV, "nucleos": NUC, "torch": torch.__version__, "python": sys.version}, open(OUT / "entorno.json", "w", encoding="utf-8"))

# %% [markdown]
# ## Datos del notebook 1

# %%
z = np.load(IN / "cubo.npz")
NDVI_ALL, NDMI_ALL, AZUL_ALL = z["ndvi"], z["ndmi"], z["azul"]
NG, X0, Y0 = int(z["n"]), float(z["x0"]), float(z["y0"])
meta = pd.read_csv(IN / "cubo_fechas.csv", parse_dates=["fecha"])
PLAT = meta.plataforma.str.replace(r"Sentinel-2[A-Z]", "S2", regex=True).to_numpy()
DIA = (meta.fecha - BASE).dt.days.to_numpy()
cl = pd.read_csv(IN / "clima.csv", parse_dates=["fecha"]).set_index("fecha")
FIN_TEST = min(cl.lluvia.last_valid_index(), pd.Timestamp("2026-11-18"))
log(f"{len(meta)} imágenes · rejilla {NG}×{NG} · clima hasta {cl.index.max().date()} · CHIRPS hasta {FIN_TEST.date()}")

# clima diario desde BASE: lluvia CHIRPS (huecos con POWER) y ETo FAO-56 (POWER)
dias_cl = pd.date_range(BASE, cl.index.max())
cl = cl.reindex(dias_cl)
LL = cl.lluvia.fillna(cl.PRECTOTCORR).fillna(0).to_numpy()
ET = cl.eto.interpolate().bfill().ffill().to_numpy()
CLL, CET = np.r_[0, np.cumsum(LL)], np.r_[0, np.cumsum(ET)]


def suma(c, a, b):
    """Suma del día a+1 al día b (días desde BASE)."""
    n = len(c) - 2
    return c[np.clip(b, -1, n) + 1] - c[np.clip(a, -1, n) + 1]


oni = pd.read_csv(IN / "oni.csv")
MES_SEAS = {"DJF": 1, "JFM": 2, "FMA": 3, "MAM": 4, "AMJ": 5, "MJJ": 6, "JJA": 7, "JAS": 8, "ASO": 9, "SON": 10, "OND": 11, "NDJ": 12}
oni["mes"] = oni.SEAS.map(MES_SEAS)
ONI = {(int(r.YR), int(r.mes)): float(r.ANOM) for r in oni.itertuples()}

# %% [markdown]
# ## Paso 2a. Selección de pixeles agrícolas, calibración Landsat → Sentinel-2 y bruma

# %%
es_s2 = PLAT == "S2"
pre = (meta.fecha < SELLO).to_numpy()
sd = np.zeros(NG * NG, np.float32)
nobs = np.zeros(NG * NG, np.int32)
for a in range(0, NG * NG, 20000):
    blk = NDVI_ALL[es_s2 & pre, a:a + 20000].astype(np.float32)
    sd[a:a + 20000] = np.nanstd(blk, axis=0)
    nobs[a:a + 20000] = np.isfinite(blk).sum(0)
MIN_OBS = 10 if QA else 50
agri = np.where((sd >= 0.15) & (nobs >= MIN_OBS))[0]
log(f"pixeles con ≥{MIN_OBS} obs S2: {(nobs >= MIN_OBS).sum()} · agrícolas (sd NDVI ≥ 0,15): {len(agri)} de {NG * NG}")

# pixeles del lote (franjas) para la evaluación del semáforo B
def bajar_repo(ruta):
    destino = OUT / "repo" / ruta
    destino.parent.mkdir(parents=True, exist_ok=True)
    if not destino.exists():
        local = Path(__file__).resolve().parents[2] / ruta if "__file__" in globals() else None
        if local is not None and local.exists():
            destino.write_bytes(local.read_bytes())
        else:
            destino.write_bytes(urllib.request.urlopen(REPO + ruta, timeout=120).read())
    return destino


pix_lote = pd.read_csv(bajar_repo("data/processed/modelo_b/pixeles_lote_s2_landsat.csv"))
pix_lote = pix_lote[pix_lote.sensor == "S2"].drop_duplicates("pixel_id").copy()
xy = pix_lote.pixel_id.str.split("_", expand=True).astype(float)
pix_lote["col"] = ((xy[1] - X0) // 10).astype(int)
pix_lote["fil"] = ((Y0 - xy[2]) // 10).astype(int)
pix_lote = pix_lote[(pix_lote.col.between(0, NG - 1)) & (pix_lote.fil.between(0, NG - 1))]
pix_lote["idx"] = pix_lote.fil * NG + pix_lote.col
LOTE = pix_lote.idx.to_numpy()

perm = rng.permutation(agri)
SEL = np.unique(np.r_[perm[:N_PIX_MAX], LOTE])
ORDEN = {p: k for k, p in enumerate(perm[:N_PIX_MAX])}  # orden fijo para la curva de aprendizaje
log(f"muestra: {min(N_PIX_MAX, len(perm))} pixeles agrícolas + {len(LOTE)} del lote = {len(SEL)}")
V = NDVI_ALL[:, SEL].astype(np.float32)
W = NDMI_ALL[:, SEL].astype(np.float32)
AZ = AZUL_ALL[:, SEL].astype(np.float32)
del NDVI_ALL, NDMI_ALL, AZUL_ALL

# calibración por plataforma: recta S2 = a + b·L con pares del mismo pixel a ≤ 2 días, antes del sello
cal = []
for p in ("landsat-7", "landsat-8", "landsat-9"):
    for nom, M in (("ndvi", V), ("ndmi", W)):
        xs, ys = [], []
        for i in np.where((PLAT == p) & pre)[0]:
            for j in np.where(es_s2 & (np.abs(DIA - DIA[i]) <= 2))[0]:
                m = np.isfinite(M[i]) & np.isfinite(M[j])
                xs.append(M[i, m]); ys.append(M[j, m])
        x = np.concatenate(xs) if xs else np.array([])
        y = np.concatenate(ys) if ys else np.array([])
        b, a = np.polyfit(x, y, 1) if len(x) > 50 else (1.0, 0.0)
        cal.append({"plataforma": p, "indice": nom, "pares": len(x), "a": a, "b": b,
                    "r2": float(np.corrcoef(x, y)[0, 1] ** 2) if len(x) > 50 else np.nan,
                    "rmse_antes": float(np.sqrt(np.mean((x - y) ** 2))) if len(x) else np.nan,
                    "rmse_despues": float(np.sqrt(np.mean((a + b * x - y) ** 2))) if len(x) else np.nan})
        filas = PLAT == p
        M[filas] = a + b * M[filas]
guardar(pd.DataFrame(cal), "paso2_calibracion_landsat_s2.csv")
log("calibración Landsat → S2: " + "; ".join(f"{r['plataforma']} {r['indice']} R²={r['r2']:.2f} n={r['pares']}" for r in cal))

# bruma (informe §4, por pixel): cae > 0,10 frente a la fecha válida anterior y a la siguiente (≤ 30 días), o
# azul ≥ 0,075 con vecinas vegetadas (NDVI > 0,5)
orden_t = np.lexsort((PLAT != "S2", DIA))  # por fecha; el mismo día, Sentinel-2 primero
V, W, AZ, DIA_O, PLAT_O = V[orden_t], W[orden_t], AZ[orden_t], DIA[orden_t], PLAT[orden_t]
n_bruma = 0
for k in range(V.shape[1]):
    ok = np.where(np.isfinite(V[:, k]))[0]
    if len(ok) < 3:
        continue
    v, d, az = V[ok, k], DIA_O[ok], AZ[ok, k]
    prev_v, next_v = np.r_[np.nan, v[:-1]], np.r_[v[1:], np.nan]
    prev_ok = np.r_[False, (d[1:] - d[:-1]) <= 30]
    next_ok = np.r_[(d[1:] - d[:-1]) <= 30, False]
    caida = prev_ok & next_ok & (v < prev_v - 0.10) & (v < next_v - 0.10)
    azul = prev_ok & next_ok & (az >= 0.075) & (prev_v > 0.5) & (next_v > 0.5)
    malo = ok[caida | azul]
    V[malo, k] = np.nan
    W[malo, k] = np.nan
    n_bruma += len(malo)
validos = int(np.isfinite(V).sum())
log(f"bruma: {n_bruma} pixel·fecha descartados de {validos + n_bruma} ({100 * n_bruma / max(validos + n_bruma, 1):.1f} %)")

# %% [markdown]
# ## Paso 2b. Fechas de siembra por la curva del NDVI (y prueba con las 3 siembras reales de Nataima)

# %%
def siembras(d, v):
    """Siembra = última fecha con NDVI ≤ 0,35 antes de la primera subida a ≥ 0,65 dentro de 20–90 días."""
    out, k = [], 0
    while k < len(v):
        if v[k] <= 0.35:
            j = k + 1
            while j < len(v) and d[j] - d[k] <= 90 and v[j] < 0.65:
                if v[j] <= 0.35:
                    k = j
                j += 1
            if j < len(v) and v[j] >= 0.65 and 20 <= d[j] - d[k] <= 90:
                out.append(d[k])
                k = j
                while k < len(v) and v[k] > 0.35:
                    k += 1
                continue
        k += 1
    return np.array(out, dtype=int)


SIEM = {}
for k in range(V.shape[1]):
    ok = np.isfinite(V[:, k])
    SIEM[k] = siembras(DIA_O[ok], V[ok, k])
col_lote = [int(np.where(SEL == p)[0][0]) for p in LOTE]
err = []
for t, f in SIEMBRAS_NATAIMA.items():
    real = (pd.Timestamp(f) - BASE).days
    for k in col_lote:
        s = SIEM[k]
        cerca = s[np.abs(s - real) <= 60]
        err.append({"temporada": t, "pixel": int(SEL[k]), "detectada": bool(len(cerca)),
                    "error_dias": float(cerca[np.argmin(np.abs(cerca - real))] - real) if len(cerca) else np.nan})
E_S = pd.DataFrame(err)
guardar(E_S, "paso2_siembras_nataima.csv")
log("siembras de Nataima detectadas: " + "; ".join(
    f"GS{t}: {g.detectada.mean() * 100:.0f} % de pixeles, error mediano {g.error_dias.median():+.0f} d"
    for t, g in E_S.groupby("temporada")))


def das_de(k, dias):
    s = SIEM[k]
    if len(s) == 0:
        return np.full(len(dias), np.nan)
    i = np.searchsorted(s, dias, side="right") - 1
    out = np.where(i >= 0, dias - s[np.clip(i, 0, None)], np.nan).astype(float)
    out[(out > 150) | (out < 0)] = np.nan
    return out


def etapa_de(das):
    return np.select([np.isnan(das), das <= 45, das <= 82, das <= 130], [0, 1, 2, 3], 0)

# %% [markdown]
# ## Paso 2c (antes de construir ejemplos). Prithvi: resumen por bloque y relleno de huecos frente a interpolación

# %%
PRITHVI_OK = False
PCS = None
try:
    import importlib.util
    from huggingface_hub import hf_hub_download

    REPO_P, REV_P = "ibm-nasa-geospatial/Prithvi-EO-2.0-300M-TL", "63adbd39c271da4c42f447e69b1a7c91a338cdc9"
    if os.environ.get("SIRA_PRITHVI_FALSO"):  # QA local: misma arquitectura en miniatura, sin pesos
        code_p = os.environ["SIRA_PRITHVI_FALSO"]
        cfg = {"img_size": 224, "patch_size": [1, 16, 16], "in_chans": 6, "embed_dim": 64, "depth": 2, "num_heads": 4,
               "decoder_embed_dim": 32, "decoder_depth": 1, "decoder_num_heads": 4, "mlp_ratio": 4.0,
               "coords_encoding": ["time", "location"], "coords_scale_learn": True, "mask_ratio": 0.75,
               "norm_pix_loss": False, "mean": [1087.0, 1342.0, 1433.0, 2734.0, 1958.0, 1363.0],
               "std": [2248.0, 2179.0, 2178.0, 1850.0, 1242.0, 1049.0]}
        pesos_p = None
    else:
        cfg_p = hf_hub_download(REPO_P, "config.json", revision=REV_P)
        code_p = hf_hub_download(REPO_P, "prithvi_mae.py", revision=REV_P)
        pesos_p = hf_hub_download(REPO_P, "Prithvi_EO_V2_300M_TL.pt", revision=REV_P)
        cfg = json.loads(Path(cfg_p).read_text())["pretrained_cfg"]
    spec = importlib.util.spec_from_file_location("prithvi_mae", code_p)
    pm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pm)
    kw = {k: cfg[k] for k in ("img_size", "patch_size", "in_chans", "embed_dim", "depth", "num_heads", "decoder_embed_dim",
                              "decoder_depth", "decoder_num_heads", "mlp_ratio", "coords_encoding", "coords_scale_learn",
                              "mask_ratio", "norm_pix_loss")}
    kw["num_frames"] = 3
    MAE = pm.PrithviMAE(**kw)
    if pesos_p:
        sd_ = torch.load(pesos_p, map_location="cpu", weights_only=True)
        for kk in [kk for kk in sd_ if "pos_embed" in kk]:
            del sd_[kk]
        faltan, sobran = MAE.load_state_dict(sd_, strict=False)
        assert all("pos_embed" in kk for kk in faltan) and not sobran, (faltan, sobran)
    MAE = MAE.to(DEV).eval()
    MEAN = np.array(cfg["mean"], np.float32)[:, None, None]
    STD = np.array(cfg["std"], np.float32)[:, None, None]
    hls = pd.read_csv(IN / "hls_fechas.csv", parse_dates=["fecha"])
    # una escena por fecha (S30 viene en 2 teselas y S30/L30 pueden coincidir): la más despejada
    hls = hls.sort_values(["fecha", "frac_despejado"], ascending=[True, False]).drop_duplicates("fecha").reset_index(drop=True)
    rej = json.load(open(IN / "hls_rejilla.json"))
    THX, THY = rej["transform"][2], rej["transform"][5]
    LADO, PATCH = rej["lado"], 16
    G14 = LADO // PATCH

    def cargar_hls(iid):
        zz = np.load(IN / "hls" / f"{iid}.npz")
        b, f = zz["b"], zz["f"]
        limpio = (f != 255) & ((f & 0b1110) == 0) & (b[0] != -9999)
        return b, limpio

    def normal(b):
        return np.where(b == -9999, 0.0001, (b.astype(np.float32) - MEAN) / STD)

    def coords(fechas):
        tc = torch.tensor([[[f.year, f.day_of_year] for f in fechas]], dtype=torch.float32, device=DEV)
        lc = torch.tensor([[4.1895, -74.9621]], dtype=torch.float32, device=DEV)
        return tc, lc

    # --- resumen (embedding) por bloque de 16 × 16 px (480 m) y fecha HLS; T = 1
    MAE.encoder.num_frames = 1
    EMB, EMB_OK = [], []
    for r in hls.itertuples():
        b, limpio = cargar_hls(r.id)
        x = torch.from_numpy(normal(b)[:, None][None]).to(DEV)
        with torch.no_grad():
            zt = MAE.encoder.forward_features(x, *coords([r.fecha]))[-1][0, 1:].float().cpu().numpy()  # (196, D)
        EMB.append(zt.astype(np.float16))
        EMB_OK.append(limpio.reshape(G14, PATCH, G14, PATCH).mean(axis=(1, 3)).ravel() >= 0.7)
    EMB, EMB_OK = np.stack(EMB), np.stack(EMB_OK)
    HLS_DIA = (hls.fecha - BASE).dt.days.to_numpy()
    from sklearn.decomposition import PCA
    ent = (hls.fecha < FOLDS[0][0]).to_numpy()
    Xp = EMB[ent][EMB_OK[ent]].astype(np.float32) if (EMB_OK[ent]).sum() > 20 else EMB[EMB_OK].astype(np.float32)
    PCA_P = PCA(n_components=10, random_state=SEM).fit(Xp)
    PCS = np.full(EMB.shape[:2] + (10,), np.nan, np.float32)
    for i in range(len(EMB)):
        PCS[i] = PCA_P.transform(EMB[i].astype(np.float32))
        PCS[i][~EMB_OK[i]] = np.nan
    guardar(pd.DataFrame({"componente": range(1, 11), "varianza_explicada": PCA_P.explained_variance_ratio_}),
            "paso6_prithvi_pca.csv")
    PRITHVI_OK = True
    log(f"Prithvi: {len(EMB)} fechas HLS con resumen; PCA 10 componentes = {PCA_P.explained_variance_ratio_.sum():.2f} de la varianza "
        f"(PCA ajustado solo con fechas antes de {FOLDS[0][0].date()})")
except Exception as e:  # noqa: BLE001
    log(f"AVISO Prithvi no disponible: {e}\n{traceback.format_exc()[-1500:]}")

# %%
# --- relleno de huecos con nubes falsas (informe §8): Prithvi (enmascarado por bloques) frente a interpolación
#     temporal, último valor despejado y vecino espacial más cercano. Se mide sobre fechas HLS despejadas.
try:
    if not PRITHVI_OK:
        raise RuntimeError("sin Prithvi")
    from scipy import ndimage
    MAE.encoder.num_frames = 3
    if hasattr(MAE.decoder, "num_frames"):
        MAE.decoder.num_frames = 3
    orig_mask = MAE.encoder.random_masking
    fr = [cargar_hls(i) for i in hls.id]
    claro = np.array([l.mean() for _, l in fr])
    cand = [i for i in range(2, len(hls)) if claro[i] >= 0.9 and HLS_DIA[i] - HLS_DIA[i - 2] <= 90]
    cand = cand[:: max(1, len(cand) // (3 if QA else 60))]
    filas_g = []
    for i in cand:
        b_t, l_t = fr[i]
        for rep in range(3):
            # nube falsa: manchas de bloques 16 × 16 (≈ 40 % de los bloques)
            g = np.zeros((G14, G14), bool)
            while g.mean() < 0.4:
                c0, f0 = rng.integers(0, G14, 2)
                rad = rng.integers(1, 4)
                g[max(0, f0 - rad):f0 + rad + 1, max(0, c0 - rad):c0 + rad + 1] = True
            nube = np.kron(g, np.ones((PATCH, PATCH), bool))
            evalm = nube & l_t
            # Prithvi: 3 cuadros (2 anteriores + el objetivo); se ocultan los bloques con nube falsa del objetivo y
            # los bloques > 30 % nublados de los anteriores
            ocultos = [((1 - fr[i - 2][1].reshape(G14, PATCH, G14, PATCH).mean(axis=(1, 3))) > 0.3).ravel(),
                       ((1 - fr[i - 1][1].reshape(G14, PATCH, G14, PATCH).mean(axis=(1, 3))) > 0.3).ravel(), g.ravel()]
            oc = np.concatenate(ocultos)
            ruido = torch.tensor(np.where(oc, 1.0, 0.0) + rng.random(oc.size) * 1e-3, dtype=torch.float32, device=DEV)[None]
            n_keep = int((~oc).sum())
            ratio = 1 - (n_keep + 0.5) / oc.size
            MAE.encoder.random_masking = lambda s, r, noise=None, _n=ruido: orig_mask(s, r, noise=_n)
            x = np.stack([normal(fr[i - 2][0]), normal(fr[i - 1][0]), normal(b_t)], axis=1)[None]
            with torch.no_grad():
                _, pred, _ = MAE(torch.from_numpy(x).to(DEV), *coords(list(hls.fecha[[i - 2, i - 1, i]])), mask_ratio=ratio)
                rec = MAE.unpatchify(pred, image_size=(LADO, LADO)).float().cpu().numpy()[0][:, 2]  # C, H, W (cuadro 3)
            rec = rec * STD + MEAN
            MAE.encoder.random_masking = orig_mask

            def ind(b):
                b = b.astype(np.float32) * 1e-4
                return (b[3] - b[2]) / (b[3] + b[2] + 1e-6), (b[3] - b[4]) / (b[3] + b[4] + 1e-6)

            v_true, w_true = ind(b_t)
            v_pri, w_pri = ind(rec)
            # interpolación temporal: anterior y siguiente fecha HLS despejada en ese pixel (pasado y futuro)
            def vecino_t(paso):
                vv, ww = np.full(nube.shape, np.nan, np.float32), np.full(nube.shape, np.nan, np.float32)
                dd = np.full(nube.shape, np.nan, np.float32)
                j = i + paso
                falta = evalm.copy()
                while 0 <= j < len(hls) and abs(HLS_DIA[j] - HLS_DIA[i]) <= 60 and falta.any():
                    bj, lj = fr[j]
                    vj, wj = ind(bj)
                    m = falta & lj
                    vv[m], ww[m], dd[m] = vj[m], wj[m], abs(HLS_DIA[j] - HLS_DIA[i])
                    falta &= ~lj
                    j += paso
                return vv, ww, dd
            vp, wp, dp = vecino_t(-1)
            vn, wn, dn = vecino_t(+1)
            wgt = dn / (dp + dn)
            v_int = np.where(np.isfinite(vp) & np.isfinite(vn), wgt * vp + (1 - wgt) * vn, np.where(np.isfinite(vp), vp, vn))
            w_int = np.where(np.isfinite(wp) & np.isfinite(wn), wgt * wp + (1 - wgt) * wn, np.where(np.isfinite(wp), wp, wn))
            # vecino espacial más cercano fuera de la nube (mismo día)
            ok_sp = l_t & ~nube
            _, (ri, ci) = ndimage.distance_transform_edt(~ok_sp, return_indices=True)
            v_sp, w_sp = v_true[ri, ci], w_true[ri, ci]
            for nom, vv, ww in (("Prithvi", v_pri, w_pri), ("interpolación temporal", v_int, w_int),
                                ("último despejado", vp, wp), ("vecino espacial", v_sp, w_sp)):
                m = evalm & np.isfinite(vv)
                filas_g.append({"fecha": str(hls.fecha[i].date()), "rep": rep, "metodo": nom, "n": int(m.sum()),
                                "cobertura": float(m.sum() / max(evalm.sum(), 1)),
                                "rmse_ndvi": float(np.sqrt(np.mean((vv[m] - v_true[m]) ** 2))) if m.any() else np.nan,
                                "rmse_ndmi": float(np.sqrt(np.mean((ww[m] - w_true[m]) ** 2))) if m.any() else np.nan})
    GF = pd.DataFrame(filas_g)
    guardar(GF, "paso2_relleno_nubes_falsas.csv")
    res_g = GF.groupby("metodo")[["rmse_ndvi", "rmse_ndmi", "cobertura"]].mean()
    log("relleno con nubes falsas (RMSE medio NDVI / NDMI · cobertura):\n" + res_g.round(3).to_string())
    del fr
except Exception as e:  # noqa: BLE001
    log(f"AVISO relleno de huecos no evaluado: {e}\n{traceback.format_exc()[-1500:]}")

# %% [markdown]
# ## Paso 2d. Ejemplos: (fecha despejada t, otra fecha despejada t+Δ, 1 ≤ Δ ≤ 30)

# %%
SENS = {"S2": 0, "landsat-7": 1, "landsat-8": 2, "landsat-9": 2}
COD = np.array([SENS.get(p, 1) for p in PLAT_O])
SEQ = 8


def oni_de(dias):
    f = BASE + pd.to_timedelta(dias, unit="D")
    return np.array([ONI.get((a, m), np.nan) for a, m in zip(f.year, f.month)], np.float32)


def prithvi_de(fil_col, dias):
    """10 componentes del bloque del pixel en la fecha HLS más reciente ≤ t (≤ 16 días)."""
    out = np.full((len(dias), 10), np.nan, np.float32)
    if PCS is None:
        return out
    fil, col = fil_col
    xm, ym = X0 + (col + 0.5) * 10, Y0 - (fil + 0.5) * 10
    bc, bf = int((xm - THX) // 30) // PATCH, int((THY - ym) // 30) // PATCH
    if not (0 <= bc < G14 and 0 <= bf < G14):
        return out
    k = np.searchsorted(HLS_DIA, dias, side="right") - 1
    ok = (k >= 0) & (dias - HLS_DIA[np.clip(k, 0, None)] <= 16)
    out[ok] = PCS[k[ok], bf * G14 + bc]
    return out


def ejemplos_pixel(k, objetivos_todos=False, delta_fijo=None):
    """Pares de un pixel. objetivos_todos=True: para cada origen despejado, Δ = 1..30 sin exigir objetivo despejado
    (para pronosticar hacia adelante)."""
    ok = np.isfinite(V[:, k]) & np.isfinite(W[:, k])
    d, v, w, c = DIA_O[ok], V[ok, k], W[ok, k], COD[ok]
    if len(d) < 2:
        return None
    _, u = np.unique(d, return_index=True)  # una obs por día (S2 primero por el orden estable)
    d, v, w, c = d[u], v[u], w[u], c[u]
    n = len(d)
    if objetivos_todos:
        ii = np.repeat(np.arange(n), 30)
        d1 = d[ii] + np.tile(np.arange(1, 31), n)
        jj = np.full(len(ii), -1)
    else:
        cnt = np.searchsorted(d, d + 30, side="right") - np.arange(n) - 1
        ii = np.repeat(np.arange(n), cnt)
        jj = ii + (np.arange(len(ii)) - np.repeat(np.cumsum(cnt) - cnt, cnt)) + 1
        d1 = d[jj]
    d0 = d[ii]
    l1 = ii - 1
    l2 = ii - 2
    a1 = np.where(l1 >= 0, d0 - d[np.clip(l1, 0, None)], np.nan)
    a2 = np.where(l2 >= 0, d0 - d[np.clip(l2, 0, None)], np.nan)
    a1[a1 > 60] = np.nan
    a2[a2 > 90] = np.nan
    das0 = das_de(k, d0)
    das1 = das0 + (d1 - d0)
    das1[das1 > 150] = np.nan
    doy0 = (BASE + pd.to_timedelta(d0, unit="D")).day_of_year.to_numpy()
    doy1 = (BASE + pd.to_timedelta(d1, unit="D")).day_of_year.to_numpy()
    f = {"pixel": np.full(len(ii), SEL[k]), "kcol": np.full(len(ii), k), "d0": d0, "d1": d1, "delta": d1 - d0,
         "ndvi0": v[ii], "ndmi0": w[ii],
         "ndvi_l1": np.where(np.isfinite(a1), v[np.clip(l1, 0, None)], np.nan), "edad_l1": a1,
         "ndmi_l1": np.where(np.isfinite(a1), w[np.clip(l1, 0, None)], np.nan),
         "ndvi_l2": np.where(np.isfinite(a2), v[np.clip(l2, 0, None)], np.nan), "edad_l2": a2,
         "sensor0": c[ii], "sensor1": np.where(jj >= 0, c[np.clip(jj, 0, None)], 0),
         "das0": das0, "das1": das1, "etapa0": etapa_de(das0), "etapa1": etapa_de(das1),
         "doy_sin": np.sin(2 * np.pi * doy0 / 365.25), "doy_cos": np.cos(2 * np.pi * doy0 / 365.25),
         "doy1_sin": np.sin(2 * np.pi * doy1 / 365.25), "doy1_cos": np.cos(2 * np.pi * doy1 / 365.25),
         "lluvia_h": suma(CLL, d0, d1), "eto_h": suma(CET, d0, d1),
         "lluvia_30": suma(CLL, d0 - 30, d0), "eto_30": suma(CET, d0 - 30, d0),
         "lluvia_30_1": suma(CLL, d1 - 30, d1), "eto_30_1": suma(CET, d1 - 30, d1),
         "oni0": oni_de(d0),
         "y_ndvi": np.where(jj >= 0, v[np.clip(jj, 0, None)], np.nan),
         "y_ndmi": np.where(jj >= 0, w[np.clip(jj, 0, None)], np.nan)}
    f["pend_ndvi"] = (f["ndvi0"] - f["ndvi_l1"]) / f["edad_l1"]
    f["pend_ndmi"] = (f["ndmi0"] - f["ndmi_l1"]) / f["edad_l1"]
    fil, col = divmod(int(SEL[k]), NG)
    P = prithvi_de((fil, col), d0)
    for q in range(10):
        f[f"pc{q + 1}"] = P[:, q]
    # secuencia para la GRU: últimas SEQ observaciones hasta t (valor NDVI, NDMI, edad en días, sensor)
    idx = ii[:, None] - np.arange(SEQ)[None, ::-1]
    okq = idx >= 0
    idc = np.clip(idx, 0, None)
    seq = np.stack([np.where(okq, v[idc], 0), np.where(okq, w[idc], 0),
                    np.where(okq, (d0[:, None] - d[idc]) / 30.0, -1), np.where(okq, c[idc], -1)], axis=-1).astype(np.float32)
    return pd.DataFrame(f), seq


partes, seqs = [], []
for k in range(len(SEL)):
    r = ejemplos_pixel(k)
    if r is not None:
        partes.append(r[0])
        seqs.append(r[1])
E = pd.concat(partes, ignore_index=True)
SEQS = np.concatenate(seqs)
del partes, seqs
E["f0"] = BASE + pd.to_timedelta(E.d0, unit="D")
E["f1"] = BASE + pd.to_timedelta(E.d1, unit="D")
E["orden_pix"] = E.pixel.map(ORDEN).fillna(10 ** 9).astype(int)
E["es_lote"] = E.pixel.isin(LOTE)
log(f"ejemplos: {len(E):,} (pixeles {E.pixel.nunique()}) · Δ mediana {E.delta.median():.0f} d · con Prithvi "
    f"{E.pc1.notna().mean() * 100:.0f} % · memoria ≈ {E.memory_usage().sum() / 1e9:.1f} GB")

# %% [markdown]
# ## Paso 1. Exploración

# %%
try:
    meta_o = meta.iloc[orden_t].reset_index(drop=True)
    meta_o["validos_muestra"] = np.isfinite(V).mean(1)
    meta_o["anio"], meta_o["mes"] = meta_o.fecha.dt.year, meta_o.fecha.dt.month
    guardar(meta_o.groupby(["anio", "plataforma"]).agg(fechas=("id", "size"), frac_despejado=("validos_muestra", "mean"))
            .reset_index(), "paso1_fechas_por_anio_sensor.csv")
    guardar(meta_o.groupby("mes").agg(fechas=("id", "size"), frac_despejado=("validos_muestra", "mean")).reset_index(),
            "paso1_despejado_por_mes.csv")
    cur = E[E.das0.notna()].assign(das_b=lambda x: (x.das0 // 10) * 10).groupby("das_b")[["ndvi0", "ndmi0"]] \
        .agg(["mean", "std", "size"]).reset_index()
    cur.columns = ["_".join(c).strip("_") for c in cur.columns]
    guardar(cur, "paso1_curva_por_dias_desde_siembra.csv")
    cor = E[["y_ndvi", "y_ndmi", "lluvia_h", "eto_h", "lluvia_30", "eto_30", "delta"]].assign(
        dndvi=E.y_ndvi - E.ndvi0, dndmi=E.y_ndmi - E.ndmi0).corr(method="spearman")[["dndvi", "dndmi"]]
    cor.to_csv(OUT / "paso1_correlacion_cambio_vs_clima.csv")
    log("exploración lista; correlación (Spearman) del cambio de NDVI con lluvia_h "
        f"{cor.loc['lluvia_h', 'dndvi']:+.2f}, eto_h {cor.loc['eto_h', 'dndvi']:+.2f}")
except Exception as e:  # noqa: BLE001
    log(f"AVISO exploración: {e}")

# %% [markdown]
# ## Paso 3. Partición cronológica (ventana deslizante) y paso 4. métricas

# %%
def filas_fold(a, b, solo_eval=False):
    tr = np.where((E.f1 < a) & (E.f0 >= a - pd.DateOffset(years=ANIOS)) & ~E.es_lote)[0]
    va = np.where((E.f0 >= a) & (E.f1 < b) & ~E.es_lote & (E.orden_pix < N_PIX_EVAL))[0]
    return tr, va


PART = [filas_fold(a, b) for a, b in FOLDS]
VA_ALL = np.concatenate([v for _, v in PART])
guardar(pd.DataFrame([{"particion": i + 1, "entrena_desde": str((a - pd.DateOffset(years=ANIOS)).date()),
                       "valida": f"{a.date()} → {b.date()}", "filas_entrena": len(tr), "filas_valida": len(va)}
                      for i, ((a, b), (tr, va)) in enumerate(zip(FOLDS, PART))] +
                     [{"particion": "prueba sellada", "entrena_desde": str((SELLO - pd.DateOffset(years=ANIOS)).date()),
                       "valida": f"{SELLO.date()} → {FIN_TEST.date()}", "filas_entrena": int(((E.f1 < SELLO) & (E.f0 >= SELLO - pd.DateOffset(years=ANIOS))).sum()),
                       "filas_valida": int(((E.f0 >= SELLO) & (E.f1 <= FIN_TEST)).sum())}]), "paso3_particiones.csv")
BIN_E = np.select([E.delta.between(a, b) for a, b, _ in BINS], range(len(BINS)), -1)
N_BOOT = 200 if QA else 1000


def bloques(idx):
    """Bootstrap de bloques: partición × 10 días del origen (los pixeles de un mismo día van juntos)."""
    fol = np.select([(E.f0.iloc[idx] >= a) & (E.f0.iloc[idx] < b) for a, b in FOLDS + [(SELLO, FIN_TEST + pd.Timedelta(days=1))]],
                    range(len(FOLDS) + 1), 9)
    blo = fol * 100000 + (E.d0.to_numpy()[idx] // 10)
    ub, bi = np.unique(blo, return_inverse=True)
    Cm = np.stack([np.bincount(rng.integers(0, len(ub), len(ub)), minlength=len(ub)) for _ in range(N_BOOT)]).astype(np.float32)
    return bi, len(ub), Cm


def skill_medio(e2m, e2p, idx, B=None):
    """Skill medio frente a la persistencia en los 5 horizontes: media de (1 − RMSE_modelo / RMSE_persistencia)."""
    bins = BIN_E[idx]
    s = []
    for q in range(len(BINS)):
        m = bins == q
        if m.sum() == 0:
            continue
        if B is None:
            s.append(1 - np.sqrt(e2m[m].mean()) / np.sqrt(e2p[m].mean()))
        else:
            bi, nb, Cm = B
            sm = Cm @ np.bincount(bi[m], weights=e2m[m], minlength=nb)
            sp = Cm @ np.bincount(bi[m], weights=e2p[m], minlength=nb)
            s.append(1 - np.sqrt(sm / np.maximum(sp, 1e-12)))
    return np.mean(s, axis=0)


def ic(x):
    return f"{np.nanquantile(x, .025):+.4f} a {np.nanquantile(x, .975):+.4f}"


BV = bloques(VA_ALL)
json.dump({"metrica_principal": "skill medio frente a persistencia en los horizontes 1, 3, 7, 15 y 30 días (bins 1–2, 3–5, 6–10, 11–20, 21–30)",
           "otras": ["RMSE", "MAE", "sesgo", "skill frente a climatología (curva por días desde siembra; si no hay siembra, media del pixel por mes)",
                     "cobertura del rango de los 5 miembros de GEOS-S2S", "CRPS de conjunto"],
           "ic": "bootstrap de bloques (partición × 10 días del origen), percentiles 2,5–97,5",
           "regla_variables": "entra un grupo solo si el IC 95 % de su mejora en skill medio excluye el 0",
           "regla_modelo": "mayor skill medio; empate (IC 95 % de la diferencia incluye el 0) → el más simple en el orden "
                           "ARIMAX, Random Forest, Gradient Boosting, XGBoost, GRU"},
          open(OUT / "paso4_metricas.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)

# %% [markdown]
# ## Paso 5 y 6. Grupos de variables, curva de aprendizaje y selección (XGBoost en GPU)

# %%
from xgboost import XGBRegressor

GRUPOS = {"base": ["ndvi0", "ndmi0", "delta", "sensor0", "sensor1"],
          "rezagos": ["ndvi_l1", "edad_l1", "ndmi_l1", "ndvi_l2", "edad_l2", "pend_ndvi", "pend_ndmi"],
          "fenologia": ["das0", "das1", "etapa0", "etapa1", "doy_sin", "doy_cos", "doy1_sin", "doy1_cos"],
          "lluvia": ["lluvia_h", "lluvia_30"], "eto": ["eto_h", "eto_30"], "oni": ["oni0"],
          "prithvi": [f"pc{q}" for q in range(1, 11)]}
if not PRITHVI_OK:
    GRUPOS.pop("prithvi")
TODAS = [c for g in GRUPOS.values() for c in g]


def xgb(**kw):
    p = dict(n_estimators=300, max_depth=6, learning_rate=0.1, subsample=0.8, colsample_bytree=0.8, min_child_weight=5,
             tree_method="hist", device=DEV, random_state=SEM, verbosity=0, n_jobs=NUC)
    p.update(kw)
    return XGBRegressor(**p)


def pred_val(fabrica, cols, y, filtro_tr=None):
    out = np.empty(len(VA_ALL), np.float32)
    pos = 0
    for tr, va in PART:
        if filtro_tr is not None:
            tr = tr[filtro_tr(tr)]
        m = fabrica().fit(E[cols].to_numpy(np.float32)[tr], E[y].to_numpy()[tr])
        out[pos:pos + len(va)] = m.predict(E[cols].to_numpy(np.float32)[va])
        pos += len(va)
    return out


Y = {i: E[f"y_{i}"].to_numpy()[VA_ALL] for i in ("ndvi", "ndmi")}
E2P = {i: (Y[i] - E[f"{i}0"].to_numpy()[VA_ALL]) ** 2 for i in ("ndvi", "ndmi")}

# curva de aprendizaje: cuántos pixeles de entrenamiento hacen falta (misma regla: el menor no peor que el mejor)
n_tot = min(N_PIX_MAX, len(perm))
TAM = sorted({max(10, n_tot // 8), max(10, n_tot // 4), max(10, n_tot // 2), n_tot})
curva, e2c = [], {}
for i in ("ndvi", "ndmi"):
    for n in TAM:
        p = pred_val(lambda: xgb(), TODAS, f"y_{i}", filtro_tr=lambda tr, n=n: E.orden_pix.to_numpy()[tr] < n)
        e2c[(i, n)] = (Y[i] - p) ** 2
        curva.append({"indice": i, "pixeles": n, "skill_medio": skill_medio(e2c[(i, n)], E2P[i], VA_ALL)})
CU = pd.DataFrame(curva)
N_ELEG = {}
for i in ("ndvi", "ndmi"):
    c = CU[CU.indice == i]
    nb = int(c.loc[c.skill_medio.idxmax(), "pixeles"])
    sb = skill_medio(e2c[(i, nb)], E2P[i], VA_ALL, BV)
    for n in TAM:
        dd = skill_medio(e2c[(i, n)], E2P[i], VA_ALL, BV) - sb
        CU.loc[(CU.indice == i) & (CU.pixeles == n), "dif_vs_mejor_IC95"] = ic(dd)
        CU.loc[(CU.indice == i) & (CU.pixeles == n), "peor_signif"] = bool(np.quantile(dd, .975) < 0)
    N_ELEG[i] = int(CU[(CU.indice == i) & ~CU.peor_signif.astype(bool)].pixeles.min())
N_PIX = max(N_ELEG.values())
guardar(CU, "paso6_curva_aprendizaje.csv")
log(f"curva de aprendizaje: {N_ELEG} → se entrena con {N_PIX} pixeles")
FILTRO = lambda tr: E.orden_pix.to_numpy()[tr] < N_PIX  # noqa: E731
for q, (tr, va) in enumerate(PART):
    PART[q] = (tr[FILTRO(tr)], va)

# selección hacia adelante por grupos
SELEC, VARS = [], {}
for i in ("ndvi", "ndmi"):
    act = ["base"]
    p = pred_val(lambda: xgb(), GRUPOS["base"], f"y_{i}")
    e2a = (Y[i] - p) ** 2
    SELEC.append({"indice": i, "ronda": 0, "grupo": "base", "skill_medio": skill_medio(e2a, E2P[i], VA_ALL), "entra": True})
    ronda = 1
    while True:
        mejor = None
        for g in [g for g in GRUPOS if g not in act]:
            cols = [c for gg in act + [g] for c in GRUPOS[gg]]
            pg = pred_val(lambda: xgb(), cols, f"y_{i}")
            e2g = (Y[i] - pg) ** 2
            gan = skill_medio(e2g, E2P[i], VA_ALL, BV) - skill_medio(e2a, E2P[i], VA_ALL, BV)
            fila = {"indice": i, "ronda": ronda, "grupo": g, "skill_medio": skill_medio(e2g, E2P[i], VA_ALL),
                    "mejora": float(np.mean(gan)), "mejora_IC95": ic(gan), "entra": False}
            SELEC.append(fila)
            if np.quantile(gan, .025) > 0 and (mejor is None or fila["mejora"] > mejor[1]["mejora"]):
                mejor = (g, fila, e2g)
        if mejor is None or queda() < 0.55 * PRESUPUESTO_S:
            break
        act.append(mejor[0])
        mejor[1]["entra"] = True
        e2a = mejor[2]
        ronda += 1
    VARS[i] = [c for g in act for c in GRUPOS[g]]
    log(f"variables {i}: grupos {act}")
guardar(pd.DataFrame(SELEC), "paso6_seleccion_variables.csv")

# %% [markdown]
# ## Paso 7. Candidatos: ARIMAX, Random Forest, Gradient Boosting, XGBoost y GRU (+ persistencia y climatología)

# %%
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.preprocessing import StandardScaler


def climatologia(tr, idx, i):
    """Curva típica por días desde siembra (bins de 5 d) del entrenamiento; si no hay siembra, media del pixel por mes."""
    T = E.iloc[tr]
    yy = T[f"y_{i}"]
    cur = yy.groupby((T.das1 // 5)).mean()
    pm = yy.groupby([T.pixel, T.f1.dt.month]).mean()
    mm = yy.groupby(T.f1.dt.month).mean()
    Q = E.iloc[idx]
    a = (Q.das1 // 5).map(cur).to_numpy()
    b = pd.Series(list(zip(Q.pixel, Q.f1.dt.month))).map(pm).to_numpy(dtype=float)
    c = Q.f1.dt.month.map(mm).to_numpy()
    return np.where(np.isfinite(a), a, np.where(np.isfinite(b), b, c))


class ARIMAXPix:
    """Regresión con errores AR(1) (statsmodels SARIMAX) ajustada sobre series diarias apiladas de una muestra de pixeles.
    Pronóstico desde un origen observado y(t): ŷ(t+Δ) = x(t+Δ)β + μ + φ^Δ·(y(t) − x(t)β − μ), μ = c/(1 − φ)."""

    def __init__(self, i, exog, n_series):
        self.i, self.exog, self.n_series = i, exog, n_series

    def _x(self, tabla, sufijo):
        cols = {"doy_sin": f"doy{sufijo}_sin" if sufijo else "doy_sin", "doy_cos": f"doy{sufijo}_cos" if sufijo else "doy_cos"}
        et = tabla[f"etapa{sufijo or '0'}"].to_numpy()
        X = [tabla[cols["doy_sin"]].to_numpy(), tabla[cols["doy_cos"]].to_numpy(), et == 1, et == 2, et == 3]
        if "clima" in self.exog:
            X += [tabla[f"lluvia_30{'_1' if sufijo else ''}"].to_numpy(), tabla[f"eto_30{'_1' if sufijo else ''}"].to_numpy()]
        return np.column_stack(X).astype(float)

    def fit(self, tr):
        from statsmodels.tsa.statespace.sarimax import SARIMAX
        T = E.iloc[tr]
        pix = T.pixel.drop_duplicates().to_numpy()[: self.n_series]
        endog, exog = [], []
        for p in pix:
            k = int(np.where(SEL == p)[0][0])
            ok = np.isfinite(V[:, k]) & np.isfinite(W[:, k])
            d = DIA_O[ok]
            v = (V if self.i == "ndvi" else W)[ok, k]
            m = (d >= T.d0.min()) & (d < T.d1.max())
            d, v = d[m], v[m]
            if len(d) < 10:
                continue
            dias = np.arange(d.min(), d.max() + 1)
            y = np.full(len(dias), np.nan)
            y[d - d.min()] = v
            das = das_de(k, dias)
            doy = (BASE + pd.to_timedelta(dias, unit="D")).day_of_year.to_numpy()
            tab = pd.DataFrame({"doy_sin": np.sin(2 * np.pi * doy / 365.25), "doy_cos": np.cos(2 * np.pi * doy / 365.25),
                                "etapa0": etapa_de(das), "lluvia_30": suma(CLL, dias - 30, dias), "eto_30": suma(CET, dias - 30, dias)})
            endog += list(y) + [np.nan] * 30
            exog.append(self._x(tab, ""))
            exog.append(np.zeros((30, exog[-1].shape[1])))
        X = np.vstack(exog)
        self.mu_x, self.sd_x = X.mean(0), X.std(0) + 1e-9  # estandarización con el entrenamiento (paso 5)
        res = SARIMAX(np.array(endog), exog=(X - self.mu_x) / self.sd_x, order=(1, 0, 0), trend="c").fit(disp=False, maxiter=100)
        par = dict(zip(res.model.param_names, np.asarray(res.params)))
        self.beta = np.array([par[f"x{q + 1}"] for q in range(X.shape[1])])
        self.phi = float(np.clip(par.get("ar.L1", 0.0), -0.999, 0.999))
        self.mu = par.get("intercept", 0.0) / (1 - self.phi)
        return self

    def predict(self, idx, tabla=None):
        T = E.iloc[idx] if tabla is None else tabla
        x0 = (self._x(T, "") - self.mu_x) / self.sd_x @ self.beta
        x1 = (self._x(T, "1") - self.mu_x) / self.sd_x @ self.beta
        y0 = T[f"{self.i}0"].to_numpy()
        return x1 + self.mu + self.phi ** T.delta.to_numpy() * (y0 - x0 - self.mu)


class GRUNet(torch.nn.Module):
    def __init__(self, n_static, h):
        super().__init__()
        self.g = torch.nn.GRU(4, h, batch_first=True)
        self.o = torch.nn.Sequential(torch.nn.Linear(h + n_static, 64), torch.nn.ReLU(), torch.nn.Linear(64, 2))

    def forward(self, s, x):
        return self.o(torch.cat([self.g(s)[1][-1], x], 1))


class GRUPix:
    """Red recurrente pequeña: las últimas 8 observaciones del pixel + variables seleccionadas → NDVI y NDMI en t+Δ."""

    def __init__(self, cols, h, lr, epocas=6):
        self.cols, self.h, self.lr, self.epocas = cols, h, lr, epocas

    def fit(self, tr):
        X = E[self.cols].to_numpy(np.float32)[tr]
        self.sc = StandardScaler().fit(np.nan_to_num(X))  # paso 5: solo con el entrenamiento
        Xs = torch.tensor(np.nan_to_num(self.sc.transform(np.nan_to_num(X))), dtype=torch.float32)
        S = torch.tensor(SEQS[tr])
        Yt = torch.tensor(E[["y_ndvi", "y_ndmi"]].to_numpy(np.float32)[tr])
        torch.manual_seed(SEM)
        self.net = GRUNet(len(self.cols), self.h).to(DEV)
        op = torch.optim.Adam(self.net.parameters(), lr=self.lr)
        n = len(tr)
        for _ in range(self.epocas):
            orden = torch.randperm(n)
            for a in range(0, n, 2048):
                b = orden[a:a + 2048]
                op.zero_grad()
                l = torch.nn.functional.mse_loss(self.net(S[b].to(DEV), Xs[b].to(DEV)), Yt[b].to(DEV))
                l.backward()
                op.step()
        return self

    def predict(self, idx, tabla=None, seqs=None):
        X = (E.iloc[idx] if tabla is None else tabla)[self.cols].to_numpy(np.float32)
        Xs = torch.tensor(np.nan_to_num(self.sc.transform(np.nan_to_num(X))), dtype=torch.float32)
        S = torch.tensor(SEQS[idx] if seqs is None else seqs)
        out = []
        with torch.no_grad():
            for a in range(0, len(Xs), 8192):
                out.append(self.net(S[a:a + 8192].to(DEV), Xs[a:a + 8192].to(DEV)).cpu().numpy())
        return np.concatenate(out)


class Arbol:
    """Envoltura común para XGBoost, Random Forest y Gradient Boosting (sklearn, por histogramas)."""

    def __init__(self, fab, cols, i):
        self.fab, self.cols, self.i = fab, cols, i

    def fit(self, tr):
        self.m = self.fab().fit(E[self.cols].to_numpy(np.float32)[tr], E[f"y_{self.i}"].to_numpy()[tr])
        return self

    def predict(self, idx, tabla=None):
        return self.m.predict((E.iloc[idx] if tabla is None else tabla)[self.cols].to_numpy(np.float32))


def candidatos(i):
    c, v = [], VARS[i]
    for p in ("fenologia", "fenologia+clima"):
        c.append(("ARIMAX", {"p": 1, "exog": p}, lambda p=p: ARIMAXPix(i, p, 12 if QA else 60)))
    for leaf, mf in [(5, 0.33), (5, 0.6), (5, 1.0), (20, 0.33), (20, 0.6), (20, 1.0)]:
        c.append(("Random Forest", {"min_samples_leaf": leaf, "max_features": mf, "max_samples": 0.1, "n_estimators": 200},
                  lambda leaf=leaf, mf=mf: Arbol(lambda: RandomForestRegressor(n_estimators=40 if QA else 200, max_samples=0.1,
                                                                          min_samples_leaf=leaf, max_features=mf, n_jobs=NUC,
                                                                          random_state=SEM), v, i)))
    for lr, hojas in [(0.1, 31), (0.1, 63), (0.1, 127), (0.05, 31), (0.05, 63), (0.05, 127)]:
        c.append(("Gradient Boosting", {"learning_rate": lr, "max_leaf_nodes": hojas, "max_iter": 400, "implementacion": "sklearn HistGradientBoosting"},
                  lambda lr=lr, hojas=hojas: Arbol(lambda: HistGradientBoostingRegressor(learning_rate=lr, max_leaf_nodes=hojas,
                                                                                    max_iter=60 if QA else 400, early_stopping=False,
                                                                                    random_state=SEM), v, i)))
    for d, lr, mcw in [(4, .05, 5), (4, .1, 5), (6, .05, 5), (6, .1, 5), (8, .05, 5), (8, .1, 5), (6, .1, 20), (8, .05, 20)]:
        c.append(("XGBoost", {"max_depth": d, "learning_rate": lr, "min_child_weight": mcw, "n_estimators": 400},
                  lambda d=d, lr=lr, mcw=mcw: Arbol(lambda: xgb(n_estimators=60 if QA else 400, max_depth=d, learning_rate=lr,
                                                              min_child_weight=mcw), v, i)))
    return c


ORDEN_SIMPLE = ["ARIMAX", "Random Forest", "Gradient Boosting", "XGBoost", "GRU"]
COLS_GRU = sorted(set(VARS["ndvi"]) | set(VARS["ndmi"]))
GRU_CFG = [(16, 1e-3), (16, 3e-3), (32, 1e-3), (32, 3e-3)]
VAL, E2M, FABR = [], {}, {}
gru_pred = {}
for h, lr in GRU_CFG:  # la GRU da las dos salidas a la vez
    if queda() < 0.35 * PRESUPUESTO_S:
        log("AVISO tiempo: se omiten configuraciones de la GRU")
        break
    out = np.empty((len(VA_ALL), 2), np.float32)
    pos = 0
    for tr, va in PART:
        m = GRUPix(COLS_GRU, h, lr, epocas=2 if QA else 6).fit(tr)
        out[pos:pos + len(va)] = m.predict(va)
        pos += len(va)
    gru_pred[(h, lr)] = out
    log(f"GRU h={h} lr={lr} lista")
for i in ("ndvi", "ndmi"):
    e2p = E2P[i]
    filas_cand = []
    for fam, hp, fab in candidatos(i):
        if queda() < 0.27 * PRESUPUESTO_S and fam in ("Random Forest", "Gradient Boosting"):
            log(f"AVISO tiempo: se omite {fam} {hp}")
            continue
        try:
            out = np.empty(len(VA_ALL), np.float32)
            pos = 0
            for tr, va in PART:
                m = fab().fit(tr)
                out[pos:pos + len(va)] = m.predict(va)
                pos += len(va)
        except Exception as e:  # noqa: BLE001
            log(f"fallo {fam} {hp}: {e}")
            continue
        filas_cand.append((fam, hp, fab, (Y[i] - out) ** 2))
        log(f"{i} · {fam} {hp}: skill medio {skill_medio((Y[i] - out) ** 2, e2p, VA_ALL):.3f}")
    for (h, lr), out in gru_pred.items():
        filas_cand.append(("GRU", {"oculto": h, "lr": lr, "epocas": 6}, (lambda h=h, lr=lr: GRUPix(COLS_GRU, h, lr)),
                           (Y[i] - out[:, 0 if i == "ndvi" else 1]) ** 2))
    # líneas base
    clim = np.concatenate([climatologia(tr, va, i) for tr, va in PART])
    e2clim = (Y[i] - clim) ** 2
    VAL.append({"indice": i, "modelo": "persistencia", "hiperparametros": "", "skill_medio": 0.0,
                "rmse": float(np.sqrt(e2p.mean()))})
    VAL.append({"indice": i, "modelo": "climatología", "hiperparametros": "", "skill_medio": skill_medio(e2clim, e2p, VA_ALL),
                "rmse": float(np.sqrt(e2clim.mean()))})
    for fam, hp, fab, e2 in filas_cand:
        VAL.append({"indice": i, "modelo": fam, "hiperparametros": json.dumps(hp), "skill_medio": skill_medio(e2, e2p, VA_ALL),
                    "rmse": float(np.sqrt(e2.mean())), **{f"rmse_h{nom}": float(np.sqrt(e2[BIN_E[VA_ALL] == q].mean()))
                                                         for q, (_, _, nom) in enumerate(BINS)}})
    # mejor configuración por familia, luego familia ganadora con la regla de empate
    best_fam = {}
    for fam, hp, fab, e2 in filas_cand:
        s = skill_medio(e2, e2p, VA_ALL)
        if fam not in best_fam or s > best_fam[fam][0]:
            best_fam[fam] = (s, hp, fab, e2)
    top = max(best_fam, key=lambda f: best_fam[f][0])
    sb = skill_medio(best_fam[top][3], e2p, VA_ALL, BV)
    empatan = []
    for fam, (s, hp, fab, e2) in best_fam.items():
        dd = skill_medio(e2, e2p, VA_ALL, BV) - sb
        VAL.append({"indice": i, "modelo": f"[mejor de la familia] {fam}", "hiperparametros": json.dumps(hp), "skill_medio": s,
                    "dif_vs_mejor_IC95": ic(dd), "empata_con_el_mejor": bool(np.quantile(dd, .975) >= 0)})
        if np.quantile(dd, .975) >= 0:
            empatan.append(fam)
    gana = min(empatan, key=ORDEN_SIMPLE.index)
    FABR[i] = (gana, best_fam[gana][1], best_fam[gana][2])
    E2M[i] = best_fam[gana][3]
    log(f"ELEGIDO {i}: {gana} {best_fam[gana][1]} (skill medio {best_fam[gana][0]:.3f}; mejor absoluto {top}; empatan {empatan})")
guardar(pd.DataFrame(VAL), "paso7_validacion.csv")

# %% [markdown]
# ## Paso 8. Prueba sellada (una vez): clima observado y GEOS-S2S corregido

# %%
TR_F = np.where((E.f1 < SELLO) & (E.f0 >= SELLO - pd.DateOffset(years=ANIOS)) & ~E.es_lote & (E.orden_pix < N_PIX))[0]
TE = np.where((E.f0 >= SELLO) & (E.f1 <= FIN_TEST) & ~E.es_lote & (E.orden_pix < N_PIX_EVAL))[0]
BT = bloques(TE)
FINAL, PRED_T, PRUEBA = {}, {}, []


def metricas(y, p, base_p, base_c, idx, B):
    filas = []
    bins = BIN_E[idx]
    et = E.etapa1.to_numpy()[idx]
    for nom, m in [("todos", np.ones(len(idx), bool))] + [(f"h{n}", bins == q) for q, (_, _, n) in enumerate(BINS)] + \
            [(f"etapa {e}", et == k) for k, e in ((1, "vegetativa"), (2, "reproductiva"), (3, "maduración"), (0, "sin siembra"))]:
        if m.sum() < 5:
            continue
        e2, e2p_, e2c_ = (y - p) ** 2, (y - base_p) ** 2, (y - base_c) ** 2
        bi, nb, Cm = B
        r = lambda e: np.sqrt((Cm @ np.bincount(bi[m], weights=e[m], minlength=nb)) / np.maximum(Cm @ np.bincount(bi[m], minlength=nb), 1))  # noqa: E731
        rm, rp, rc = r(e2), r(e2p_), r(e2c_)
        filas.append({"grupo": nom, "n": int(m.sum()), "rmse": float(np.sqrt(e2[m].mean())), "rmse_IC95": ic(rm),
                      "mae": float(np.abs(y - p)[m].mean()), "sesgo": float((p - y)[m].mean()),
                      "rmse_persistencia": float(np.sqrt(e2p_[m].mean())), "rmse_climatologia": float(np.sqrt(e2c_[m].mean())),
                      "skill_vs_persistencia": float(1 - np.sqrt(e2[m].mean() / e2p_[m].mean())), "skill_vs_persistencia_IC95": ic(1 - rm / rp),
                      "skill_vs_climatologia": float(1 - np.sqrt(e2[m].mean() / e2c_[m].mean())), "skill_vs_climatologia_IC95": ic(1 - rm / rc)})
    return filas


for i in ("ndvi", "ndmi"):
    fam, hp, fab = FABR[i]
    m = fab().fit(TR_F)
    FINAL[i] = m
    p = m.predict(TE)
    if p.ndim == 2:
        p = p[:, 0 if i == "ndvi" else 1]
    PRED_T[i] = p
    y = E[f"y_{i}"].to_numpy()[TE]
    clim = climatologia(TR_F, TE, i)
    for f_ in metricas(y, p, E[f"{i}0"].to_numpy()[TE], clim, TE, BT):
        PRUEBA.append({"indice": i, "escenario": "clima observado (techo)", "modelo": fam, **f_})
    log(f"PRUEBA {i} ({fam}): " + ", ".join(f"{r['grupo']} skill {r['skill_vs_persistencia']:+.2f}" for r in PRUEBA
                                             if r["indice"] == i and r["grupo"].startswith("h")))
guardar(pd.DataFrame(PRUEBA), "paso8_prueba_clima_observado.csv")
E.iloc[TE][["pixel", "f0", "f1", "delta", "etapa1", "ndvi0", "ndmi0", "y_ndvi", "y_ndmi"]].assign(
    pred_ndvi=PRED_T["ndvi"], pred_ndmi=PRED_T["ndmi"]).to_csv(OUT / "paso8_predicciones_prueba.csv.gz", index=False)

# %%
# --- GEOS-S2S: corrección de sesgo (climatología de los hindcasts 2001–2020 frente a CHIRPS / POWER 2001–2020) y
#     ETo por Hargreaves calibrada contra FAO-56; nada de esto usa la prueba.
GEO_RES = []
try:
    def geos_csv(nombre):
        """GEOS-S2S: de la entrada si está; si no, del repositorio (rama modelo-humedad, se baja fuera de Kaggle)."""
        local = IN / nombre
        if local.exists() and local.stat().st_size > 100:
            return local
        destino = OUT / "repo" / nombre
        destino.parent.mkdir(parents=True, exist_ok=True)
        url = "https://raw.githubusercontent.com/GomSal/SIRA/modelo-humedad/data/processed/modelo_a/geos/" + nombre
        destino.write_bytes(urllib.request.urlopen(url, timeout=300).read())
        log(f"GEOS-S2S tomado del repositorio: {nombre}")
        return destino

    dr = pd.read_csv(geos_csv("geos_drift.csv"), parse_dates=["fecha"])
    dr["mm"] = dr.inicio_mmdd.astype(str).str.zfill(4)
    ini_m = dr.mm.str[:2].astype(int)
    ini_d = dr.mm.str[2:].astype(int)
    anio_ini = np.where(ini_m > dr.fecha.dt.month, dr.fecha.dt.year - 1, dr.fecha.dt.year)
    dr["inicio"] = pd.to_datetime(dict(year=anio_ini, month=ini_m, day=ini_d))
    dr["plazo"] = (dr.fecha - dr.inicio).dt.days
    dr["mes"] = dr.fecha.dt.month
    dr["v"] = np.where(dr.variable == "PRECTOTCORR", dr.valor * 86400, dr.valor - 273.15)
    obs = cl.loc["2001-01-01":"2020-12-31"].copy()
    obs["tmed"] = (obs.T2M_MAX + obs.T2M_MIN) / 2
    obs_ll = obs.lluvia.groupby(obs.index.month).mean()
    obs_t = obs.tmed.groupby(obs.index.month).mean()
    PLZ = [(0, 10), (11, 20), (21, 60)]
    dr["plz"] = np.select([dr.plazo.between(a, b) for a, b in PLZ], range(3), -1)
    corr = []
    for (var, mes, plz), g in dr[dr.plz >= 0].groupby(["variable", "mes", "plz"]):
        if var == "PRECTOTCORR":
            corr.append({"variable": "pr", "mes": mes, "plazo": plz, "factor": obs_ll[mes] / max(g.v.mean(), 1e-6)})
        else:
            corr.append({"variable": "t", "mes": mes, "plazo": plz, "factor": obs_t[mes] - g.v.mean()})
    CORR = pd.DataFrame(corr)
    guardar(CORR, "paso8_geos_correccion.csv")
    fac = {(r.variable, r.mes, r.plazo): r.factor for r in CORR.itertuples()}
    # Hargreaves calibrada contra FAO-56 (POWER) en los años de entrenamiento
    hg = lambda tx, tn, ra: 0.0023 * 0.408 * ra * ((tx + tn) / 2 + 17.8) * np.sqrt(np.clip(tx - tn, 0, None))  # noqa: E731
    cal_h = cl.loc[SELLO - pd.DateOffset(years=ANIOS):SELLO - pd.Timedelta(days=1)].dropna(subset=["T2M_MAX", "T2M_MIN", "Ra", "eto"])
    HB, HA = np.polyfit(hg(cal_h.T2M_MAX, cal_h.T2M_MIN, cal_h.Ra), cal_h.eto, 1)
    RA = cl.Ra.to_numpy()
    g = pd.read_csv(geos_csv("geos_nrt.csv"), parse_dates=["fecha"])
    g["inicio"] = pd.to_datetime(g.inicio.astype(str))
    g["plazo"] = (g.fecha - g.inicio).dt.days
    g["mes"] = g.fecha.dt.month
    g["plz"] = np.select([g.plazo.between(a, b) for a, b in PLZ], range(3), 2)
    gp = g.pivot_table(index=["inicio", "miembro", "fecha", "plazo", "mes", "plz"], columns="variable", values="valor").reset_index()
    for v_ in ("pr_sfc", "t2mmax", "t2mmin"):
        if v_ not in gp:
            gp[v_] = np.nan
    gp["pr_raw"] = gp.pr_sfc * 86400
    gp["tx_raw"], gp["tn_raw"] = gp.t2mmax - 273.15, gp.t2mmin - 273.15
    gp["pr"] = gp.pr_raw * [fac.get(("pr", m, p), 1.0) for m, p in zip(gp.mes, gp.plz)]
    dt_ = np.array([fac.get(("t", m, p), 0.0) for m, p in zip(gp.mes, gp.plz)])
    gp["tx"], gp["tn"] = gp.tx_raw + dt_, gp.tn_raw + dt_
    gp["dia"] = (gp.fecha - BASE).dt.days
    ra = RA[np.clip(gp.dia, 0, len(RA) - 1)]
    # ETo pronosticada. El rango diario de GEOS-S2S en la celda del lote es ~1,9 °C frente a ~10,6 °C de POWER
    # (la celda de 0,5° promedia cordillera), así que Hargreaves con Tmáx − Tmín sale muy baja. Se usa la parte de
    # Hargreaves que sí sirve, la temperatura media: ETo = ETo climatológica del día (FAO-56, POWER, años de
    # entrenamiento) × (Tmedia GEOS corregida + 17,8) / (Tmedia climatológica + 17,8).
    per = cl.loc[SELLO - pd.DateOffset(years=ANIOS):SELLO - pd.Timedelta(days=1)]
    CLIM_ETO = per.eto.groupby(per.index.day_of_year).mean()
    CLIM_LL = per.lluvia.groupby(per.index.day_of_year).mean()
    CLIM_T = ((per.T2M_MAX + per.T2M_MIN) / 2).groupby(per.index.day_of_year).mean()
    doy_g = gp.fecha.dt.day_of_year
    e_c, t_c = doy_g.map(CLIM_ETO).to_numpy(), doy_g.map(CLIM_T).to_numpy()
    gp["eto"] = e_c * ((gp.tx + gp.tn) / 2 + 17.8) / (t_c + 17.8)
    gp["eto_raw"] = e_c * ((gp.tx_raw + gp.tn_raw) / 2 + 17.8) / (t_c + 17.8)
    rango = (gp.tx_raw - gp.tn_raw).mean()
    log(f"GEOS-S2S: rango diario medio Tmáx − Tmín {rango:.1f} °C (POWER {(per.T2M_MAX - per.T2M_MIN).mean():.1f} °C); "
        f"Hargreaves calibrada a,b = {HA:.2f}, {HB:.2f} solo como referencia")
    # días sin la variable pronosticada (p. ej. los inicios de 2025 traen solo lluvia): climatología del
    # entrenamiento por día del año, marcada como relleno
    gp["eto_relleno"], gp["pr_relleno"] = gp.eto.isna(), gp.pr.isna()
    gp["eto"] = gp.eto.fillna(doy_g.map(CLIM_ETO))
    gp["pr"] = gp.pr.fillna(doy_g.map(CLIM_LL))
    log(f"GEOS-S2S: {gp.inicio.nunique()} inicios · días sin temperatura pronosticada (ETo por climatología) "
        f"{gp.eto_relleno.mean() * 100:.0f} % · sin lluvia {gp.pr_relleno.mean() * 100:.0f} %")
    # error del clima pronosticado (sumas de 10 días desde el día 5 del inicio), crudo frente a corregido
    ev = []
    for (ini, mb), q in gp.groupby(["inicio", "miembro"]):
        for a, b in ((5, 14), (15, 24), (25, 34)):
            w_ = q[q.plazo.between(a, b)]
            if len(w_) < 10:
                continue
            d0_, d1_ = int(w_.dia.min()) - 1, int(w_.dia.max())
            if pd.Timestamp(BASE + pd.Timedelta(days=d1_)) > FIN_TEST:
                continue
            ev.append({"inicio": ini, "miembro": mb, "plazo": f"{a}–{b} d", "periodo": "2025 (antes de la prueba)" if ini < SELLO else "2026 (prueba)",
                       "lluvia_obs": suma(CLL, d0_, d1_), "lluvia_cruda": w_.pr_raw.sum(), "lluvia_corregida": w_.pr.sum(),
                       "eto_obs": suma(CET, d0_, d1_),
                       "eto_cruda": w_.eto_raw.sum() if not w_.eto_relleno.any() else np.nan,
                       "eto_corregida": w_.eto.sum() if not w_.eto_relleno.any() else np.nan})
    EV = pd.DataFrame(ev)
    if len(EV):
        rs = EV.groupby(["periodo", "plazo"]).apply(lambda q: pd.Series({
            "n": len(q), "n_con_eto": int(q.eto_corregida.notna().sum()), "sesgo_lluvia_cruda": (q.lluvia_cruda - q.lluvia_obs).mean(), "sesgo_lluvia_corregida": (q.lluvia_corregida - q.lluvia_obs).mean(),
            "mae_lluvia_cruda": (q.lluvia_cruda - q.lluvia_obs).abs().mean(), "mae_lluvia_corregida": (q.lluvia_corregida - q.lluvia_obs).abs().mean(),
            "sesgo_eto_cruda": (q.eto_cruda - q.eto_obs).mean(), "sesgo_eto_corregida": (q.eto_corregida - q.eto_obs).mean(),
            "mae_eto_corregida": (q.eto_corregida - q.eto_obs).abs().mean()})).reset_index()
        guardar(rs, "paso8_geos_error_clima.csv")
        log("GEOS-S2S, error de sumas de 10 días:\n" + rs.round(2).to_string(index=False))
    # escenario realista: para cada ejemplo de prueba, el último inicio publicado (≥ 4 días antes del origen)
    inicios = np.sort(gp.inicio.unique())
    TEg = E.iloc[TE].copy()
    ini_ok = [inicios[inicios <= f - pd.Timedelta(days=4)] for f in TEg.f0]
    TEg["inicio"] = [x[-1] if len(x) else pd.NaT for x in ini_ok]
    TEg = TEg[TEg.inicio.notna() & ((TEg.f1 - TEg.inicio).dt.days <= 44)]
    if len(TEg):
        tabla_g = {(r.inicio, r.miembro, r.dia): (r.pr, r.eto) for r in gp.itertuples()}
        miembros = sorted(gp.miembro.unique())
        preds = {i: [] for i in ("ndvi", "ndmi")}
        for mb in miembros:
            T2 = TEg.copy()
            ll_h, et_h, ll_30, et_30 = [], [], [], []
            for r in T2.itertuples():
                dias = range(int(r.d0) + 1, int(r.d1) + 1)
                vals = [tabla_g.get((r.inicio, mb, d_)) or
                        (CLIM_LL.get((BASE + pd.Timedelta(days=d_)).day_of_year, 0.0),
                         CLIM_ETO.get((BASE + pd.Timedelta(days=d_)).day_of_year, ET.mean())) for d_ in dias]
                lh = np.nansum([x[0] for x in vals])
                eh = np.nansum([x[1] for x in vals])
                ll_h.append(lh)
                et_h.append(eh)
                # 30 días que terminan en t+Δ: parte observada hasta t + parte pronosticada
                ll_30.append(suma(CLL, int(r.d1) - 30, int(r.d0)) + lh)
                et_30.append(suma(CET, int(r.d1) - 30, int(r.d0)) + eh)
            T2["lluvia_h"], T2["eto_h"], T2["lluvia_30_1"], T2["eto_30_1"] = ll_h, et_h, ll_30, et_30
            for i in ("ndvi", "ndmi"):
                if FABR[i][0] == "GRU":
                    p = FINAL[i].predict(None, tabla=T2, seqs=SEQS[T2.index.to_numpy()])[:, 0 if i == "ndvi" else 1]
                else:
                    p = FINAL[i].predict(None, tabla=T2)
                preds[i].append(p)
        sel_te = np.searchsorted(TE, TEg.index.to_numpy())
        for i in ("ndvi", "ndmi"):
            P = np.stack(preds[i])  # miembros × filas
            y = TEg[f"y_{i}"].to_numpy()
            pm_ = P.mean(0)
            p_obs = PRED_T[i][sel_te]
            crps = np.abs(P - y).mean(0) - 0.5 * np.abs(P[:, None] - P[None]).mean((0, 1))
            cub = (y >= P.min(0)) & (y <= P.max(0))
            B2 = bloques(TEg.index.to_numpy())
            clim = climatologia(TR_F, TEg.index.to_numpy(), i)
            for f_ in metricas(y, pm_, TEg[f"{i}0"].to_numpy(), clim, TEg.index.to_numpy(), B2):
                GEO_RES.append({"indice": i, "escenario": "GEOS-S2S corregido (realista)", "modelo": FABR[i][0], **f_})
            for f_ in metricas(y, p_obs, TEg[f"{i}0"].to_numpy(), clim, TEg.index.to_numpy(), B2):
                GEO_RES.append({"indice": i, "escenario": "clima observado, mismas filas", "modelo": FABR[i][0], **f_})
            GEO_RES.append({"indice": i, "escenario": "GEOS-S2S incertidumbre", "grupo": "todos", "n": len(y),
                            "cobertura_rango_5_miembros": float(cub.mean()), "cobertura_nominal": 4 / 6,
                            "crps": float(crps.mean()), "ancho_medio_rango": float((P.max(0) - P.min(0)).mean())})
        log(f"GEOS-S2S: {len(TEg)} filas de prueba con pronóstico ({TEg.f0.min().date()} → {TEg.f0.max().date()}), {len(miembros)} miembros")
except Exception as e:  # noqa: BLE001
    log(f"AVISO escenario GEOS-S2S: {e}\n{traceback.format_exc()[-2000:]}")
guardar(pd.DataFrame(GEO_RES), "paso8_prueba_geos_s2s.csv")

# %% [markdown]
# ## Paso 8b. Semáforo B hacia adelante contra el TDR de Nataima (2021–2022)
# El modelo predictor se reentrena con lo anterior a la 1.ª siembra (objetivos < 2021-05-01, 10 años hacia atrás o
# lo que haya) y pronostica el NDVI de las 3 franjas. El semáforo B (ARIMAX(1), su modelo de uso) se evalúa con sus
# mismas 4 particiones: NDVI observado frente a NDVI pronosticado a 1–30 días.

# %%
B_RES = []
try:
    from scipy.stats import norm
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import StratifiedGroupKFold
    CORTE_B = pd.Timestamp("2021-05-01")
    TR_B = np.where((E.f1 < CORTE_B) & (E.f0 >= CORTE_B - pd.DateOffset(years=ANIOS)) & ~E.es_lote & (E.orden_pix < N_PIX))[0]
    MOD_B = {i: FABR[i][2]().fit(TR_B) for i in ("ndvi",)}
    # pronósticos de las franjas: cada origen despejado de 2021-05 a 2022-12, Δ = 1..30
    filas_f, seq_f = [], []
    for p in LOTE:
        k = int(np.where(SEL == p)[0][0])
        r = ejemplos_pixel(k, objetivos_todos=True)
        if r is None:
            continue
        df, sq = r
        m = (df.d0 >= (CORTE_B - BASE).days) & (df.d0 <= (pd.Timestamp("2022-12-31") - BASE).days)
        filas_f.append(df[m.to_numpy()])
        seq_f.append(sq[m.to_numpy()])
    FF = pd.concat(filas_f, ignore_index=True)
    SQ = np.concatenate(seq_f)
    if FABR["ndvi"][0] == "GRU":
        FF["pred"] = MOD_B["ndvi"].predict(None, tabla=FF, seqs=SQ)[:, 0]
    else:
        FF["pred"] = MOD_B["ndvi"].predict(None, tabla=FF)
    FF = FF.merge(pix_lote[["idx", "frac_MIRI", "frac_AWD+MIRI", "frac_CD"]], left_on="pixel", right_on="idx")
    FRAC = {"oeste": "frac_MIRI", "centro": "frac_AWD+MIRI", "este": "frac_CD"}
    FF["fis"] = FF[list(FRAC.values())].idxmax(axis=1).map({v: k for k, v in FRAC.items()})
    FF = FF[FF[list(FRAC.values())].max(axis=1) >= 0.9]
    PF = FF.groupby(["fis", "d0", "d1"]).pred.mean().reset_index()

    # --- reconstrucción de las entradas del semáforo B (scripts/modelo_humedad_unificado.py)
    diario = pd.read_csv(bajar_repo("data/processed/nataima_diario.csv"))
    clima_b = pd.read_csv(bajar_repo("data/processed/modelo_b/clima_dia.csv"), parse_dates=["fecha"]).set_index("fecha")
    pixb = pd.read_csv(bajar_repo("data/processed/modelo_b/pixeles_lote_s2_landsat.csv"), parse_dates=["fecha"])
    tabla_b = pd.read_csv(bajar_repo("reports/humedad_unificada/tabla_entrenamiento.csv"))
    ROT = {1: ("MIRI", "AWD+MIRI", "CD"), 2: ("AWD+MIRI", "CD", "MIRI"), 3: ("CD", "MIRI", "AWD+MIRI")}
    MALAS = {"2021-07-07", "2021-08-11", "2021-09-15", "2022-01-03"}
    TAW, P_AG, ZR = 6.7, 0.65, 150.0
    RAW, DSAT = P_AG * TAW, ZR * (0.4765 - 0.30)
    s2b = pixb[(pixb.sensor == "S2") & (pixb[list(FRAC.values())].max(axis=1) >= 0.9) & ~pixb.fecha.dt.strftime("%Y-%m-%d").isin(MALAS)].copy()
    s2b["fis"] = s2b[list(FRAC.values())].idxmax(axis=1).map({v: k for k, v in FRAC.items()})
    s2b = s2b.groupby(["fecha", "fis"], as_index=False).ndvi.mean()

    def reservorio(entra, sale, tau):
        s, out, f = 0.0, [], np.exp(-1 / tau)
        for a_, b_ in zip(entra, sale):
            s = s * f + a_ - b_
            out.append(s)
        return np.array(out)

    def balance_fao(lluvia, riego, kc, eto, k):
        dr_, out = 0.0, []
        for q in range(len(lluvia)):
            dr_ -= lluvia[q] + riego[q]
            ks = 1.0 if dr_ <= RAW else max(0.0, (TAW - dr_) / ((1 - P_AG) * TAW))
            dr_ += ks * kc[q] * eto[q]
            if dr_ < 0:
                dr_ *= (1 - k)
            out.append(float(np.clip(dr_, -DSAT, TAW)))
        return np.array(out)

    def dias_desde(ev):
        out, n = [], 60
        for e_ in ev:
            n = 0 if e_ else n + 1
            out.append(min(n, 60))
        return np.array(out)

    SER = {}
    for (t, trat), d in diario.groupby(["temporada", "tratamiento"]):
        d = d.sort_values("das").copy()
        d["fecha"] = pd.Timestamp(SIEMBRAS_NATAIMA[t]) + pd.to_timedelta(d.das - 1, unit="D")
        c_ = clima_b.reindex(d.fecha)
        d["lluvia"] = c_.lluvia.fillna(0).to_numpy()
        d["eto"] = c_.eto.ffill().bfill().to_numpy()
        d["riego"] = d.riego_mm.fillna(0)
        fis = dict(zip(ROT[t], ("oeste", "centro", "este")))[trat]
        s = s2b[s2b.fis == fis].copy()
        s["das"] = (s.fecha - pd.Timestamp(SIEMBRAS_NATAIMA[t])).dt.days + 1
        s = s[s.das.between(-15, 140)].sort_values("das")
        d["ndvi_obs"] = np.interp(d.das, s.das, s.ndvi)
        d["fis"] = fis
        d["dia"] = (d.fecha - BASE).dt.days
        SER[(t, trat)] = d.reset_index(drop=True)

    def entradas(d, ndvi):
        kc = np.clip(1.0 * ndvi + 0.45, 0.6, 1.3)
        sale = kc * d.eto.to_numpy()
        ent = (d.lluvia + d.riego).to_numpy()
        return {"S_tau20": reservorio(ent, sale, 20), "S_tau5": reservorio(ent, sale, 5),
                "Dr_k0.5": balance_fao(d.lluvia.to_numpy(), d.riego.to_numpy(), kc, d.eto.to_numpy(), 0.5),
                "riego_5d": d.riego.rolling(5, min_periods=1).sum().to_numpy(),
                "dias_desde_riego": dias_desde(d.riego.to_numpy() > 0), "eto_5d": d.eto.rolling(5, min_periods=1).sum().to_numpy(),
                "ndvi": ndvi}

    EXB = ["S_tau20", "S_tau5", "Dr_k0.5", "riego_5d", "dias_desde_riego", "eto_5d", "ndvi"]
    filas_b = []  # una fila por (medición TDR, origen del pronóstico) y la versión con NDVI observado
    for (t, trat), d in SER.items():
        obs_in = entradas(d, d.ndvi_obs.to_numpy())
        med = d[d.vwc_pct.notna()]
        for r in med.itertuples():
            filas_b.append({"t": t, "tr": trat, "das": r.das, "vwc": r.vwc_pct, "h": 0, "version": "observado",
                            **{c: obs_in[c][r.Index] for c in EXB}})
            # orígenes: fechas despejadas de la franja antes de la medición (1–30 días)
            orig = PF[(PF.fis == d.fis.iloc[0]) & (PF.d1 == r.dia) & (PF.d0 < r.dia) & (PF.d0 >= d.dia.min())]
            for o in orig.itertuples():
                camino = PF[(PF.fis == d.fis.iloc[0]) & (PF.d0 == o.d0)].set_index("d1").pred
                nd = d.ndvi_obs.to_numpy().copy()
                fut = (d.dia > o.d0) & (d.dia <= r.dia)
                nd[fut.to_numpy()] = camino.reindex(d.dia[fut]).interpolate().bfill().ffill().to_numpy()
                ein = entradas(d, nd)
                filas_b.append({"t": t, "tr": trat, "das": r.das, "vwc": r.vwc_pct, "h": int(r.dia - o.d0),
                                "version": "pronosticado", **{c: ein[c][r.Index] for c in EXB}})
    FB = pd.DataFrame(filas_b)
    cls = lambda v: np.select([v < 27.1, v < 30.0], [2, 1], 0)  # noqa: E731
    FB["y"] = cls(FB.vwc.to_numpy())
    # ARIMAX(1) del semáforo B, mismas 4 particiones que reports/semaforo_clasificador (semilla 0, grupos de 10 días)
    M_B = tabla_b.sort_values(["temporada", "tratamiento", "das"]).reset_index(drop=True)
    yb = cls(M_B.vwc_pct.to_numpy())
    from statsmodels.tsa.statespace.sarimax import SARIMAX

    def ajustar_b(idx):
        X = M_B.loc[idx, EXB].to_numpy()
        mu, sdv = X.mean(0), X.std(0) + 1e-9
        Z = (X - mu) / sdv
        dfz = pd.DataFrame(Z, columns=EXB)
        dfz["hum"] = M_B.vwc_pct.to_numpy()[idx]
        dfz[["t", "tr", "das"]] = M_B.loc[idx, ["temporada", "tratamiento", "das"]].to_numpy()
        en, ex = [], []
        for _, g_ in dfz.groupby(["t", "tr"]):
            g_ = g_.sort_values("das")
            full = pd.DataFrame({"das": np.arange(g_.das.min(), g_.das.max() + 1)}).merge(g_, on="das", how="left")
            full[EXB] = full[EXB].interpolate(limit_direction="both")
            en += list(full.hum) + [np.nan] * 30
            ex += full[EXB].values.tolist() + [[0.0] * len(EXB)] * 30
        res = SARIMAX(np.array(en, float), exog=np.array(ex, float), order=(1, 0, 0), trend="c").fit(disp=False, maxiter=200)
        par = dict(zip(res.model.param_names, np.asarray(res.params)))
        beta = np.array([par[f"x{q + 1}"] for q in range(len(EXB))])
        phi = par.get("ar.L1", 0.0)
        c_ = par.get("intercept", 0.0)
        s2_ = par["sigma2"] / max(1 - phi ** 2, 1e-3)
        prior = np.bincount(yb[idx], minlength=3) / len(idx)
        return mu, sdv, beta, c_ / max(1 - phi, 1e-3), np.sqrt(s2_), prior

    def proba_b(mod, X):
        mu, sdv, beta, k0, sd_, prior = mod
        m_ = (X - mu) / sdv @ beta + k0
        pr = norm.cdf((27.1 - m_) / sd_)
        pa = norm.cdf((30.0 - m_) / sd_) - pr
        P = np.c_[1 - pr - pa, pa, pr] / prior
        return P / P.sum(1, keepdims=True)

    outer = StratifiedGroupKFold(n_splits=4, shuffle=True, random_state=0)
    FB["P0"] = FB["P1"] = FB["P2"] = np.nan
    for dev, te in outer.split(M_B, yb, M_B.grupo.to_numpy()):
        mod = ajustar_b(dev)
        claves = set(zip(M_B.temporada.to_numpy()[te], M_B.tratamiento.to_numpy()[te], M_B.das.to_numpy()[te]))
        m_ = np.array([(a_, b_, c_) in claves for a_, b_, c_ in zip(FB.t, FB.tr, FB.das)])
        FB.loc[m_, ["P0", "P1", "P2"]] = proba_b(mod, FB.loc[m_, EXB].to_numpy())

    def auc_macro(yy, P):
        a_ = [roc_auc_score(yy == q, P[:, q]) for q in range(3) if 0 < (yy == q).sum() < len(yy)]
        return float(np.mean(a_)) if a_ else np.nan

    for nom, q in [("NDVI observado", FB.version == "observado")] + \
            [(f"NDVI pronosticado h {a_}–{b_} d", (FB.version == "pronosticado") & FB.h.between(a_, b_)) for a_, b_, _ in BINS] + \
            [("NDVI pronosticado, todos los horizontes", FB.version == "pronosticado")]:
        S_ = FB[q & FB.P0.notna()]
        if len(S_) < 10:
            continue
        P = S_[["P0", "P1", "P2"]].to_numpy()
        pred = P.argmax(1)
        B_RES.append({"version": nom, "n": len(S_), "auc_macro": auc_macro(S_.y.to_numpy(), P),
                      "recall_rojo": float(np.mean(pred[S_.y == 2] == 2)) if (S_.y == 2).any() else np.nan,
                      "recall_amarillo": float(np.mean(pred[S_.y == 1] == 1)) if (S_.y == 1).any() else np.nan,
                      "exactitud_balanceada": float(np.mean([np.mean(pred[S_.y == q_] == q_) for q_ in range(3) if (S_.y == q_).any()]))})
    log("semáforo B hacia adelante:\n" + pd.DataFrame(B_RES).round(3).to_string(index=False))
except Exception as e:  # noqa: BLE001
    log(f"AVISO semáforo B hacia adelante: {e}\n{traceback.format_exc()[-2000:]}")
guardar(pd.DataFrame(B_RES), "paso8_semaforo_b_hacia_adelante.csv")

# %% [markdown]
# ## Modelo de uso y resumen

# %%
import joblib  # noqa: E402

meta_m = {"fecha": dt.datetime.utcnow().isoformat(), "anios_historia": ANIOS, "sello_prueba": str(SELLO.date()),
          "pixeles_entrenamiento": N_PIX, "variables": VARS, "variables_gru": COLS_GRU, "prithvi": PRITHVI_OK,
          "elegidos": {i: {"familia": FABR[i][0], "hiperparametros": FABR[i][1]} for i in ("ndvi", "ndmi")},
          "error_esperado_por_horizonte": "paso8_prueba_clima_observado.csv y paso8_prueba_geos_s2s.csv",
          "versiones": {"python": sys.version.split()[0], "torch": torch.__version__,
                        "xgboost": __import__("xgboost").__version__, "sklearn": __import__("sklearn").__version__,
                        "statsmodels": __import__("statsmodels").__version__}}
for i in ("ndvi", "ndmi"):
    m = FINAL[i]
    try:
        if FABR[i][0] == "XGBoost":
            m.m.save_model(OUT / "modelos" / f"predictor_{i}.json")
        elif FABR[i][0] == "GRU":
            torch.save({"estado": m.net.state_dict(), "media": m.sc.mean_, "escala": m.sc.scale_, "cols": m.cols, "h": m.h},
                       OUT / "modelos" / f"predictor_{i}_gru.pt")
        elif FABR[i][0] == "ARIMAX":
            json.dump({"beta": m.beta.tolist(), "phi": m.phi, "mu": m.mu, "mu_x": m.mu_x.tolist(), "sd_x": m.sd_x.tolist(),
                       "exog": m.exog}, open(OUT / "modelos" / f"predictor_{i}_arimax.json", "w", encoding="utf-8"))
        else:
            joblib.dump(m.m, OUT / "modelos" / f"predictor_{i}.joblib", compress=3)
    except Exception as e:  # noqa: BLE001
        log(f"AVISO guardar modelo {i}: {e}")
json.dump(meta_m, open(OUT / "modelos" / "meta.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False, default=str)
log(f"ENTRENAMIENTO OK en {(time.time() - T0) / 60:.0f} min · elegidos {meta_m['elegidos']}")
