import os
import io
import pickle
from datetime import datetime
import time
import threading

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

import config
from logger_config import logger
import chatbot 

# Almacenamiento en memoria de archivos
file_memory_storage = {}

def authenticate():
    creds = None
    if os.path.exists(config.TOKEN_PICKLE):
        try:
            with open(config.TOKEN_PICKLE, 'rb') as token:
                creds = pickle.load(token)
        except (EOFError, pickle.UnpicklingError):
            logger.warning(f"Archivo {config.TOKEN_PICKLE} corrupto o vacío. Se requerirá nueva autenticación.")
            creds = None
            if os.path.exists(config.TOKEN_PICKLE):
                os.remove(config.TOKEN_PICKLE)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                logger.error(f"Error al refrescar token: {e}. Se requerirá nueva autenticación.")
                if os.path.exists(config.TOKEN_PICKLE):
                    os.remove(config.TOKEN_PICKLE)
                creds = None
        if not creds:
            if not os.path.exists(config.CREDENTIALS_FILE):
                logger.error(f"Error Crítico: Falta el archivo '{config.CREDENTIALS_FILE}'. Descárgalo desde Google Cloud Console.")
                return None
            try:
                flow = InstalledAppFlow.from_client_secrets_file(config.CREDENTIALS_FILE, config.SCOPES)
                creds = flow.run_local_server(port=0)
            except Exception as e:
                logger.error(f"Error durante el flujo de autenticación: {e}")
                return None

        try:
            with open(config.TOKEN_PICKLE, 'wb') as token:
                pickle.dump(creds, token)
        except Exception as e:
            logger.error(f"Error al guardar el archivo {config.TOKEN_PICKLE}: {e}")

    return creds

def load_state():
    if os.path.exists(config.STATE_FILE):
        try:
            with open(config.STATE_FILE, 'rb') as f:
                return pickle.load(f)
        except (EOFError, pickle.UnpicklingError):
            logger.warning(f"Archivo de estado ({config.STATE_FILE}) corrupto o vacío. Se creará uno nuevo.")
            return {}
        except Exception as e:
            logger.error(f"Error inesperado al cargar el estado desde {config.STATE_FILE}: {e}")
            return {}
    return {}

def save_state(state):
    try:
        with open(config.STATE_FILE, 'wb') as f:
            pickle.dump(state, f)
    except Exception as e:
        logger.error(f"Error al guardar el estado en {config.STATE_FILE}: {e}")

