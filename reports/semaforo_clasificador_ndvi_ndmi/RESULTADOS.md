# Semáforo con NDVI + NDMI (Sentinel-2)

Mismo clasificador, separación, candidatos y criterio que `reports/semaforo_clasificador/`. Solo se agrega el NDMI de la franja: mismo procesamiento que el NDVI (pixeles ≥ 90 % en la franja, control de bruma, interpolación diaria).
- Correlación del NDMI con la humedad TDR: 0,02.
- Correlación del NDMI con el NDVI: 0,82.

| Prueba sellada | Solo NDVI | NDVI + NDMI |
|---|---|---|
| AUC-ROC macro | 0,75 [0,68–0,81] | 0,76 [0,69–0,83] |
| AUC verde / amarillo / rojo | 0,77 / 0,73 / 0,74 | 0,77 / 0,76 / 0,75 |
| Recall verde | 0,66 | 0,69 |
| Recall amarillo | 0,29 | 0,21 |
| Recall rojo | 0,65 (11 de 17) | 0,71 (12 de 17) |
| Precisión rojo | 0,21 | 0,22 |
| Modelo elegido | ARIMAX en 3 de 4 particiones; modelo de uso ARIMAX(1) | igual |

**Lectura:** el NDMI cambia poco y dentro del margen de error. Detecta un déficit más (12 de 17) y pierde en amarillo. Su aporte no es claro.
