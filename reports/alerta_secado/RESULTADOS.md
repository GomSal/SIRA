# Alerta de secado del suelo: AUC-ROC, recall y precisión

Script: `scripts/alerta_secado.py`.
- **Datos:** las 3 temporadas como un solo cultivo (tabla de `reports/humedad_unificada/`); respuesta = TDR medido (198 mediciones).
- **Validación:** por bloques de 10 días (las 3 franjas de un bloque salen juntas) y, además, una temporada afuera.
- **Umbral de alerta:** se elige **dentro del entrenamiento** de cada partición, con el índice de Youden sobre predicciones fuera de muestra de una validación interna. La prueba nunca toca el umbral.
- **IC 95 %:** bootstrap de bloques de 10 días, 1000 repeticiones.

## Alerta "seco": humedad < 30 % (bajo capacidad de campo), 59 de 198 (30 %)

| Modelo (entradas completas) | AUC-ROC [IC 95 %] | Recall [IC 95 %] | Precisión [IC 95 %] | Especificidad |
|---|---|---|---|---|
| **Regresión logística** | **0,79 [0,70–0,87]** | **0,83 [0,67–0,95]** | 0,49 [0,37–0,61] | 0,63 |
| Logística solo con el balance de agua | 0,77 [0,69–0,84] | 0,88 [0,79–0,96] | 0,47 [0,36–0,58] | 0,58 |
| Random Forest | 0,75 [0,68–0,81] | 0,88 [0,72–1,00] | 0,44 [0,34–0,55] | 0,53 |
| XGBoost | 0,75 [0,68–0,82] | 0,86 [0,72–0,98] | 0,46 [0,36–0,56] | 0,56 |
| Regresión de humedad (RF) como puntaje | 0,76 [0,69–0,83] | 0,86 [0,70–0,98] | 0,46 [0,35–0,57] | 0,56 |

Con una temporada afuera, la logística da AUC 0,82, recall 0,86 y precisión 0,49.

## Alerta "déficit": humedad < 27,1 %, 17 de 198 (9 %), todas en AWD+MIRI

| Modelo (entradas completas) | AUC-ROC [IC 95 %] | Recall [IC 95 %] | Precisión [IC 95 %] | Especificidad |
|---|---|---|---|---|
| **XGBoost** | **0,76 [0,62–0,86]** | **0,71 [0,43–0,92]** | 0,18 [0,08–0,29] | 0,70 |
| Random Forest | 0,75 [0,63–0,85] | 0,71 [0,46–0,92] | 0,15 [0,07–0,26] | 0,62 |
| Logística solo con el balance de agua | 0,76 [0,62–0,85] | 0,65 [0,33–0,88] | 0,16 [0,06–0,27] | 0,68 |
| Regresión logística | 0,70 [0,56–0,81] | 0,59 [0,27–0,83] | 0,11 [0,04–0,19] | 0,54 |

Con una temporada afuera, XGBoost da AUC 0,77, recall 0,76 y precisión 0,18.

## Sin registro de riego

En las dos alertas y con todos los modelos, el AUC-ROC queda entre **0,36 y 0,57**, que es el nivel del azar. Sin saber cuándo se regó, no hay alerta.

## Lectura

- **"Seco" funciona:** detecta 8 de cada 10 días bajo capacidad de campo, con AUC ≈ 0,8. Cerca de la mitad de las alertas son falsas: precisión 0,49, frente al 0,30 que daría alertar al azar. El mejor es el modelo más simple, la regresión logística.
- **"Déficit" es más difícil:** detecta 7 de cada 10, pero emite unas 5 alertas falsas por cada acierto (precisión 0,18, frente a 0,09 al azar). Hay solo 17 casos y los IC son anchos.
- **El riego registrado es indispensable.**
- **Para la app:** semáforo amarillo = alerta "seco" (revisar el lote o regar pronto); rojo = alerta "déficit" con su nivel de confianza.
