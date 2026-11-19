# Entrenamiento v1 (19-nov-2026, Kaggle, 89 min): historial, REEMPLAZADO por la corrida final

Se conserva como evidencia.

**Qué quedó incompleto:**
- **Prithvi falló** por un error de dispositivo (CPU frente a GPU) en el código propio. Por eso:
  - no entró como grupo candidato en la selección de variables (paso 6);
  - no se evaluó el relleno de huecos con nubes falsas.
- **El escenario GEOS-S2S** no corrió, porque los datos aún no estaban en el repo.

**La corrida final** del mismo notebook 2, ya corregido, reemplaza estos resultados. Va en `reports/modelo_a/entrenamiento_final/`.

**Lo que esta versión sí muestra y sigue siendo válido como referencia:**
- elección de Random Forest entre los candidatos sin Prithvi;
- prueba sellada con clima observado;
- semáforo B hacia adelante;
- prueba formal Diebold–Mariano del paso 6 (`PASO6_PRUEBA_FORMAL.md`).
