import time, json, os, numpy as np, pandas as pd, warnings
warnings.filterwarnings("ignore")
rng = np.random.default_rng(0); R = {}
def t(f, *a, **k):
    s = time.perf_counter(); out = f(*a, **k); return time.perf_counter() - s, out
# ---------- 1. preprocesado por pixel·fecha (numpy, vectorizado) ----------
P, D = 20000, 1900
dias = np.sort(rng.choice(np.arange(42*365), D, replace=False)).astype(float)
def prepro_numpy():
    b4 = rng.integers(200, 3000, (D, P), dtype=np.uint16); b8 = rng.integers(1500, 5000, (D, P), dtype=np.uint16)
    b8a = rng.integers(1500, 5000, (D, P), dtype=np.uint16); b11 = rng.integers(800, 3000, (D, P), dtype=np.uint16)
    scl = rng.integers(0, 12, (D, P), dtype=np.uint8)
    ndvi = (b8.astype(np.float32) - b4) / (b8.astype(np.float32) + b4)
    ndmi = (b8a.astype(np.float32) - b11) / (b8a.astype(np.float32) + b11)
    ok = np.isin(scl, [4, 5])                      # vegetación o suelo
    ndvi[~ok] = np.nan; ndmi[~ok] = np.nan
    # bruma: caída > 0,10 frente a vecinas (por pixel)
    d1 = ndvi[1:-1] - np.fmax(ndvi[:-2], ndvi[2:]); ndvi[1:-1][d1 < -0.10] = np.nan
    return ndvi, ndmi, ok
dt, (ndvi, ndmi, ok) = t(prepro_numpy); R["prepro_ns_por_pixfecha_numpy"] = dt / (P*D) * 1e9
# interpolación diaria por pixel (relleno simple) sobre la rejilla de fechas
def interp():
    out = np.empty_like(ndvi)
    for j in range(P):
        m = ~np.isnan(ndvi[:, j]); out[:, j] = np.interp(dias, dias[m], ndvi[m, j]) if m.sum() > 1 else np.nan
    return out
dt, _ = t(interp); R["interp_ns_por_pixfecha_numpy"] = dt / (P*D) * 1e9
# ejemplos: pares (origen, objetivo) despejados con 1 ≤ Δ ≤ 30 días
def ejemplos():
    n = 0
    for j in range(P):
        d = dias[ok[:, j]]
        k = np.searchsorted(d, d + 30, side="right") - np.arange(1, len(d) + 1)
        n += int(k.sum())
    return n
dt, n_ej = t(ejemplos); R["ejemplos_por_pixel"] = n_ej / P; R["ejemplos_ns_por_pixfecha"] = dt / (P*D) * 1e9
# misma QC con pandas fila a fila (formato largo) para comparar
Pl = 2000
df = pd.DataFrame({"pixel": np.repeat(np.arange(Pl), D), "dia": np.tile(dias, Pl),
                   "ndvi": rng.random(Pl*D), "scl": rng.integers(0, 12, Pl*D)})
def prepro_pandas():
    d = df[df.scl.isin([4, 5])].sort_values(["pixel", "dia"])
    g = d.groupby("pixel").ndvi
    d = d[~((d.ndvi - np.fmax(g.shift(1), g.shift(-1))) < -0.10)]
    return d.groupby("pixel").apply(lambda x: np.interp(dias, x.dia, x.ndvi)).shape
dt, _ = t(prepro_pandas); R["prepro_ns_por_pixfecha_pandas"] = dt / (Pl*D) * 1e9
del ndvi, ndmi, ok
print({k: round(v, 1) for k, v in R.items()}, flush=True)
# ---------- 2. ajuste de modelos ----------
from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
from xgboost import XGBRegressor
F = 20
def datos(N):
    X = rng.normal(size=(N, F)).astype(np.float32); y = X[:, 0]*0.5 + np.sin(X[:, 1]) + rng.normal(scale=.3, size=N); return X, y
for N in (10_000, 100_000, 1_000_000):
    X, y = datos(N)
    mods = {"XGBoost(300,d4)": lambda: XGBRegressor(n_estimators=300, max_depth=4, learning_rate=.1, subsample=.8, n_jobs=16, tree_method="hist", verbosity=0)}
    if N <= 100_000:
        mods["RandomForest(300,leaf5)"] = lambda: RandomForestRegressor(n_estimators=300, min_samples_leaf=5, max_features=0.33, n_jobs=16, random_state=0)
        mods["GradientBoosting(200,d3)"] = lambda: GradientBoostingRegressor(n_estimators=200, max_depth=3, subsample=.8, random_state=0)
    for nm, f in mods.items():
        dt, _ = t(f().fit, X, y); R[f"fit_s|{nm}|{N}"] = dt; print(nm, N, round(dt, 2), flush=True)
# GRU pequeña: secuencia de 12 observaciones × 4 variables, oculto 32, 5 épocas
import torch; torch.set_num_threads(16)
class G(torch.nn.Module):
    def __init__(s): super().__init__(); s.g = torch.nn.GRU(4, 32, batch_first=True); s.o = torch.nn.Linear(32 + 6, 5)
    def forward(s, x, z): h = s.g(x)[1][-1]; return s.o(torch.cat([h, z], 1))
for N in (10_000, 100_000):
    x = torch.randn(N, 12, 4); z = torch.randn(N, 6); yy = torch.randn(N, 5); m = G(); op = torch.optim.Adam(m.parameters(), 1e-3)
    s = time.perf_counter()
    for ep in range(5):
        for i in range(0, N, 512):
            op.zero_grad(); l = ((m(x[i:i+512], z[i:i+512]) - yy[i:i+512])**2).mean(); l.backward(); op.step()
    R[f"fit_s|GRU(32,5ep)|{N}"] = time.perf_counter() - s; print("GRU", N, round(R[f"fit_s|GRU(32,5ep)|{N}"], 2), flush=True)
# ARIMAX: una serie por pixel (statsmodels), 600 pasos con huecos, 4 exógenas
from statsmodels.tsa.statespace.sarimax import SARIMAX
ts = []
for _ in range(5):
    e = rng.normal(size=(600, 4)); yv = e @ [.3, .2, -.1, .05] + np.cumsum(rng.normal(scale=.05, size=600)); yv[rng.random(600) < .7] = np.nan
    s = time.perf_counter(); SARIMAX(yv, exog=e, order=(1, 0, 0), trend="c").fit(disp=False, maxiter=200); ts.append(time.perf_counter() - s)
R["fit_s|ARIMAX(1) por serie 600 pasos"] = float(np.median(ts)); print("ARIMAX", R["fit_s|ARIMAX(1) por serie 600 pasos"])
json.dump(R, open(__import__("pathlib").Path(__file__).resolve().parents[2] / "reports/modelo_a/estimacion/bench_computo.json", "w"), indent=1)
