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

            # Configurar el text splitter con chunks grandes y overlap grande
            text_splitter = RecursiveCharacterTextSplitter(
                chunk_size=4000,  # Chunks grandes para aprovechar ventana de contexto
                chunk_overlap=800,  # Overlap grande para mantener contexto
                length_function=len,
                separators=["\n\n", "\n", ". ", " ", ""]
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
        """Actualizar el vectorstore con documentos del directorio"""
        if self.vectorstore is None:
            logger.error("Vectorstore no inicializado")
            return False

        try:
            # Obtener todos los archivos PDF y DOCX del directorio
            supported_extensions = ['.pdf', '.docx', '.doc']
            all_documents = []

            if not os.path.exists(documents_dir):
                logger.warning(f"Directorio de documentos no existe: {documents_dir}")
                return False

            for root, dirs, files in os.walk(documents_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    file_extension = os.path.splitext(file)[1].lower()
                    
                    if file_extension in supported_extensions:
                        logger.info(f"Procesando archivo para RAG: {file}")
                        document_splits = self.load_and_split_document(file_path)
                        all_documents.extend(document_splits)

            if not all_documents:
                logger.info("No se encontraron documentos PDF/DOCX para procesar")
                return True

            # Limpiar vectorstore existente
            try:
                self.vectorstore.delete_collection()
                logger.info("Vectorstore anterior limpiado")
            except Exception as e:
                logger.warning(f"No se pudo limpiar vectorstore anterior: {e}")

            # Reinicializar vectorstore
            self.initialize_vectorstore()

            # Agregar documentos al vectorstore en lotes
            batch_size = 50
            total_docs = len(all_documents)
            
            for i in range(0, total_docs, batch_size):
                batch = all_documents[i:i + batch_size]
                self.vectorstore.add_documents(batch)
                logger.info(f"Procesado lote {i//batch_size + 1}: {len(batch)} documentos")

            self.last_update = time.time()
            logger.info(f"Vectorstore actualizado con {total_docs} chunks de documentos")
            return True

        except Exception as e:
            logger.error(f"Error al actualizar vectorstore: {e}")
            return False

    def retrieve_documents(self, query: str, k: int = 5) -> List[Document]:
        """Recuperar documentos relevantes basados en la query"""
        if self.retriever is None:
            logger.error("Retriever no inicializado")
            return []

        try:
            # Actualizar el número de documentos a recuperar
            self.retriever.search_kwargs["k"] = k
            
            retrieved_docs = self.retriever.invoke(query)
            logger.info(f"Recuperados {len(retrieved_docs)} documentos para la query: {query[:50]}...")
            
            return retrieved_docs

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