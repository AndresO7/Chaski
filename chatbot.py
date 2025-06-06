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
from llm_pool import llm_pool

# Estado global del chatbot
docs_string = ""
system_prompt = ""
ultimo_update = 0
docs_actualizados = threading.Event()
llm = None
conversaciones = {}  # Historiales por usuario - OPTIMIZADO para memoria
conversaciones_lock = threading.Lock()  # Lock para conversaciones thread-safe

# Configuración de memoria optimizada
MAX_MESSAGES_PER_USER = 5  # Solo 5 mensajes por usuario antes de reset
MAX_CONCURRENT_USERS = 25  # Máximo de usuarios concurrentes
USER_CLEANUP_INTERVAL = 600  # Limpiar usuarios inactivos cada 10 minutos

def limpiar_memoria_usuario(user_id: str):
    """Limpiar memoria de un usuario específico"""
    with conversaciones_lock:
        if user_id in conversaciones:
            del conversaciones[user_id]
            logger.info(f"🧹 Memoria limpiada para usuario {user_id}")

def limpiar_memoria_global():
    """Limpiar memoria global periódicamente"""
    current_time = time.time()
    usuarios_a_limpiar = []
    
    with conversaciones_lock:
        for user_id, historial in conversaciones.items():
            # Si un usuario tiene más de MAX_MESSAGES_PER_USER mensajes, limpiar
            if len(historial) > MAX_MESSAGES_PER_USER:
                usuarios_a_limpiar.append(user_id)
            # También limpiar usuarios que no han interactuado en la última hora
            elif historial and hasattr(historial[-1], 'timestamp'):
                last_interaction = getattr(historial[-1], 'timestamp', current_time)
                if current_time - last_interaction > 3600:  # 1 hora
                    usuarios_a_limpiar.append(user_id)
    
    for user_id in usuarios_a_limpiar:
        limpiar_memoria_usuario(user_id)
    
    # Si hay demasiados usuarios, limpiar los más antiguos
    with conversaciones_lock:
        if len(conversaciones) > MAX_CONCURRENT_USERS:
            usuarios_ordenados = sorted(conversaciones.items(), 
                                       key=lambda x: len(x[1]))  # Por cantidad de mensajes
            usuarios_excedentes = usuarios_ordenados[:-MAX_CONCURRENT_USERS]
            for user_id, _ in usuarios_excedentes:
                del conversaciones[user_id]
                logger.info(f"🧹 Usuario {user_id} removido por límite de concurrencia")

def inicializar_limpieza_memoria():
    """Inicializar thread de limpieza de memoria"""
    def cleanup_worker():
        while True:
            time.sleep(USER_CLEANUP_INTERVAL)
            try:
                limpiar_memoria_global()
                stats = llm_pool.get_stats()
                logger.info(f"📊 Pool Stats: {stats}")
                logger.info(f"💾 Usuarios en memoria: {len(conversaciones)}")
            except Exception as e:
                logger.error(f"Error en limpieza de memoria: {e}")
    
    cleanup_thread = threading.Thread(target=cleanup_worker, daemon=True)
    cleanup_thread.start()
    logger.info("🧹 Sistema de limpieza de memoria iniciado")

