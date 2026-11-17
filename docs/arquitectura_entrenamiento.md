# SIRA: flujo de entrenamiento, paso a paso

Se lee de arriba hacia abajo. **Cilindros** = datos que entran. **Rectángulos** = transformaciones. **Paralelogramos** = lo que produce cada paso.

```mermaid
flowchart TB
  %% ---------- insumos ----------
  CAMPO[("Paper Nataima<br/>humedad TDR (198 mediciones)<br/>riego diario, días desde siembra")]
  S2[("Sentinel-2 L2A<br/>píxeles de 10–20 m")]
  HLS[("HLS = Landsat 8/9 + Sentinel-2<br/>píxeles de 30 m, 6 bandas")]
  CHIRPS[("CHIRPS<br/>lluvia diaria")]
  POWER[("NASA POWER<br/>temperatura, radiación,<br/>viento, humedad del aire")]

  %% ---------- rama satélite: índices ----------
  S2 --> NUB1["1a. Quitar nubes y sombras<br/>(máscara de calidad de la imagen)"]
  NUB1 --> IDX["2a. Calcular índices en cada píxel<br/>NDMI = (B8A − B11)/(B8A + B11)<br/>NDWI = (B3 − B8)/(B3 + B8)<br/>NDVI, MNDWI"]
  IDX --> OIDX[/"Índices por píxel y fecha"/]

  %% ---------- rama satélite: Prithvi ----------
  HLS --> NUB2["1b. Quitar nubes y sombras"]
  NUB2 --> PRI["2b. Prithvi congelado<br/>recibe 6 bandas × hasta 4 fechas<br/>devuelve 1024 números por bloque"]
  PRI --> PCA["3b. Comprimir 1024 → 10–20 números<br/>(PCA)"]
  PCA --> OPRI[/"Resumen Prithvi por bloque y fecha"/]

  %% ---------- rama clima ----------
  CHIRPS --> LLU["2c. Lluvia acumulada<br/>de 1, 3 y 5 días"]
  POWER --> ETO["2d. ETo con FAO-56<br/>(cuánta agua pide la atmósfera)"]
  LLU --> OCLI[/"Clima por día"/]
  ETO --> OCLI

  %% ---------- unir ----------
  CAMPO --> UNIR
  OIDX --> UNIR
  OPRI --> UNIR
  OCLI --> UNIR
  UNIR["4. Emparejar<br/>misma parcela + misma fecha (±1 día)<br/>solo píxeles dentro de cada franja"]
  UNIR --> TABLA[/"Tabla de entrenamiento<br/>una fila = parcela × día medido<br/>entradas: índices + Prithvi + clima + riego + días<br/>respuesta: humedad TDR"/]

  %% ---------- entrenar ----------
  TABLA --> PART["5. Partir por temporada<br/>entrenar con 2, probar con la 3ª<br/>(3 vueltas)"]
  PART --> EST["6. Estandarizar<br/>con medias y desviaciones<br/>solo del entrenamiento"]
  EST --> MA["7A. Modelo A<br/>índices + clima → Random Forest"]
  EST --> MB["7B. Modelo B<br/>A + Prithvi → Random Forest"]
  EST --> MC["7C. Modelo C<br/>Prithvi + cabeza pequeña"]
  MA --> EVAL
  MB --> EVAL
  MC --> EVAL
  EVAL["8. Evaluar en la temporada no vista<br/>error en puntos de humedad (RMSE)<br/>acierto en déficit / óptimo / exceso"]
  EVAL --> FIN[/"Modelo elegido<br/>→ mapa de humedad por píxel"/]

  classDef dato fill:#E6F1FB,stroke:#185FA5,color:#0C447C
  classDef proceso fill:#F1EFE8,stroke:#5F5E5A,color:#2C2C2A
  classDef producto fill:#E1F5EE,stroke:#0F6E56,color:#085041
  class CAMPO,S2,HLS,CHIRPS,POWER dato
  class NUB1,IDX,NUB2,PRI,PCA,LLU,ETO,UNIR,PART,EST,MA,MB,MC,EVAL proceso
  class OIDX,OPRI,OCLI,TABLA,FIN producto
```

## Notas

- **Respuesta que aprende el modelo:** la humedad medida en campo. Todo lo demás son entradas.
- **Clima:** se entrena con CHIRPS y NASA POWER porque son las fuentes que habrá en producción. La lluvia medida en el lote solo sirve para medir cuánto se equivocan.
- **Prithvi:** se usa congelado, como en el ejemplo de carbono de NASA. El modelo C es la receta "oficial" (Prithvi + cabeza); A y B permiten medir cuánto aporta Prithvi frente a los índices solos.
