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
