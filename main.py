import os
import threading
import sys
import config
from logger_config import logger
from chatbot import cargar_documentos
from google_drive import monitoreo_drive
from slack_app import start_slack_app

if __name__ == "__main__":
    logger.info("Iniciando Chaski Bot...")

    if not os.path.exists(config.DOWNLOAD_PATH):
        try:
            os.makedirs(config.DOWNLOAD_PATH, exist_ok=True)
            logger.info(f"Directorio de descargas creado: {config.DOWNLOAD_PATH}")
        except OSError as e:
            logger.error(f"No se pudo crear directorio {config.DOWNLOAD_PATH}: {e}. Funcionalidad limitada.")

    logger.info("Cargando documentos iniciales...")
    cargar_documentos()

    logger.info("Iniciando monitoreo de Google Drive...")
    drive_thread = threading.Thread(target=monitoreo_drive, daemon=True)
    drive_thread.start()

    logger.info("Iniciando conexión con Slack...")
    start_slack_app()

    logger.info("Chaski Bot detenido.")