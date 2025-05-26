import os
import time
import threading
import re
import mimetypes

from langchain_community.document_loaders import UnstructuredFileIOLoader
from langchain_google_genai import ChatGoogleGenerativeAI


import config
from logger_config import logger
from prompts import prompt_base
from google_drive import file_memory_storage

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

    logger.info("Cargando documentos desde memoria")

    if not file_memory_storage:
        logger.warning("No hay archivos en memoria para cargar.")
        docs_string = ""
        system_prompt = prompt_base + "\n"
        ultimo_update = time.time()
        logger.warning("Usando prompt base sin documentos adicionales.")
        inicializar_llm()
        return

    archivos = list(file_memory_storage.keys())
    logger.info(f"Archivos encontrados en memoria: {len(archivos)}")

    todos_docs = []
    for archivo_nombre in archivos:
        try:
            archivo_contenido = file_memory_storage[archivo_nombre]
            extension = os.path.splitext(archivo_nombre)[1].lower()
            
            logger.info(f"Procesando archivo en memoria: {archivo_nombre} (extensión: {extension})")
            
            # Reiniciar el puntero al inicio del archivo en memoria
            archivo_contenido.seek(0)
            
            # Usar UnstructuredFileIOLoader para trabajar con archivos en memoria
            # El modo 'elements' permite extraer elementos estructurados del documento
            loader = UnstructuredFileIOLoader(
                archivo_contenido, 
                mode="elements"
            )
            
            try:
                docs = loader.load()
                todos_docs.extend(docs)
                logger.info(f"Archivo {archivo_nombre} cargado con {len(docs)} elementos.")
            except Exception as e:
                logger.error(f"Error durante la carga de {archivo_nombre}: {e}")
                # Si falla el primer intento, intentamos de nuevo con diferentes configuraciones
                try:
                    archivo_contenido.seek(0)
                    loader = UnstructuredFileIOLoader(
                        archivo_contenido,
                        mode="single"
                    )
                    docs = loader.load()
                    todos_docs.extend(docs)
                    logger.info(f"Segundo intento exitoso para {archivo_nombre}: {len(docs)} elementos.")
                except Exception as e2:
                    logger.error(f"Error en segundo intento para {archivo_nombre}: {e2}")
            
        except Exception as e:
            logger.error(f"Error al procesar el archivo {archivo_nombre}: {e}")

    if todos_docs:
        docs_string = "\n".join([doc.page_content for doc in todos_docs])
        system_prompt = prompt_base + docs_string + "\n"
        ultimo_update = time.time()
        logger.info(f"Documentos cargados: {len(todos_docs)} elementos. Longitud texto: {len(docs_string)} chars.")
        logger.debug(f"Muestra contenido: {docs_string[:200]}...") # Muestra para depuración
    else:
        logger.warning("Se encontraron archivos pero no se pudo extraer contenido.")
        docs_string = ""
        system_prompt = prompt_base + "\n"
        ultimo_update = time.time()
        logger.info("Usando prompt base sin documentos adicionales.")

    inicializar_llm() 

def inicializar_llm():
    global llm

    logger.info("Inicializando/Actualizando modelo LLM...")

    try:
        llm = ChatGoogleGenerativeAI(
            model=config.MODEL_NAME,
            temperature=config.LLM_TEMPERATURE,
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
            logger.info(f"Respuesta recibida del LLM: {respuesta.content[:50]}...")
        return respuesta.content
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