def download_file_to_memory(service, file_id, file_name):
    try:
        logger.info(f"Descargando archivo en memoria: {file_name} (ID: {file_id})")
        request = service.files().get_media(fileId=file_id)
        file_content = io.BytesIO()
        downloader = MediaIoBaseDownload(file_content, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        
        file_content.seek(0)
        file_memory_storage[file_name] = file_content
        logger.info(f"Archivo '{file_name}' descargado exitosamente en memoria")
        return True
    except HttpError as error:
        logger.error(f"Ocurrió un error de API al descargar {file_name}: {error}")
        return False
    except Exception as e:
        logger.error(f"Ocurrió un error inesperado al descargar {file_name}: {e}")
        return False

def get_all_folders_recursive(service, folder_id, visited_folders=None):
    if visited_folders is None:
        visited_folders = set()
    
    if folder_id in visited_folders:
        return []
    
    visited_folders.add(folder_id)
    all_folders = [folder_id]
    
    try:
        query = f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed = false"
        results = service.files().list(
            q=query,
            pageSize=100,
            fields="nextPageToken, files(id, name)"
        ).execute()
        
        subfolders = results.get('files', [])
        logger.info(f"Encontradas {len(subfolders)} subcarpetas en carpeta ID: {folder_id}")
        
        for subfolder in subfolders:
            subfolder_id = subfolder['id']
            subfolder_name = subfolder['name']
            logger.info(f"Explorando subcarpeta: {subfolder_name} (ID: {subfolder_id})")
            
            recursive_folders = get_all_folders_recursive(service, subfolder_id, visited_folders)
            all_folders.extend(recursive_folders)
            
    except Exception as e:
        logger.error(f"Error al obtener subcarpetas de {folder_id}: {e}")
    
    return all_folders

def check_drive_files(service, current_state):
    new_state = current_state.copy()
    updated_count = 0
    added_count = 0
    cambios = False

    try:
        logger.info(f"Verificando archivos en la carpeta de Drive ID: {config.GOOGLE_DRIVE_FOLDER_ID}")
        
        all_folder_ids = get_all_folders_recursive(service, config.GOOGLE_DRIVE_FOLDER_ID)
        logger.info(f"Total de carpetas para verificar (incluyendo subcarpetas): {len(all_folder_ids)}")
        
        all_items = []
        
        for folder_id in all_folder_ids:
            query = f"'{folder_id}' in parents and ("
            query += "mimeType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' or "
            query += "mimeType='application/pdf' or "
            query += "mimeType='application/vnd.openxmlformats-officedocument.wordprocessingml.document'"
            query += ") and trashed = false"
            
            results = service.files().list(
                q=query,
                pageSize=100,
                fields="nextPageToken, files(id, name, modifiedTime, mimeType, parents)"
            ).execute()
            
            folder_items = results.get('files', [])
            if folder_items:
                logger.info(f"Encontrados {len(folder_items)} archivos en carpeta ID: {folder_id}")
                all_items.extend(folder_items)

        if not all_items:
            logger.info(f"No se encontraron archivos (xlsx/pdf/docx) en la carpeta principal ni subcarpetas")
            ids_in_drive = set()
            drive_files_map = {}
        else:
            logger.info(f"Total de archivos encontrados en Drive: {len(all_items)}")
            drive_files_map = {
                item['id']: {
                    'name': item['name'], 
                    'modifiedTime': item['modifiedTime'],
                    'mimeType': item['mimeType'],
                    'parents': item.get('parents', [])
                } for item in all_items
            }
            ids_in_drive = set(drive_files_map.keys())

            for file_id, file_info in drive_files_map.items():
                file_name = file_info['name']
                drive_modified_time = file_info['modifiedTime']
                mime_type = file_info['mimeType']

                logger.debug(f"Archivo {file_name} tiene MIME type: {mime_type}")

                force_download = (len(current_state) == 0) or (file_name not in file_memory_storage)

                if force_download or file_id not in current_state or current_state[file_id] != drive_modified_time:
                    if force_download and file_name in file_memory_storage:
                         logger.info(f"Archivo ya existe en memoria, forzando descarga de: {file_name}")
                         updated_count += 1
                    elif force_download:
                        logger.info(f"Forzando descarga de nuevo archivo: {file_name}")
                        added_count += 1
                    elif file_id not in current_state:
                        logger.info(f"Archivo nuevo detectado: {file_name}")
                        added_count += 1
                    else:
                        logger.info(f"Archivo modificado detectado: {file_name}")
                        updated_count += 1

                    if download_file_to_memory(service, file_id, file_name):
                        new_state[file_id] = drive_modified_time
                        cambios = True
                        logger.info(f"Estado actualizado para archivo: {file_name}")
                    else:
                        logger.warning(f"Fallo al descargar {file_name}, se reintentará en el próximo ciclo.")
                        if file_id in new_state:
                            del new_state[file_id]

        ids_in_state = set(new_state.keys())
        ids_to_remove_from_state = ids_in_state - ids_in_drive
        if ids_to_remove_from_state:
            for id_to_remove in ids_to_remove_from_state:
                file_name = next((info['name'] for fid, info in drive_files_map.items() if fid == id_to_remove), None)
                if file_name and file_name in file_memory_storage:
                    del file_memory_storage[file_name]
                logger.info(f"Archivo con ID {id_to_remove} ya no está en Drive o fue eliminado. Eliminando del estado.")
                del new_state[id_to_remove]
                cambios = True

        return new_state, added_count, updated_count, cambios

    except Exception as e:
        logger.error(f'Ocurrió un error durante la verificación de Drive: {e}')
        return current_state, 0, 0, False

def monitoreo_drive():
    logger.info("Iniciando monitoreo de Google Drive...")
    creds = authenticate()
    if not creds:
        logger.error("Fallo en la autenticación de Google Drive. El monitoreo no puede continuar.")
        return

    try:
        service = build('drive', 'v3', credentials=creds)
        logger.info("Autenticación exitosa y servicio de Drive creado.")

        current_state = load_state()
        logger.info(f"Estado inicial cargado con {len(current_state)} archivos rastreados.")

        logger.info("Realizando verificación inicial de archivos en Drive...")
        new_state, added, updated, cambios = check_drive_files(service, current_state)

        if cambios:
            logger.info("Cambios detectados en la verificación inicial. Actualizando estado y documentos.")
            save_state(new_state)
            current_state = new_state
            logger.info(f"Resumen inicial: {added} archivos agregados, {updated} archivos actualizados.")
            chatbot.cargar_documentos()
            chatbot.docs_actualizados.set()
        else:
            logger.info("No se detectaron cambios respecto al estado guardado. Cargando documentos existentes...")
            chatbot.cargar_documentos()
        while True:
            logger.info(f"Esperando {config.CHECK_INTERVAL_SECONDS} segundos para la próxima verificación de Drive...")
            time.sleep(config.CHECK_INTERVAL_SECONDS)

            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            logger.info(f"Verificando cambios en Drive ({now})")
            try:
                new_state, added, updated, cambios = check_drive_files(service, current_state)

                if cambios:
                    logger.info("Se detectaron cambios en los archivos. Actualizando estado y recargando documentos...")
                    save_state(new_state)
                    current_state = new_state
                    logger.info(f"Resumen: {added} archivos agregados, {updated} archivos actualizados.")
                    chatbot.cargar_documentos()
                    chatbot.docs_actualizados.set()
                else:
                    logger.info("No se detectaron cambios en los archivos.")

            except Exception as e:
                logger.error(f"Error durante el ciclo de verificación de Drive: {e}. Reintentando en el próximo ciclo.")
                time.sleep(60) 

    except HttpError as error:
        logger.error(f"Error de API de Google Drive durante el monitoreo: {error}")
    except Exception as e:
        logger.error(f"Error crítico en el monitoreo de Drive: {e}")
    finally:
        if 'current_state' in locals():
            save_state(current_state)
            logger.info("Estado final guardado antes de terminar el monitoreo.") 