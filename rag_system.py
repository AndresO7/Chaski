import os
import time
from typing import List
from langchain_community.document_loaders import UnstructuredPDFLoader, UnstructuredWordDocumentLoader
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_chroma import Chroma
from langchain_core.documents import Document

import config
from logger_config import logger

class RAGSystem:
    def __init__(self):
        self.embeddings = None
        self.vectorstore = None
        self.retriever = None
        self.last_update = 0
        self.initialize_embeddings()
        self.initialize_vectorstore()

    def initialize_embeddings(self):
        """Inicializar los embeddings de Google GenAI"""
        try:
            self.embeddings = GoogleGenerativeAIEmbeddings(
                model="models/text-embedding-004",
                google_api_key=config.GOOGLE_API_KEY
            )
            logger.info("Embeddings de Google GenAI inicializados correctamente")
        except Exception as e:
            logger.error(f"Error al inicializar embeddings: {e}")
            self.embeddings = None

    def initialize_vectorstore(self):
        """Inicializar ChromaDB vectorstore"""
        try:
            if self.embeddings is None:
                logger.error("No se pueden inicializar vectorstore sin embeddings")
                return

            # Crear el directorio para ChromaDB si no existe
            persist_directory = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'chroma_db')
            os.makedirs(persist_directory, exist_ok=True)

            self.vectorstore = Chroma(
                embedding_function=self.embeddings,
                persist_directory=persist_directory,
                collection_name="kushki_docs"
            )

            # Configurar el retriever para obtener al menos 5 resultados
            self.retriever = self.vectorstore.as_retriever(
                search_type="similarity",
                search_kwargs={"k": 5}
            )

            logger.info("ChromaDB vectorstore inicializado correctamente")
        except Exception as e:
            logger.error(f"Error al inicializar vectorstore: {e}")
            self.vectorstore = None
            self.retriever = None

    def load_and_split_document(self, file_path: str) -> List[Document]:
        """Cargar y dividir un documento en chunks"""
        try:
            # Determinar el tipo de loader basado en la extensión
            file_extension = os.path.splitext(file_path)[1].lower()
            
            if file_extension == '.pdf':
                loader = UnstructuredPDFLoader(file_path)
            elif file_extension in ['.docx', '.doc']:
                loader = UnstructuredWordDocumentLoader(file_path)
            else:
                logger.warning(f"Tipo de archivo no soportado para RAG: {file_extension}")
                return []

            # Cargar el documento
            documents = loader.load()
            logger.info(f"Documento cargado: {file_path} con {len(documents)} elementos")

            # Configurar el text splitter optimizado para mejor retrieval
            text_splitter = RecursiveCharacterTextSplitter(
                chunk_size=2000,  # Chunks más manejables para mejor precisión
                chunk_overlap=400,  # Overlap optimizado
                length_function=len,
                separators=["\n\n", "\n", ". ", "! ", "? ", "; ", ", ", " ", ""]
            )

            # Dividir documentos en chunks
            splits = text_splitter.split_documents(documents)
            
            # Agregar metadata adicional
            for i, split in enumerate(splits):
                split.metadata.update({
                    'source_file': os.path.basename(file_path),
                    'chunk_id': i,
                    'file_type': file_extension,
                    'processed_time': time.time()
                })

            logger.info(f"Documento dividido en {len(splits)} chunks")
            return splits

        except Exception as e:
            logger.error(f"Error al cargar y dividir documento {file_path}: {e}")
            return []

    def update_vectorstore(self, documents_dir: str):
        """Actualizar el vectorstore incrementalmente solo con documentos nuevos o modificados"""
        if self.vectorstore is None:
            logger.error("Vectorstore no inicializado")
            return False

        try:
            # Obtener todos los archivos PDF y DOCX del directorio
            supported_extensions = ['.pdf', '.docx', '.doc']
            
            if not os.path.exists(documents_dir):
                logger.warning(f"Directorio de documentos no existe: {documents_dir}")
                return False

            # Obtener archivos existentes en vectorstore
            try:
                existing_docs = self.vectorstore.get()
                existing_files = set()
                if existing_docs and 'metadatas' in existing_docs:
                    for metadata in existing_docs['metadatas']:
                        if metadata and 'source_file' in metadata:
                            existing_files.add(metadata['source_file'])
                logger.info(f"Archivos ya en vectorstore: {len(existing_files)}")
            except Exception as e:
                logger.warning(f"No se pudo obtener archivos existentes del vectorstore: {e}")
                existing_files = set()

            # Identificar archivos que necesitan procesamiento
            current_files = set()
            new_documents = []
            
            for root, dirs, files in os.walk(documents_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    file_extension = os.path.splitext(file)[1].lower()
                    
                    if file_extension in supported_extensions:
                        current_files.add(file)
                        
                        # Solo procesar si es nuevo o no está en vectorstore
                        if file not in existing_files:
                            logger.info(f"🆕 Procesando archivo NUEVO para RAG: {file}")
                            document_splits = self.load_and_split_document(file_path)
                            new_documents.extend(document_splits)
                        else:
                            logger.debug(f"⏭️ Archivo ya procesado, saltando: {file}")

            # Remover archivos que ya no existen del vectorstore
            files_to_remove = existing_files - current_files
            if files_to_remove:
                logger.info(f"🗑️ Removiendo {len(files_to_remove)} archivos eliminados del vectorstore")
                for file_to_remove in files_to_remove:
                    try:
                        # Buscar y eliminar documentos de este archivo
                        docs = self.vectorstore.get(where={"source_file": file_to_remove})
                        if docs and docs['ids']:
                            self.vectorstore.delete(ids=docs['ids'])
                            logger.info(f"🗑️ Archivo {file_to_remove} removido del vectorstore")
                    except Exception as e:
                        logger.error(f"Error removiendo archivo {file_to_remove}: {e}")

            # Solo agregar documentos nuevos si los hay
            if new_documents:
                logger.info(f"📚 Agregando {len(new_documents)} chunks nuevos al vectorstore")
                
                # Agregar documentos al vectorstore en lotes
                batch_size = 50
                total_docs = len(new_documents)
                
                for i in range(0, total_docs, batch_size):
                    batch = new_documents[i:i + batch_size]
                    self.vectorstore.add_documents(batch)
                    logger.info(f"✅ Procesado lote {i//batch_size + 1}: {len(batch)} documentos")

                self.last_update = time.time()
                logger.info(f"🎉 Vectorstore actualizado incrementalmente: +{total_docs} chunks nuevos")
            else:
                logger.info(f"✅ Vectorstore ya está actualizado, no hay archivos nuevos")

            # Estadísticas finales
            try:
                final_docs = self.vectorstore.get()
                total_chunks = len(final_docs['ids']) if final_docs and 'ids' in final_docs else 0
                logger.info(f"📊 Total chunks en vectorstore: {total_chunks}")
            except Exception as e:
                logger.debug(f"No se pudo obtener estadísticas finales: {e}")

            return True

        except Exception as e:
            logger.error(f"Error al actualizar vectorstore: {e}")
            return False

    def retrieve_documents(self, query: str, k: int = 5) -> List[Document]:
        """Recuperar documentos relevantes basados en la query con logging detallado"""
        if self.retriever is None:
            logger.error("Retriever no inicializado")
            return []

        try:
            # Actualizar el número de documentos a recuperar (recuperar más para luego filtrar)
            self.retriever.search_kwargs["k"] = k * 2  # Recuperar el doble para mejor selección
            
            retrieved_docs = self.retriever.invoke(query)
            
            # Logging detallado de la recuperación
            logger.info(f"🔍 RAG RETRIEVAL para query: '{query[:100]}...'")
            logger.info(f"📊 Documentos recuperados: {len(retrieved_docs)}")
            
            if retrieved_docs:
                # Log de cada documento recuperado con score si está disponible
                for i, doc in enumerate(retrieved_docs[:k]):  # Solo mostrar los top k
                    source = doc.metadata.get('source_file', 'desconocida')
                    chunk_id = doc.metadata.get('chunk_id', 'N/A')
                    content_preview = doc.page_content[:150].replace('\n', ' ')
                    
                    logger.info(f"  📄 [{i+1}] Fuente: {source} | Chunk: {chunk_id}")
                    logger.info(f"      Contenido: {content_preview}...")
                
                # Filtrar a los mejores k documentos
                final_docs = retrieved_docs[:k]
                total_chars = sum(len(doc.page_content) for doc in final_docs)
                logger.info(f"✅ RAG Context final: {len(final_docs)} docs, {total_chars} caracteres")
                
                return final_docs
            else:
                logger.warning("❌ No se recuperaron documentos del RAG")
                return []

        except Exception as e:
            logger.error(f"Error al recuperar documentos: {e}")
            return []

    def get_context_from_query(self, query: str, k: int = 5) -> str:
        """Obtener contexto relevante como string para incluir en el prompt"""
        retrieved_docs = self.retrieve_documents(query, k)
        
        if not retrieved_docs:
            return ""

        # Combinar el contenido de los documentos recuperados
        context_parts = []
        for i, doc in enumerate(retrieved_docs):
            source_info = f"[Fuente: {doc.metadata.get('source_file', 'desconocida')}]"
            context_parts.append(f"{source_info}\n{doc.page_content}")

        context = "\n\n".join(context_parts)
        logger.info(f"Contexto RAG generado con {len(context)} caracteres")
        
        return context

    def is_initialized(self) -> bool:
        """Verificar si el sistema RAG está completamente inicializado"""
        return (self.embeddings is not None and 
                self.vectorstore is not None and 
                self.retriever is not None)

# Instancia global del sistema RAG
rag_system = RAGSystem() 