def cargar_documentos(force_reload=True):
    global docs_string, system_prompt, ultimo_update

    logger.info(f"🔄 Cargando documentos (force_reload={force_reload})")
    
    # Evitar recargas innecesarias si no hay cambios
    if not force_reload and docs_string and system_prompt and time.time() - ultimo_update < 60:
        logger.info("⏭️ Documentos cargados recientemente, saltando recarga")
        return

    # Procesar solo archivos XLSX desde memoria para system prompt
    archivos_xlsx = [f for f in file_memory_storage.keys() if f.lower().endswith('.xlsx')] if file_memory_storage else []
    
    logger.info(f"📊 Archivos en memoria total: {len(file_memory_storage) if file_memory_storage else 0}")
    logger.info(f"📋 Archivos XLSX para system prompt: {len(archivos_xlsx)}")
    
    if not archivos_xlsx:
        logger.info("ℹ️ No hay archivos XLSX en memoria, usando solo prompt base")
        docs_string = ""
        system_prompt = prompt_base + "\n"
        ultimo_update = time.time()
    else:

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
            logger.debug(f"Muestra contenido XLSX: {docs_string[:200]}...")
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
        logger.info("🤖 Iniciando actualización del sistema RAG...")
        
        # Verificar si hay archivos PDF/DOCX en el directorio
        pdf_docx_files = []
        if os.path.exists(config.DOWNLOAD_PATH):
            for file in os.listdir(config.DOWNLOAD_PATH):
                if file.lower().endswith(('.pdf', '.docx', '.doc')):
                    pdf_docx_files.append(file)
        
        logger.info(f"📁 Archivos PDF/DOCX en directorio: {len(pdf_docx_files)}")
        
        if rag_system.update_vectorstore(config.DOWNLOAD_PATH):
            logger.info("✅ Sistema RAG actualizado exitosamente")
        else:
            logger.warning("⚠️ No se pudo actualizar el sistema RAG")
    else:
        logger.warning("❌ Sistema RAG no está inicializado correctamente")

    # Inicializar sistema de limpieza si no está iniciado
    if not hasattr(cargar_documentos, '_cleanup_iniciado'):
        inicializar_limpieza_memoria()
        cargar_documentos._cleanup_iniciado = True

def inicializar_llm():
    global llm
    logger.info("NOTA: Usando LLM Pool en lugar de instancia única")
    # Ya no necesitamos una instancia única, usamos el pool

def generar_respuesta(pregunta, historial_mensajes, user_id: str):
    """Generar respuesta usando el pool de LLMs con optimización de memoria"""
    
    if docs_actualizados.is_set():
        logger.info("Esperando actualización de documentos...")
        docs_actualizados.wait()
        logger.info("Actualización de documentos completada.")

    logger.info(f"👤 Generando respuesta para usuario {user_id}: {pregunta[:50]}...")

    # Obtener contexto relevante del RAG si está disponible
    rag_context = ""
    if rag_system.is_initialized():
        try:
            rag_context = rag_system.get_context_from_query(pregunta, k=5)
            if rag_context:
                logger.info(f"📚 Contexto RAG obtenido: {len(rag_context)} caracteres")
            else:
                logger.info("ℹ️ No se encontró contexto relevante en RAG")
        except Exception as e:
            logger.error(f"Error al obtener contexto RAG: {e}")
            rag_context = ""
    else:
        logger.warning("⚠️ Sistema RAG no inicializado, solo usando context XLSX")

    # Optimización de memoria: limitar historial automáticamente
    historial_limitado = historial_mensajes[-MAX_MESSAGES_PER_USER*2:] if len(historial_mensajes) > MAX_MESSAGES_PER_USER*2 else historial_mensajes

    history_text = ""
    for msg in historial_limitado:
        role = "Usuario" if msg["role"] == "user" else "Asistente"
        history_text += f"{role}: {msg['content']}\n"

    # Construir prompt con contexto RAG adicional si está disponible
    if rag_context:
        full_prompt = f"{system_prompt}\n\n# Contexto adicional de documentos PDF/DOCX:\n{rag_context}\n\n{history_text}Usuario: {pregunta}\nAsistente:"
    else:
        full_prompt = f"{system_prompt}\n{history_text}Usuario: {pregunta}\nAsistente:"

    # Obtener LLM del pool
    llm = llm_pool.get_llm(user_id, timeout=45.0)
    if llm is None:
        return "Lo siento, el servicio está temporalmente saturado. Por favor, intenta en unos momentos."

    try:
        # Usar el método de retry del pool
        respuesta = llm_pool.invoke_with_retry(llm, full_prompt, user_id)
        return respuesta
    finally:
        # Liberar LLM inmediatamente después de cada respuesta para mejor concurrencia
        llm_pool.release_llm(user_id, llm)
        logger.debug(f"🔄 LLM liberado para usuario {user_id} después de respuesta")

def limitar_historial(historial):
    """Limitar historial a MAX_MESSAGES_PER_USER para optimizar memoria"""
    if len(historial) > MAX_MESSAGES_PER_USER * 2:  # *2 porque son pares usuario-asistente
        logger.debug(f"Historial excedió {MAX_MESSAGES_PER_USER * 2} mensajes, truncando.")
        return historial[-MAX_MESSAGES_PER_USER * 2:]
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