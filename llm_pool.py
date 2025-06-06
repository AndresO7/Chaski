import threading
import time
import queue
from typing import Optional
from langchain_google_genai import ChatGoogleGenerativeAI

import config
from logger_config import logger

class LLMPool:
    def __init__(self, pool_size: int = 5, max_retries: int = 3):
        self.pool_size = pool_size
        self.max_retries = max_retries
        self.pool = queue.Queue(maxsize=pool_size)
        self.pool_lock = threading.Lock()
        self.active_connections = {}  # user_id -> (llm_instance, timestamp)
        self.connection_timeout = 120  # 2 minutos (reducido)
        self.use_session_persistence = False  # Deshabilitar persistencia para mejor concurrencia
        
        # Inicializar pool de LLMs
        self._initialize_pool()
        
        # Thread para limpieza periódica
        self.cleanup_thread = threading.Thread(target=self._cleanup_connections, daemon=True)
        self.cleanup_thread.start()
        
    def _initialize_pool(self):
        """Inicializar el pool con instancias LLM"""
        logger.info(f"Inicializando pool de LLMs con {self.pool_size} instancias...")
        
        for i in range(self.pool_size):
            try:
                llm = ChatGoogleGenerativeAI(
                    model=config.MODEL_NAME,
                    temperature=config.LLM_TEMPERATURE,
                )
                self.pool.put(llm)
                logger.info(f"LLM {i+1}/{self.pool_size} inicializado en pool")
            except Exception as e:
                logger.error(f"Error al inicializar LLM {i+1}: {e}")
        
        logger.info(f"Pool de LLMs inicializado con {self.pool.qsize()} instancias disponibles")
    
    def get_llm(self, user_id: str, timeout: float = 45.0) -> Optional[ChatGoogleGenerativeAI]:
        """Obtener una instancia LLM del pool para un usuario"""
        start_time = time.time()
        
        # Si la persistencia está deshabilitada, siempre obtener del pool
        if not self.use_session_persistence:
            try:
                logger.info(f"🔄 Usuario {user_id} solicitando LLM del pool. Disponibles: {self.pool.qsize()}")
                llm = self.pool.get(timeout=timeout)
                
                elapsed = time.time() - start_time
                logger.info(f"✅ LLM asignado a usuario {user_id} en {elapsed:.2f}s. Pool restante: {self.pool.qsize()}")
                return llm
                
            except queue.Empty:
                elapsed = time.time() - start_time
                logger.warning(f"⏱️ Timeout obteniendo LLM para usuario {user_id} después de {elapsed:.2f}s")
                return None
        
        # Código legacy para persistencia (si se habilita)
        # Verificar si el usuario ya tiene una conexión activa
        with self.pool_lock:
            if user_id in self.active_connections:
                llm, timestamp = self.active_connections[user_id]
                # Verificar si la conexión no ha expirado
                if time.time() - timestamp < self.connection_timeout:
                    logger.debug(f"Reutilizando conexión LLM existente para usuario {user_id}")
                    # Actualizar timestamp
                    self.active_connections[user_id] = (llm, time.time())
                    return llm
                else:
                    # Conexión expirada, remover
                    del self.active_connections[user_id]
                    self.pool.put(llm)
                    logger.info(f"Conexión LLM expirada para usuario {user_id}, devolviendo al pool")
        
        # Intentar obtener una nueva instancia del pool
        try:
            logger.info(f"🔄 Usuario {user_id} solicitando LLM del pool. Disponibles: {self.pool.qsize()}")
            llm = self.pool.get(timeout=timeout)
            
            # Asignar al usuario
            with self.pool_lock:
                self.active_connections[user_id] = (llm, time.time())
            
            elapsed = time.time() - start_time
            logger.info(f"✅ LLM asignado a usuario {user_id} en {elapsed:.2f}s. Pool restante: {self.pool.qsize()}")
            return llm
            
        except queue.Empty:
            elapsed = time.time() - start_time
            logger.warning(f"⏱️ Timeout obteniendo LLM para usuario {user_id} después de {elapsed:.2f}s")
            return None
    
    def release_llm(self, user_id: str, llm_instance: ChatGoogleGenerativeAI = None):
        """Liberar la instancia LLM de un usuario de vuelta al pool"""
        
        # Si no se usa persistencia, liberar directamente la instancia
        if not self.use_session_persistence and llm_instance:
            try:
                self.pool.put_nowait(llm_instance)
                logger.info(f"🔄 LLM liberado de usuario {user_id} y devuelto al pool. Disponibles: {self.pool.qsize()}")
                return
            except queue.Full:
                logger.warning(f"Pool lleno, no se pudo devolver LLM de usuario {user_id}")
                return
        
        # Código legacy para persistencia
        with self.pool_lock:
            if user_id in self.active_connections:
                llm, _ = self.active_connections[user_id]
                del self.active_connections[user_id]
                
                # Devolver al pool si hay espacio
                try:
                    self.pool.put_nowait(llm)
                    logger.info(f"🔄 LLM liberado de usuario {user_id} y devuelto al pool. Disponibles: {self.pool.qsize()}")
                except queue.Full:
                    logger.warning(f"Pool lleno, no se pudo devolver LLM de usuario {user_id}")
            else:
                logger.debug(f"Usuario {user_id} no tenía LLM asignado")
    
    def _cleanup_connections(self):
        """Limpiar conexiones expiradas periódicamente"""
        while True:
            time.sleep(60)  # Verificar cada minuto
            current_time = time.time()
            expired_users = []
            
            with self.pool_lock:
                for user_id, (llm, timestamp) in self.active_connections.items():
                    if current_time - timestamp > self.connection_timeout:
                        expired_users.append(user_id)
                
                # Limpiar conexiones expiradas
                for user_id in expired_users:
                    llm, _ = self.active_connections[user_id]
                    del self.active_connections[user_id]
                    try:
                        self.pool.put_nowait(llm)
                        logger.info(f"🧹 Conexión LLM limpiada para usuario inactivo {user_id}")
                    except queue.Full:
                        logger.warning(f"Pool lleno durante limpieza de usuario {user_id}")
    
    def get_stats(self) -> dict:
        """Obtener estadísticas del pool"""
        with self.pool_lock:
            return {
                'pool_size': self.pool_size,
                'available': self.pool.qsize(),
                'active_connections': len(self.active_connections),
                'active_users': list(self.active_connections.keys())
            }
    
    def invoke_with_retry(self, llm: ChatGoogleGenerativeAI, prompt: str, user_id: str) -> str:
        """Invocar LLM con reintentos y manejo de errores específicos"""
        for attempt in range(self.max_retries):
            try:
                logger.debug(f"🚀 Invocando LLM para usuario {user_id} (intento {attempt + 1}/{self.max_retries})")
                response = llm.invoke(prompt)
                logger.info(f"✅ Respuesta LLM exitosa para usuario {user_id}: {response.content[:50]}...")
                return response.content
                
            except Exception as e:
                error_str = str(e)
                logger.error(f"❌ Error LLM para usuario {user_id} (intento {attempt + 1}): {error_str}")
                
                # Detectar errores 429 (rate limit)
                if "429" in error_str or "quota" in error_str.lower() or "ResourceExhausted" in error_str:
                    if attempt < self.max_retries - 1:
                        wait_time = (attempt + 1) * 5  # 5, 10, 15 segundos
                        logger.warning(f"⏱️ Error 429 para usuario {user_id}, esperando {wait_time}s antes de reintentar...")
                        time.sleep(wait_time)
                        continue
                    else:
                        return "Lo siento, el servicio está temporalmente saturado. Por favor, intenta en unos minutos."
                
                # Detectar errores de tamaño de payload
                elif "400" in error_str and "request payload size" in error_str.lower():
                    return "Lo siento, tu consulta es demasiado larga. Por favor, intenta con una pregunta más específica."
                
                # Para otros errores, no reintentar
                else:
                    return f"Lo siento, ocurrió un error inesperado. Por favor, intenta nuevamente."
        
        return "Lo siento, no se pudo procesar tu solicitud después de varios intentos."

# Instancia global del pool
llm_pool = LLMPool(pool_size=15)  # 15 instancias para manejar mejor la concurrencia 