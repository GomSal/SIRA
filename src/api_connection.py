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
