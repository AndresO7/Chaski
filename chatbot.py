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
from rag_system import rag_system

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

    logger.info("Cargando documentos desde memoria y actualizando RAG")

    # Procesar solo archivos XLSX desde memoria para system prompt
    if not file_memory_storage:
        logger.warning("No hay archivos XLSX en memoria para cargar.")
        docs_string = ""
        system_prompt = prompt_base + "\n"
        ultimo_update = time.time()
        logger.warning("Usando prompt base sin documentos XLSX adicionales.")
    else:
        archivos_xlsx = [f for f in file_memory_storage.keys() if f.lower().endswith('.xlsx')]
        logger.info(f"Archivos XLSX encontrados en memoria: {len(archivos_xlsx)}")

        todos_docs = []
        for archivo_nombre in archivos_xlsx:
            try:
                archivo_contenido = file_memory_storage[archivo_nombre]
                extension = os.path.splitext(archivo_nombre)[1].lower()
                
                logger.info(f"Procesando archivo XLSX en memoria: {archivo_nombre}")
                
                # Reiniciar el puntero al inicio del archivo en memoria
                archivo_contenido.seek(0)
                
                # Usar UnstructuredFileIOLoader para trabajar con archivos en memoria
                loader = UnstructuredFileIOLoader(
                    archivo_contenido, 
                    mode="elements"
                )
                
                try:
                    docs = loader.load()
                    todos_docs.extend(docs)
                    logger.info(f"Archivo XLSX {archivo_nombre} cargado con {len(docs)} elementos.")
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
                logger.error(f"Error al procesar el archivo XLSX {archivo_nombre}: {e}")

        if todos_docs:
            docs_string = "\n".join([doc.page_content for doc in todos_docs])
            system_prompt = prompt_base + docs_string + "\n"
            ultimo_update = time.time()
            logger.info(f"Documentos XLSX cargados: {len(todos_docs)} elementos. Longitud texto: {len(docs_string)} chars.")
            logger.debug(f"Muestra contenido XLSX: {docs_string[:200]}...") # Muestra para depuración
        else:
            logger.warning("Se encontraron archivos XLSX pero no se pudo extraer contenido.")
            docs_string = ""
            system_prompt = prompt_base + "\n"
            ultimo_update = time.time()
            logger.info("Usando prompt base sin documentos XLSX adicionales.")

    # Guardar system prompt en archivo para verificación
    try:
        with open("system_prompt_verificacion.txt", "w", encoding="utf-8") as f:
            f.write("="*80 + "\n")
            f.write("SYSTEM PROMPT - VERIFICACIÓN\n")
            f.write(f"Generado: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("="*80 + "\n\n")
            f.write("NOTA: Este archivo debe contener SOLO contenido de archivos XLSX.\n")
            f.write("Los archivos PDF/DOCX deben procesarse por RAG, no aparecer aquí.\n")
            f.write("="*80 + "\n\n")
            f.write(system_prompt)
        
        logger.info("System prompt guardado en 'system_prompt_verificacion.txt' para verificación")
        print(f"✅ System prompt guardado en: system_prompt_verificacion.txt")
        print(f"📊 Longitud total: {len(system_prompt)} caracteres")
        print(f"📁 Archivos XLSX procesados: {len(archivos_xlsx) if 'archivos_xlsx' in locals() else 0}")
        
    except Exception as e:
        logger.error(f"Error al guardar system prompt en archivo: {e}")

    # Actualizar el sistema RAG con archivos PDF/DOCX del directorio de descarga
    if rag_system.is_initialized():
        logger.info("Actualizando sistema RAG con documentos PDF/DOCX...")
        if rag_system.update_vectorstore(config.DOWNLOAD_PATH):
            logger.info("Sistema RAG actualizado exitosamente")
        else:
            logger.warning("No se pudo actualizar el sistema RAG")
    else:
        logger.warning("Sistema RAG no está inicializado correctamente")

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

    # Obtener contexto relevante del RAG si está disponible
    rag_context = ""
    if rag_system.is_initialized():
        try:
            rag_context = rag_system.get_context_from_query(pregunta, k=5)
            if rag_context:
                logger.info(f"Contexto RAG obtenido: {len(rag_context)} caracteres")
            else:
                logger.info("No se encontró contexto relevante en RAG")
        except Exception as e:
            logger.error(f"Error al obtener contexto RAG: {e}")
            rag_context = ""
    else:
        logger.warning("Sistema RAG no inicializado, solo usando context XLSX")

    history_text = ""
    for msg in historial_mensajes:
        role = "Usuario" if msg["role"] == "user" else "Asistente"
        history_text += f"{role}: {msg['content']}\n"
 
    # Construir prompt con contexto RAG adicional si está disponible
    if rag_context:
        full_prompt = f"{system_prompt}\n\n# Contexto adicional de documentos PDF/DOCX:\n{rag_context}\n\n{history_text}Usuario: {pregunta}\nAsistente:"
    else:
        full_prompt = f"{system_prompt}\n{history_text}Usuario: {pregunta}\nAsistente:"
    
    # Intentar generar respuesta con reinicio automático en caso de error 429
    max_retries = 3
    for attempt in range(max_retries):
        try:
            with llm_mutex:
                logger.debug(f"Enviando prompt al LLM (longitud: {len(full_prompt)}) ...")
                respuesta = llm.invoke(full_prompt)
                logger.info(f"Respuesta recibida del LLM: {respuesta.content[:50]}...")
            return respuesta.content
        except Exception as e:
            logger.error(f"Error al invocar el LLM: {e}")
            
            # Detectar específicamente error 429
            if "429" in str(e) or "quota" in str(e).lower() or "ResourceExhausted" in str(e):
                logger.warning(f"Error 429 detectado (intento {attempt + 1}/{max_retries}). Reiniciando modelo LLM...")
                
                # Reiniciar el modelo LLM
                llm = None
                time.sleep(2)  # Esperar 2 segundos antes de reinicializar
                inicializar_llm()
                
                if llm is None:
                    return "Lo siento, el asistente no está disponible temporalmente debido a límites de cuota."
                
                # Si no es el último intento, continuar con el bucle
                if attempt < max_retries - 1:
                    logger.info("Modelo LLM reiniciado. Reintentando solicitud...")
                    time.sleep(3)  # Esperar un poco más antes de reintentar
                    continue
                else:
                    return "Lo siento, se excedió el límite de solicitudes. Por favor, intenta nuevamente en unos minutos."
            
            # Manejar error específico de tamaño de payload
            elif "400" in str(e) and "request payload size" in str(e).lower():
                logger.error("El tamaño del prompt excedió el límite del modelo.")
                return "Lo siento, la conversación es demasiado larga. Intenta empezar de nuevo o simplificar la pregunta."
            else:
                # Para otros errores, no reintentar
                return "Lo siento, ocurrió un error inesperado."
    
    # Si llegamos aquí, se agotaron todos los reintentos
    return "Lo siento, no se pudo procesar tu solicitud después de varios intentos. Por favor, intenta más tarde."

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