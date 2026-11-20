# ¿Abarata Spark? La ecuación, antes de usarlo

## 1. Modelo de costo

**Notación:**
- **N** = filas a procesar (pixel·fecha).
- **w** = tiempo de cómputo por fila en un núcleo. Medido: 160 ns en numpy y 180 ns en pandas (`bench_computo.json`).
- **b** = bytes por fila en memoria (≈ 50 en formato largo de pandas).
- **k** ≈ 2–3 = copias de trabajo que pandas mantiene al mismo tiempo.
- **c** = núcleos de la máquina.
- **η** ≈ 0,7–0,9 = eficiencia paralela.
- **P** = precio por hora de la máquina.
- **D** = tiempo de descarga de las imágenes. Lo limita la red y **es igual en las dos opciones**, así que se cancela.

**Python en una máquina** (numpy o pandas, por bloques en paralelo con `multiprocessing`):

  T_py = N·w / (c·η)                         si N·b·k ≤ RAM
  (si no cabe en RAM: hay que trocear o usar una máquina más grande)

**Spark en la misma máquina** (local[*], que es lo que se puede usar en Kaggle o en una sola EC2):

  T_spark = T₀ + N·w / (c·η) + N·s / c

- **T₀** = arranque de Java y Spark (≈ 10–30 s, valor típico).
- **s** = costo por fila de pasar los datos entre Python y Java (Arrow): unos ns por valor (valor típico).

**Spark en un clúster de n máquinas:**

  T_spark,n = T₀' + N·(w + s) / (n·c·η) + T_shuffle,     costo = n·P·T_spark,n

- **T₀'** = arranque del clúster: minutos.

## 2. Condición para que Spark abarate

**En la misma máquina:** Spark abarata solo si T_spark < T_py, es decir:

  T₀ + N·s/c < 0

**Eso nunca se cumple.** En una máquina, Spark hace el mismo cómputo y le suma su arranque y la serialización. Solo gana frente a pandas **de un solo núcleo**: c·η veces más rápido, menos el arranque. Pero numpy por bloques también usa todos los núcleos.

**En clúster:** n·P·T_spark,n < P·T_py exige

  n·(T₀' + N·(w+s)/(n·c·η)) < N·w/(c·η)  →  n·T₀' + N·s/(c·η) < 0

**Tampoco se cumple.** Con precio lineal por máquina (como en EC2), repartir no baja el costo total; baja el tiempo y le suma arranque.

**El único caso en que Spark abarata es por memoria:**
- Si **N·b·k > RAM**, pandas necesita una máquina más grande (y más cara) o trocear a mano.
- Spark procesa por particiones de tamaño N/p y desborda a disco. Así cabe en una máquina pequeña.

  Spark abarata  ⇔  N·b·k > RAM de la máquina barata  y  P_grande·T_py > P_pequeña·T_spark

## 3. Con los números de Nataima (10 años, ventana de 5 × 5 km a 10 m)

| Cantidad | Valor |
|---|---|
| N (pixel·fecha, todas las fechas) | 250 000 × 1 410 = 3,5 × 10⁸ |
| N después de quitar nubes (~30 % despejado) | ≈ 1,1 × 10⁸ |
| Cómputo en 1 núcleo, N·w | 3,5 × 10⁸ × 160 ns ≈ **56 s** |
| Con 4 núcleos (Kaggle), η = 0,8 | ≈ **18 s** |
| Spark: arranque T₀ | ≈ 10–30 s (**más que todo el cómputo**) |
| Memoria pandas, N·b·k (solo despejados) | 1,1 × 10⁸ × 50 × 2,5 ≈ **14 GB** → cabe en Kaggle (~30 GB, según fuentes secundarias) si se filtra antes |

**Conclusión de la ecuación: Spark NO abarata en Nataima.**
- El cómputo de preparación es de segundos.
- El arranque de Spark ya cuesta más que eso.
- Todo cabe en memoria.
- El costo real es la descarga, que es igual con o sin Spark.

**Cuándo empieza a ser necesario (por memoria):** cuando N·b·k supera la RAM, es decir, N ≳ 2 × 10⁸ filas despejadas en una máquina de 30 GB. Eso pasa con:
- ≳ 45 km² con 10 años; o
- la tesela completa (≈ 5 × 10¹⁰ filas despejadas); o
- el escenario de 100 predios.

Aun en esos casos, numpy por bloques en una máquina hace lo mismo. Spark aporta **orden y reparto automático** en muchas máquinas, no menos operaciones.

## 4. Qué falta medir

T₀ y s son valores típicos, no medidos aquí. Se pueden medir en local con un micro-ensayo de minutos, sin costo, con PySpark + Java 17. Si salen distintos, solo cambia el umbral, no la conclusión, porque los dos términos son ≥ 0.
