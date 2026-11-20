"""¿Qué fuente de lluvia se parece más a la medida en el propio lote de Nataima (Fig. 2 del artículo)?

Fuentes (descargadas en data/raw/clima/):
- NASA POWER (PRECTOTCORR, celda ~0,5°)            power.json
- ERA5 vía Open-Meteo (celda ~0,25°)               era5_openmeteo.json
- CHIRPS v2 vía ClimateSERV (promedio 0,05° sobre el lote)  chirps_climateserv.json
- IDEAM, estaciones automáticas cercanas (datos.gov.co): ideam_<código>_<inicio>.json

Pasos:
1. El artículo solo da meses de siembra. Para cada temporada se prueban fechas de siembra posibles y se
   elige la que mejor alinea la lluvia del artículo con las fuentes satelitales/reanálisis (consenso: media
   de la correlación de sumas de 5 días de POWER, ERA5 y CHIRPS).
2. Con esa fecha fija, cada fuente se compara contra la lluvia del artículo (permitiendo ±1 día por el
   corte horario del "día" de cada fuente).

Salidas: data/interim/lluvia_fuentes_diaria.csv y reports/comparacion_lluvia.md
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
C = RAIZ / "data" / "raw" / "clima"
IDEAM = {"0021187030": ("IDEAM Cucunubá (Valle de San Juan)", 15.3),
         "2119700147": ("IDEAM San Miguel (Ricaurte)", 24.8),
         "2119500111": ("IDEAM Base Aérea Melgar", 36.0)}
VENTANAS = {1: ("2021-04-10", "2021-06-15"), 2: ("2021-11-05", "2022-01-20"), 3: ("2022-06-10", "2022-08-15")}


def cargar_fuentes() -> pd.DataFrame:
    p = json.loads((C / "power.json").read_text())["properties"]["parameter"]["PRECTOTCORR"]
    power = pd.Series({pd.Timestamp(k): v for k, v in p.items()}).replace(-999.0, np.nan)
    e = json.loads((C / "era5_openmeteo.json").read_text())["daily"]
    era5 = pd.Series(e["precipitation_sum"], index=pd.to_datetime(e["time"]))
    ch = json.loads((C / "chirps_climateserv.json").read_text())["data"]
    chirps = pd.Series({pd.Timestamp(r["year"], r["month"], r["day"]): r["value"]["avg"] for r in ch})
    df = pd.DataFrame({"NASA POWER": power, "ERA5": era5, "CHIRPS": chirps})
    for cod, (nombre, _) in IDEAM.items():
        filas = []
        for f in C.glob(f"ideam_{cod}_*.json"):
            txt = f.read_text(encoding="utf-8").strip()
            if txt.startswith("["):
                filas += json.loads(txt)
        if filas:
            s = pd.DataFrame(filas)
            s = s[s.n.astype(int) >= 130]  # día completo: ≥130 de 144 registros de 10 min
            df[nombre] = pd.Series(s.mm.astype(float).values, index=pd.to_datetime(s.dia.str[:10]))
    return df.sort_index()


def r(a: pd.Series, b: pd.Series) -> float:
    m = a.notna() & b.notna()
    return float(np.corrcoef(a[m], b[m])[0, 1]) if m.sum() > 10 and a[m].std() > 0 and b[m].std() > 0 else np.nan


def alinear(paper: pd.Series, fuente: pd.Series, siembra: pd.Timestamp, desf: int = 0) -> pd.Series:
    fechas = siembra + pd.to_timedelta(paper.index - 1 + desf, unit="D")
    return pd.Series(fuente.reindex(fechas).values, index=paper.index)


def main() -> None:
    fuentes = cargar_fuentes()
    lluvia = pd.read_csv(RAIZ / "data" / "interim" / "fig2_lluvia_atwd.csv")
    grillas = ["NASA POWER", "ERA5", "CHIRPS"]
    filas, siembras, tabla = [], {}, []
    for t, g in lluvia.groupby("temporada"):
        paper = g.set_index("das").lluvia_mm
        p5 = paper.rolling(5).sum()
        cand = pd.date_range(*VENTANAS[t])
        puntaje = {d: np.nanmean([r(p5, alinear(paper, fuentes[s], d).rolling(5).sum()) for s in grillas])
                   for d in cand}
        siembra = max(puntaje, key=puntaje.get)
        siembras[t] = (siembra, puntaje[siembra])
        out = pd.DataFrame({"temporada": t, "das": paper.index,
                            "fecha": siembra + pd.to_timedelta(paper.index - 1, unit="D"), "paper": paper.values})
        for s in fuentes.columns:
            # ±1 día por el corte horario del "día" de cada fuente
            desf = max((-1, 0, 1), key=lambda k: np.nan_to_num(r(p5, alinear(paper, fuentes[s], siembra, k)
                                                                    .rolling(5).sum()), nan=-9))
            a = alinear(paper, fuentes[s], siembra, desf)
            out[s] = a.values
            m = a.notna()
            if m.sum() < 20:
                tabla.append({"temporada": t, "fuente": s, "dias_con_dato": int(m.sum())})
                continue
            pa, aa = paper[m], a[m]
            llueve_p, llueve_a = pa >= 5, aa >= 5
            tabla.append({
                "temporada": t, "fuente": s, "dias_con_dato": int(m.sum()), "desfase_dias": desf,
                "r_diaria": round(r(pa, aa), 2), "r_5dias": round(r(pa.rolling(5).sum(), aa.rolling(5).sum()), 2),
                "total_paper_mm": round(pa.sum(), 1), "total_fuente_mm": round(aa.sum(), 1),
                "sesgo_pct": round(100 * (aa.sum() - pa.sum()) / pa.sum(), 1) if pa.sum() > 0 else np.nan,
                "error_medio_mm_dia": round(float((aa - pa).abs().mean()), 2),
                "acierta_dias_lluvia_pct": round(100 * (llueve_a & llueve_p).sum() / max(llueve_p.sum(), 1), 0),
                "falsas_alarmas_pct": round(100 * (llueve_a & ~llueve_p).sum() / max(llueve_a.sum(), 1), 0),
                "max_paper": pa.max(), "max_fuente": round(aa.max(), 1)})
        filas.append(out)
    diaria = pd.concat(filas, ignore_index=True)
    diaria.to_csv(RAIZ / "data" / "interim" / "lluvia_fuentes_diaria.csv", index=False)
    tab = pd.DataFrame(tabla)
    tab.to_csv(RAIZ / "data" / "interim" / "lluvia_fuentes_metricas.csv", index=False)

    # resumen global (solo días con dato de cada fuente, las 3 temporadas juntas)
    res = []
    for s in fuentes.columns:
        m = diaria[s].notna()
        if m.sum() < 30:
            continue
        pa, aa = diaria.paper[m], diaria[s][m]
        r5 = np.nanmean([r(g.paper.rolling(5).sum(), g[s].rolling(5).sum())
                         for _, g in diaria[m].groupby("temporada") if len(g) > 20])
        res.append({"fuente": s, "dias": int(m.sum()), "r_diaria": round(r(pa, aa), 2), "r_5dias": round(r5, 2),
                    "sesgo_pct": round(100 * (aa.sum() - pa.sum()) / pa.sum(), 1),
                    "error_medio_mm_dia": round(float((aa - pa).abs().mean()), 2)})
    res = pd.DataFrame(res).sort_values("r_5dias", ascending=False)

    L = ["# Comparación de fuentes de lluvia contra la lluvia medida en Nataima (Fig. 2)", "",
         "Referencia: lluvia diaria digitalizada del artículo (estación en el propio lote). "
         "Fechas de siembra estimadas por alineación (el artículo solo da meses):", ""]
    for t, (d, sc) in siembras.items():
        L.append(f"- GS{t}: siembra ≈ **{d.date()}** (correlación media de sumas de 5 días con las fuentes en grilla: {sc:.2f})")
    L += ["", "## Resumen (las 3 temporadas, solo días con dato de cada fuente)", "",
          res.to_markdown(index=False), "",
          "r_diaria: correlación día a día. r_5dias: correlación de sumas de 5 días (lo que importa para el "
          "balance de agua). sesgo: cuánto más (+) o menos (−) llueve la fuente que lo medido en el lote.", "",
          "## Por temporada", "", tab.to_markdown(index=False), ""]
    L += ["## Decisión", "",
          "- **Ninguna fuente externa reproduce bien la lluvia diaria del lote** (correlación día a día ≤ 0,42; "
          "el aguacero de 113 mm de GS1 aparece como 26–48 mm en las fuentes satelitales). En el trópico la lluvia "
          "es muy local: el modelo no debe tomar la lluvia diaria externa como dato exacto.",
          "- **Lluvia: CHIRPS** como fuente principal: menor sesgo en volumen (+1 %, +12 %, +29 % por temporada), "
          "~5 km y serie completa. ERA5 se descarta para lluvia (+142 % en GS2). NASA POWER queda de respaldo.",
          "- **IDEAM no es mejor aquí:** las estaciones automáticas públicas más cercanas están a 15–36 km, tienen "
          "grandes huecos (ninguna tiene datos de GS2) y subestiman 36–63 %. San Miguel correlaciona algo mejor en "
          "sumas de 5 días (0,50), pero con solo 82 días.",
          "- **Temperatura, radiación, viento y humedad del aire (para la ETo):** NASA POWER (no evaluado aquí "
          "porque el artículo no publica esas variables; varían poco en el espacio).",
          "- Las fechas de siembra salen de esta misma alineación; la de GS3 es la menos segura (0,29). Esto "
          "favorece un poco a las fuentes en grilla frente al IDEAM.", "",
          "## Datos descargados (data/raw/clima/)", "",
          "- NASA POWER: power.larc.nasa.gov/api/temporal/daily/point (PRECTOTCORR, punto del lote).",
          "- ERA5: archive-api.open-meteo.com/v1/archive (precipitation_sum).",
          "- CHIRPS v2: climateserv.servirglobal.net/api (datatype=0, promedio sobre 0,05° alrededor del lote).",
          "- IDEAM: datos.gov.co, dataset s54a-sgyg (estaciones automáticas; día completo = ≥130 de 144 registros).",
          ""]
    (RAIZ / "reports" / "comparacion_lluvia.md").write_text("\n".join(L), encoding="utf-8")
    print("listo: reports/comparacion_lluvia.md, data/interim/lluvia_fuentes_diaria.csv")


if __name__ == "__main__":
    main()
