"""Convierte sira_a_1_datos.py y sira_a_2_entrenar.py (celdas `# %%`) en notebooks .ipynb para Kaggle.

Uso: python cloud/modelo_a/hacer_notebooks.py   → cloud/modelo_a/sira_a_1_datos.ipynb y sira_a_2_entrenar.ipynb
"""
import json
import re
from pathlib import Path

AQUI = Path(__file__).resolve().parent


def celdas(texto):
    out = []
    for bloque in re.split(r"^# %%", texto, flags=re.M)[1:]:
        cab, _, cuerpo = bloque.partition("\n")
        if cab.strip().startswith("[markdown]"):
            lineas = [re.sub(r"^# ?", "", ln) for ln in cuerpo.strip("\n").splitlines()]
            out.append({"cell_type": "markdown", "metadata": {}, "source": "\n".join(lineas)})
        else:
            out.append({"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [],
                        "source": cuerpo.strip("\n")})
    return out


for nombre in ("sira_a_1_datos", "sira_a_2_entrenar"):
    texto = (AQUI / f"{nombre}.py").read_text(encoding="utf-8")
    nb = {"cells": celdas(texto), "nbformat": 4, "nbformat_minor": 5,
          "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                       "language_info": {"name": "python"}}}
    (AQUI / f"{nombre}.ipynb").write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print(nombre, len(nb["cells"]), "celdas")
