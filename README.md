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
```bash
git clone https://github.com/TU_USUARIO/Landsat-project.git
cd "Landsat project"
conda create --name landsat_env python=3.10 -y
conda activate landsat_env
pip install -r requirements.txt
```

### 2. Opción PowerShell (Usuarios Windows)
Abre PowerShell como administrador y ejecuta:
```powershell
git clone https://github.com/TU_USUARIO/Landsat-project.git
cd "Landsat project"
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
*(Nota: Si obtienes un error de políticas de ejecución, ejecuta `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser` previamente).*

### 3. Opción Pip / Terminal estándar (Linux/macOS)
Abre tu terminal Bash/Zsh y ejecuta:
```bash
git clone https://github.com/TU_USUARIO/Landsat-project.git
cd "Landsat project"
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```
