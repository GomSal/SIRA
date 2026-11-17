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
