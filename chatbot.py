import os
import glob
import time
import threading
import re

from langchain_community.document_loaders import UnstructuredExcelLoader
from langchain_google_vertexai import VertexAI

import config
from logger_config import logger
from prompts import prompt_base

# Estado global del chatbot
docs_string = ""
system_prompt = ""
ultimo_update = 0
docs_actualizados = threading.Event()
llm = None
conversaciones = {} # Historiales por usuario
llm_mutex = threading.Lock() # Mutex para acceso concurrente al LLM

def cargar_documentos():
    global docs_string, system_prompt, ultimo_update

    logger.info(f"Cargando documentos Excel desde: {config.DOWNLOAD_PATH}")

    if not os.path.exists(config.DOWNLOAD_PATH):
        logger.warning(f"El directorio de descargas {config.DOWNLOAD_PATH} no existe. Creándolo.")
        try:
            os.makedirs(config.DOWNLOAD_PATH, exist_ok=True)
        except OSError as e:
            logger.error(f"No se pudo crear el directorio de descargas {config.DOWNLOAD_PATH}: {e}")
            docs_string = ""
            system_prompt = prompt_base + "\n"
            ultimo_update = time.time()
            logger.warning("Usando prompt base sin documentos adicionales.")
            inicializar_llm()
            return

    archivos_excel = glob.glob(os.path.join(config.DOWNLOAD_PATH, "*.xlsx"))
    logger.info(f"Archivos Excel encontrados: {len(archivos_excel)}")

    if not archivos_excel:
        logger.warning(f"No se encontraron archivos Excel en {config.DOWNLOAD_PATH}.")
        docs_string = ""
        system_prompt = prompt_base + "\n"
        ultimo_update = time.time()
        logger.info("Usando prompt base sin documentos adicionales.")
        inicializar_llm()
        return

    todos_docs = []
    for archivo in archivos_excel:
        try:
            logger.info(f"Procesando archivo: {archivo}")
            loader = UnstructuredExcelLoader(archivo, mode="elements")
            docs = loader.load()
            todos_docs.extend(docs)
            logger.info(f"Archivo {os.path.basename(archivo)} cargado con {len(docs)} elementos.")
        except Exception as e:
            logger.error(f"Error al cargar o procesar el archivo {archivo}: {e}")

    if todos_docs:
        docs_string = "\n".join([doc.page_content for doc in todos_docs])
        system_prompt = prompt_base + docs_string + "\n"
        ultimo_update = time.time()
        logger.info(f"Documentos cargados: {len(todos_docs)} elementos. Longitud texto: {len(docs_string)} chars.")
        logger.debug(f"Muestra contenido: {docs_string[:200]}...") # Muestra para depuración
    else:
        logger.warning("Se encontraron archivos Excel pero no se pudo extraer contenido.")
        docs_string = ""
        system_prompt = prompt_base + "\n"
        ultimo_update = time.time()
        logger.info("Usando prompt base sin documentos adicionales.")

    inicializar_llm() 

def inicializar_llm():
    global llm

    logger.info("Inicializando/Actualizando modelo LLM...")

    try:
        llm = VertexAI(
            model=config.MODEL_NAME,
            temperature=config.LLM_TEMPERATURE,
            project="chaski-457917"
        )
        logger.info(f"Modelo LLM ({config.MODEL_NAME}) inicializado/actualizado.")
    except Exception as e:
        logger.error(f"Error al inicializar el modelo LLM: {e}")
        llm = None 

def generar_respuesta(pregunta, historial_mensajes):
    global llm

    if docs_actualizados.is_set():
        logger.info("Esperando actualización de documentos...")
        docs_actualizados.wait() 
        logger.info("Actualización de documentos completada.")

    if llm is None:
        logger.error("LLM no inicializado al intentar generar respuesta.")
        inicializar_llm()
        if llm is None:
            return "Lo siento, el asistente no está disponible temporalmente."

    logger.info(f"Generando respuesta para: {pregunta[:50]}...")

    
    history_text = ""
    for msg in historial_mensajes:
        role = "Usuario" if msg["role"] == "user" else "Asistente"
        history_text += f"{role}: {msg['content']}\n"
 
    full_prompt = f"{system_prompt}\n{history_text}Usuario: {pregunta}\nAsistente:"
    try:
        with llm_mutex:
            logger.debug(f"Enviando prompt al LLM (longitud: {len(full_prompt)}) ...")
            respuesta = llm.invoke(full_prompt)
            logger.info(f"Respuesta recibida del LLM: {respuesta[:50]}...")
        return respuesta
    except Exception as e:
        logger.error(f"Error al invocar el LLM: {e}")
        # Manejar error específico de tamaño de payload
        if "400" in str(e) and "request payload size" in str(e).lower():
             logger.error("El tamaño del prompt excedió el límite del modelo.")
             return "Lo siento, la conversación es demasiado larga. Intenta empezar de nuevo o simplificar la pregunta."
        return "Lo siento, ocurrió un error inesperado."

def limitar_historial(historial):
    if len(historial) > config.HISTORY_MAX_MESSAGES:
        logger.debug(f"Historial excedió {config.HISTORY_MAX_MESSAGES} mensajes, truncando.")
        return historial[-config.HISTORY_MAX_MESSAGES:]
    return historial

def convertir_a_slack_markdown(texto):
    texto = re.sub(r'\*\*(.*?)\*\*', r'*\1*', texto) 
    texto = re.sub(r'^\* (.*)$', r'• \1', texto, flags=re.MULTILINE)
    texto = re.sub(r'^- (.*)$', r'• \1', texto, flags=re.MULTILINE) 
    return texto

def dividir_mensaje(texto, max_length=config.SLACK_MAX_MESSAGE_LENGTH):
    if len(texto) <= max_length:
        return [texto]

    partes = []
    texto_restante = texto
    while len(texto_restante) > max_length:
        punto_corte = -1
        corte_parrafo = texto_restante.rfind('\n\n', 0, max_length)
        if corte_parrafo != -1:
            punto_corte = corte_parrafo
        else:
            corte_linea = texto_restante.rfind('\n', 0, max_length)
            if corte_linea != -1:
                punto_corte = corte_linea
            else:

                corte_espacio = texto_restante.rfind(' ', 0, max_length)
                if corte_espacio != -1:
                    punto_corte = corte_espacio
                else:
                    punto_corte = max_length

        partes.append(texto_restante[:punto_corte].strip())
        texto_restante = texto_restante[punto_corte:].strip()
        if partes and texto_restante:
             partes[-1] += "\n\n_()_"

    if texto_restante:
        partes.append(texto_restante)

    logger.info(f"Mensaje dividido en {len(partes)} partes.")
    return partes 