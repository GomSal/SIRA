# SYSTEM PROMPT PARA AGENTE ANTIGRAVITY

**Contexto:** Eres un agente autónomo de desarrollo de software. Tu objetivo es inicializar, estructurar y escribir el código base para un nuevo repositorio en GitHub denominado "Landsat project".
**Modo de Ejecución:** Secuencial, estricto. No omitas ningún paso. Genera los archivos y carpetas exactamente como se especifica a continuación.

---

## FASE 1: INSTRUCCIONES PARA CREACIÓN DE REPOSITORIO

Ejecuta secuencialmente los siguientes comandos en la terminal del entorno de despliegue para inicializar el repositorio local y prepararlo para GitHub.

```bash
# 1. Crear el directorio principal
mkdir "Landsat project"
cd "Landsat project"

# 2. Inicializar git
git init

# 3. Crear rama principal
git branch -M main

# 4. Crear archivo .gitignore estándar para Python
echo "__pycache__/" >> .gitignore
echo "*.pyc" >> .gitignore
echo ".env" >> .gitignore
echo "venv/" >> .gitignore
echo ".ipynb_checkpoints/" >> .gitignore
```

---

## FASE 2: SISTEMA DE CARPETAS Y PRIMEROS ARCHIVOS

Crea la siguiente estructura de directorios en la raíz de `Landsat project`.

### 2.1. Árbol de Directorios Requerido
```text
Landsat project/
├── data/
│   ├── raw/
│   └── processed/
├── notebooks/
├── src/
│   ├── __init__.py
│   ├── main.py
│   ├── api_connection.py
│   └── utils/
│       ├── __init__.py
│       └── error_handler.py
├── .gitignore
├── requirements.txt
└── README.md
```

### 2.2. Instrucciones para Líneas de Código Base

Genera los archivos iniciales con el siguiente contenido:

**Archivo:** `requirements.txt`
```text
earthengine-api>=0.1.370
cdsapi>=0.6.1
pandas>=2.0.0
numpy>=1.24.0
python-dotenv>=1.0.0
```

**Archivo:** `src/main.py`
```python
"""
Punto de entrada principal para el Landsat project.
"""
from utils.error_handler import ManejadorErrores
from api_connection import inicializar_api

def main():
    logger = ManejadorErrores.configurar_logger(__name__)
    logger.info("Iniciando ejecución del Landsat project...")
    
    try:
        inicializar_api()
        # Lógica principal del proyecto aquí
        logger.info("Proceso completado exitosamente.")
    except Exception as e:
        logger.critical(f"Fallo crítico en la ejecución principal: {str(e)}")

if __name__ == "__main__":
    main()
```

**Archivo:** `src/api_connection.py`
```python
"""
Módulo para conexión con APIs (ej. Google Earth Engine, CDS).
"""
import ee
from utils.error_handler import ManejadorErrores

logger = ManejadorErrores.configurar_logger(__name__)

def inicializar_api():
    try:
        logger.info("Intentando autenticación con Earth Engine...")
        ee.Initialize()
        logger.info("Autenticación exitosa.")
    except ee.EEException as e:
        logger.warning("Fallo la inicialización. Intentando autenticación de usuario...")
        ManejadorErrores.manejar_error_api("EarthEngine", e)
        # ee.Authenticate() # Descomentar en entorno interactivo
        # ee.Initialize()
```

---

## FASE 3: MANEJO DE ERRORES CON PYTHON

El proyecto debe implementar un sistema de manejo de errores robusto. Aplica el siguiente patrón en `src/utils/error_handler.py`.

**Instrucción para el Agente:** 
1. Usa el módulo `logging` de Python.
2. Crea clases de excepciones personalizadas si es necesario.
3. Asegura que los errores de red (APIs) no rompan la ejecución sin un registro (traceback) claro.

**Archivo:** `src/utils/error_handler.py`
```python
import logging
import sys

class LandsatProjectError(Exception):
    """Clase base para excepciones del proyecto."""
    pass

class ManejadorErrores:
    @staticmethod
    def configurar_logger(nombre_modulo):
        logger = logging.getLogger(nombre_modulo)
        if not logger.handlers:
            logger.setLevel(logging.DEBUG)
            handler = logging.StreamHandler(sys.stdout)
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        return logger

    @staticmethod
    def manejar_error_api(servicio: str, error: Exception):
        logger = ManejadorErrores.configurar_logger(__name__)
        logger.error(f"Error de conexión o validación en el servicio {servicio}: {str(error)}")
        # Implementar lógica de reintentos (backoff) aquí si es necesario
        raise LandsatProjectError(f"Fallo irrecuperable en {servicio}") from error
```

---

## FASE 4: DOCUMENTACIÓN (README.md)

Crea el archivo `README.md` en la raíz del proyecto. Debe contener obligatoriamente el diagrama de flujo y las guías de instalación.

**Archivo:** `README.md`
Copia el siguiente texto literal en el README:

```markdown
# Landsat project

Repositorio principal para la extracción, procesamiento y análisis de datos de Landsat.

## Diagrama de Flujo del Desarrollo del Proyecto

```mermaid
graph TD
    A[Inicio] --> B[Clonar Repositorio]
    B --> C[Configurar Entorno Virtual]
    C --> D[Instalar Dependencias]
    D --> E[Ejecutar src/main.py]
    E --> F{Autenticación API Exitosa?}
    F -->|Sí| G[Extracción de Datos Landsat]
    F -->|No| H[Registrar Error / Solicitar Credenciales]
    G --> I[Procesamiento / Limpieza de Datos]
    I --> J[Guardar en data/processed]
    J --> K[Fin]
    H --> K
```

## Guía de Clonación e Instalación

A continuación, se presentan las 3 opciones soportadas para la configuración del entorno local.

### 1. Opción Conda (Recomendada para Data Science)
Abre tu terminal de Anaconda/Miniconda y ejecuta:
\`\`\`bash
git clone https://github.com/TU_USUARIO/Landsat-project.git
cd "Landsat project"
conda create --name landsat_env python=3.10 -y
conda activate landsat_env
pip install -r requirements.txt
\`\`\`

### 2. Opción PowerShell (Usuarios Windows)
Abre PowerShell como administrador y ejecuta:
\`\`\`powershell
git clone https://github.com/TU_USUARIO/Landsat-project.git
cd "Landsat project"
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
\`\`\`
*(Nota: Si obtienes un error de políticas de ejecución, ejecuta `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser` previamente).*

### 3. Opción Pip / Terminal estándar (Linux/macOS)
Abre tu terminal Bash/Zsh y ejecuta:
\`\`\`bash
git clone https://github.com/TU_USUARIO/Landsat-project.git
cd "Landsat project"
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
\`\`\`
```

---
**[FIN DEL PROMPT DEL AGENTE]**
Al completar la generación de estos archivos, el agente debe ejecutar `git add .` y `git commit -m "Initial commit: Estructura base del Landsat project"`.