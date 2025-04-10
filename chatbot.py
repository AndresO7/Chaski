import os

app_token= os.getenv('SLACK_APP_TOKEN')
bot_token= os.getenv('SLACK_BOT_TOKEN')

from langchain_community.document_loaders import UnstructuredExcelLoader
from langchain_google_genai import GoogleGenerativeAI, HarmBlockThreshold, HarmCategory
from langchain.chains import ConversationChain
from langchain.memory import ConversationBufferMemory
from langchain_core.prompts import PromptTemplate
from langchain_core.messages import HumanMessage, AIMessage
import glob
import os
import threading
import time
import io
import pickle
from datetime import datetime
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
import logging
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload
import re
import random

# Configurar logging con timestamp
class CustomFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        return datetime.fromtimestamp(record.created).strftime('%Y-%m-%d %H:%M:%S')

formatter = CustomFormatter('%(asctime)s - %(levelname)s - %(message)s')
handler = logging.StreamHandler()
handler.setFormatter(formatter)

logger = logging.getLogger()
logger.setLevel(logging.INFO)
for h in logger.handlers:
    logger.removeHandler(h)
logger.addHandler(handler)

prompt_base = """
Eres el asistente virtual de Kushki, tu nombres es Chaski eres un asistente amigable y confiable, cercano con el equipo de kushki, tu mision es ayudar al equipo de kushki respondiendo sus inquietudes, dudas, y preguntas, tu base de conocimiento, que sera una seria de excels, que contienen informacion relevante para el equipo de kushki.
# Tareas:
- Debes responder las preguntas del usuario, y si no tienes la informacion, debes responder que no tienes informacion al respecto, y que si necesitas mas informacion, puedes preguntarle a al equipo de technical writting de kushki.
- Debes responder las preguntas con un tono amigable y cercano, y con un tono profesional.
- Debes responder las preguntas con un tono formal y preciso.
- En caso de que la pregunta sea ambigua, debes hacer una serie de preguntas para dar informacion mas precisa por ejemplo, puedes pedir 
el pais, Chile, Colombia, Ecuador, etc, tambien puedes preguntar el tipo de tarjeta, credito, debito, prepago, etc, tambien puedes preguntar que tarjeta si visa o mastercard o preguntas similares basandote en la estructura de la base de conocimiento.
- Recuerda responder con emojis si la info que obtienes de tu base de conocimiento lo tiene.
- Si la pregunta es de un tema que no esta relacionado con la base de conocimiento, debes decir que tu objetivo unicamente es ayudar con informacion que de tu base de conocimiento.
# Notas
- Ten sumamente en cuenta que como tu base de conocimiento son documentos excel, debes tener en cuenta que la estructura de la base de conocimiento son tablas, por ende por ejemplo va a estar separado por columnas como Visa y en otra Mastercard, o si es credito o debito u otro eso tambien se debe incluir en la respuesta, por ende debes tener en cuenta esto cuando respondas las preguntas, recuerda eso ya que es sumamente importante saber informacion especifica.
- Siempre que te consulten acerca de disponibilidad responde acerca de todas las marcas de tarjetas que tengas en tu base de conocimiento y separa cada una por tarjeta.
- Siempre debes responder en espanol latino.
- Solo responde info de tu base de conocimiento, si no sabes la respuesta, debes decir que no tienes informacion al respecto.
- Trata de ser muy detallado y dar la mayor informacion posible respecto a la pregunta asi que trata de dar bastante info de la pregunta siempre y cuando este en tu base de conocimiento, no te inventes nada adicional a la informacion que tengas en tu base de conocimiento.
- Como eres un chatbot en slack, tienes q tener en cuenta que te saludaran muchas veces, asi que solo responde con un saludo las veces q ellos primeros te saluden y ahi si da tu nombre, presentacion y eso cada vez que empieze con un saludo, despues de eso ya puedes responder las preguntas sin necesidad de saludar nuevamente, por ende no digas Hola en cada respuesta, solo si ellos te dan algun tipo de saludo.
- Como vas a tener en algunos casos informacion variada, recuerda disernir bien lo que vayas a responder, por ejemplo si te preguntan de info de disponibilidad no deberias responder info de un banco de preguntas, obviamente si te preguntan de disponibildad por ejemplo trata de ser lo mas detallado como info de marcas de tarjetas, tipos de tarjetas, etc.
- Recuerda que como eres un chatbot de slack no puedes pasar de los 3000 caracteres, por ende si hay informacion que no puedes responder por que excede los 3000 caracteres, debes preguntar que si desea continuar con el resto de informacion faltante.
- Recuerda solo seguir la estructura de la base de conocimiento y no inventar informacion adicional, como veras se hace basntante enfasis en la estrucutra del excel como tipo de tarjeta: visa, mastercard, amex, diners,etc, y tipo de tarjeta: credito, debito, si es por ejemplo Cloud Terminal (BP) o Raw card-present, etc, ten super encuenta eso al responder, ya que si no lo haces, te vas a equivocar y vas a dar informacion incorrecta que al usuario no le servira de ayuda por eso siempre especifica eso basante en la estrucutra del excel.
- Recuerda solo saludar si el usuario te saluda con hola o algo similar, de lo contrario solo responde las preguntas, trata de evitar el saludo en cada respuesta, asi que no digas Hola en cada respuesta.
- No digas "Hola" en cada respuesta.
- Si no estas seguro de algo puedes hacer una serie de preguntas para dar informacion mas precisa. 
# Base de conocimiento
Tu base de conocimiento es la siguiente, recuerda solo responder informacion que este aqui y seguir la estructura de los excels para que des informacion correcta y detallada:
"""

# Configuración de Google Drive
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
FOLDER_ID = '1O297YnAdRz0iwber4ey1NC3xrlyzTn7Y'
DOWNLOAD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'descargas_drive')
TOKEN_PICKLE = 'token.pickle'
STATE_FILE = 'drive_sync_state.pkl'
CHECK_INTERVAL_SECONDS = 86400  # 24 horas

# API KEY de Google Generative AI
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')

# Inicializar app de Slack
app = App(token=bot_token) 

# Variables globales
docs_string = ""
ultimo_update = 0
system_prompt = ""
docs_actualizados = threading.Event()
llm = None
conversaciones = {}

def authenticate():
    creds = None
    if os.path.exists(TOKEN_PICKLE):
        try:
            with open(TOKEN_PICKLE, 'rb') as token:
                creds = pickle.load(token)
        except (EOFError, pickle.UnpicklingError):
            logging.warning("Archivo token.pickle corrupto o vacío. Se requerirá nueva autenticación.")
            creds = None
            if os.path.exists(TOKEN_PICKLE):
                os.remove(TOKEN_PICKLE)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                logging.error(f"Error al refrescar token: {e}. Se requerirá nueva autenticación.")
                if os.path.exists(TOKEN_PICKLE):
                    os.remove(TOKEN_PICKLE)
                creds = None
        if not creds:
            if not os.path.exists('credentials.json'):
                logging.error("Error Crítico: Falta el archivo 'credentials.json'. Descárgalo desde Google Cloud Console.")
                return None
            try:
                flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
                creds = flow.run_local_server(port=0)
            except Exception as e:
                logging.error(f"Error durante el flujo de autenticación: {e}")
                return None

        try:
            with open(TOKEN_PICKLE, 'wb') as token:
                pickle.dump(creds, token)
        except Exception as e:
            logging.error(f"Error al guardar el archivo token.pickle: {e}")

    return creds

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'rb') as f:
                return pickle.load(f)
        except (EOFError, pickle.UnpicklingError):
            logging.warning(f"Archivo de estado ({STATE_FILE}) corrupto o vacío. Se creará uno nuevo.")
            return {}
        except Exception as e:
            logging.error(f"Error inesperado al cargar el estado desde {STATE_FILE}: {e}")
            return {}
    return {}

def save_state(state):
    try:
        with open(STATE_FILE, 'wb') as f:
            pickle.dump(state, f)
    except Exception as e:
        logging.error(f"Error al guardar el estado en {STATE_FILE}: {e}")

def download_file(service, file_id, file_name, local_path):
    try:
        logging.info(f"Descargando archivo: {file_name} (ID: {file_id})")
        request = service.files().get_media(fileId=file_id)
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        with io.FileIO(local_path, 'wb') as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while done is False:
                status, done = downloader.next_chunk()
        logging.info(f"Archivo '{file_name}' descargado exitosamente en '{local_path}'")
        return True
    except HttpError as error:
        logging.error(f"Ocurrió un error de API al descargar {file_name}: {error}")
        return False
    except Exception as e:
        logging.error(f"Ocurrió un error inesperado al descargar {file_name}: {e}")
        return False

def check_drive_files(service, folder_id, download_path, current_state):
    new_state = current_state.copy()
    updated_count = 0
    added_count = 0
    cambios = False

    try:
        logging.info(f"Verificando archivos en la carpeta de Drive ID: {folder_id}")
        logging.info(f"Directorio de descarga configurado: {download_path}")
        
        # Asegurar que el directorio de descargas existe
        if not os.path.exists(download_path):
            os.makedirs(download_path, exist_ok=True)
            logging.info(f"Directorio creado durante verificación: {download_path}")
        
        query = f"'{folder_id}' in parents and mimeType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' and trashed = false"
        results = service.files().list(
            q=query,
            pageSize=100,
            fields="nextPageToken, files(id, name, modifiedTime)"
        ).execute()
        items = results.get('files', [])

        if not items:
            logging.info(f"No se encontraron archivos .xlsx en la carpeta ID: {folder_id}")
            ids_in_drive = set()
        else:
            logging.info(f"Archivos .xlsx encontrados en Drive: {len(items)}")
            
            # Listar archivos encontrados
            for item in items:
                logging.info(f"Archivo en Drive: {item['name']} (ID: {item['id']})")
            
            drive_files_map = {item['id']: {'name': item['name'], 'modifiedTime': item['modifiedTime']} for item in items}
            ids_in_drive = set(drive_files_map.keys())

            for file_id, file_info in drive_files_map.items():
                file_name = file_info['name']
                drive_modified_time = file_info['modifiedTime']
                local_file_path = os.path.join(download_path, file_name)
                
                # Forzar descarga en el primer ciclo o si no existe localmente
                force_download = (len(current_state) == 0) or (not os.path.exists(local_file_path))
                
                if force_download or file_id not in current_state or current_state[file_id] != drive_modified_time:
                    if force_download:
                        logging.info(f"Forzando descarga de: {file_name}")
                        added_count += 1
                    elif file_id not in current_state:
                        logging.info(f"Archivo nuevo detectado: {file_name}")
                        added_count += 1
                    else:
                        logging.info(f"Archivo modificado detectado: {file_name}")
                        updated_count += 1

                    if download_file(service, file_id, file_name, local_file_path):
                        new_state[file_id] = drive_modified_time
                        cambios = True
                        logging.info(f"Archivo descargado exitosamente: {local_file_path}")
                    else:
                        logging.warning(f"Fallo al descargar {file_name}, se reintentará en el próximo ciclo.")

        # Listar archivos locales después de la descarga
        if os.path.exists(download_path):
            archivos_locales = glob.glob(os.path.join(download_path, "*.xlsx"))
            logging.info(f"Archivos Excel locales después de la verificación: {len(archivos_locales)}")
            for archivo in archivos_locales:
                logging.info(f"Archivo local: {archivo}")
        
        ids_in_state = set(new_state.keys())
        ids_to_remove_from_state = ids_in_state - ids_in_drive
        if ids_to_remove_from_state:
            for id_to_remove in ids_to_remove_from_state:
                if id_to_remove in new_state:
                    del new_state[id_to_remove]
                    cambios = True
                    logging.info(f"Eliminando del estado el archivo con ID: {id_to_remove}")

        return new_state, added_count, updated_count, cambios

    except Exception as e:
        logging.error(f'Ocurrió un error durante la verificación: {e}')
        return current_state, 0, 0, False

def monitoreo_drive():
    global docs_string, system_prompt, ultimo_update
    
    logging.info("Iniciando monitoreo de Google Drive...")
    creds = authenticate()
    if not creds:
        logging.error("Fallo en la autenticación de Google Drive.")
        return

    try:
        service = build('drive', 'v3', credentials=creds)
        logging.info("Autenticación exitosa y servicio de Drive creado.")

        # Asegurar que el directorio de descargas existe
        if not os.path.exists(DOWNLOAD_PATH):
            os.makedirs(DOWNLOAD_PATH, exist_ok=True)
            logging.info(f"Directorio creado durante monitoreo: {DOWNLOAD_PATH}")

        current_state = load_state()
        logging.info(f"Estado inicial cargado con {len(current_state)} archivos rastreados.")

        # Verificar archivos en la primera ejecución
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        logging.info(f"Verificando archivos en Drive ({now})")
        new_state, added, updated, cambios = check_drive_files(service, FOLDER_ID, DOWNLOAD_PATH, current_state)

        if cambios:
            logging.info("Se detectaron cambios en los archivos. Actualizando estado...")
            save_state(new_state)
            current_state = new_state
            logging.info(f"Resumen inicial: {added} archivos agregados, {updated} archivos actualizados.")
            
            # Actualizar documentos y system prompt
            cargar_documentos()
            docs_actualizados.set()
        else:
            if added == 0 and updated == 0:
                logging.info("No se detectaron cambios en los archivos en la verificación inicial.")

        # Ciclo de monitoreo continuo
        while True:
            logging.info(f"Esperando {CHECK_INTERVAL_SECONDS} segundos para la próxima verificación...")
            time.sleep(CHECK_INTERVAL_SECONDS)
            
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            logging.info(f"Verificando cambios en Drive ({now})")
            new_state, added, updated, cambios = check_drive_files(service, FOLDER_ID, DOWNLOAD_PATH, current_state)

            if cambios:
                logging.info("Se detectaron cambios en los archivos. Actualizando estado...")
                save_state(new_state)
                current_state = new_state
                logging.info(f"Resumen: {added} archivos agregados, {updated} archivos actualizados.")
                
                # Actualizar documentos y system prompt
                cargar_documentos()
                docs_actualizados.set()
            else:
                if added == 0 and updated == 0:
                    logging.info("No se detectaron cambios en los archivos.")

    except KeyboardInterrupt:
        logging.info("Monitoreo de Drive interrumpido.")
        if 'current_state' in locals():
            save_state(current_state)
    except Exception as e:
        logging.error(f"Error crítico en el monitoreo de Drive: {e}")
        if 'current_state' in locals():
            save_state(current_state)

def cargar_documentos():
    global docs_string, system_prompt, ultimo_update, llm
    
    ruta_actual = os.getcwd()
    logging.info(f"Directorio actual: {ruta_actual}")
    logging.info(f"Ruta de descargas configurada: {DOWNLOAD_PATH}")
    
    logging.info("Cargando documentos Excel...")
    
    # Asegurar que el directorio de descargas existe
    if not os.path.exists(DOWNLOAD_PATH):
        os.makedirs(DOWNLOAD_PATH, exist_ok=True)
        logging.info(f"Directorio creado: {DOWNLOAD_PATH}")
    
    # Lista todos los archivos en el directorio de descargas 
    logging.info(f"Contenido del directorio {DOWNLOAD_PATH}:")
    if os.path.exists(DOWNLOAD_PATH):
        archivos_dir = os.listdir(DOWNLOAD_PATH)
        for archivo in archivos_dir:
            logging.info(f"- {archivo}")
    
    # Buscar archivos Excel
    archivos_excel = glob.glob(os.path.join(DOWNLOAD_PATH, "*.xlsx"))
    logging.info(f"Archivos Excel encontrados: {len(archivos_excel)}")
    
    if not archivos_excel:
        logging.warning("No se encontraron Excel en la ruta principal, buscando en directorio actual...")
        archivos_excel = glob.glob("*.xlsx")
    
    todos_docs = []
    for archivo in archivos_excel:
        try:
            logging.info(f"Procesando archivo: {archivo}")
            loader = UnstructuredExcelLoader(archivo, mode="elements")
            docs = loader.load()
            todos_docs.extend(docs)
            logging.info(f"Archivo {os.path.basename(archivo)} cargado con {len(docs)} elementos")
        except Exception as e:
            logging.error(f"Error al cargar {archivo}: {e}")
    
    if todos_docs:
        docs_string = "\n".join([doc.page_content for doc in todos_docs])
        system_prompt = prompt_base + docs_string + "\n" 
        ultimo_update = time.time()
        logging.info(f"Documentos cargados: {len(todos_docs)} elementos de {len(archivos_excel)} archivos")
    else:
        logging.warning("No se encontraron documentos para cargar")
        docs_string = ""
        system_prompt = prompt_base + "\n"
        ultimo_update = time.time()
        logging.info("Usando prompt base sin documentos adicionales")
    
    # Inicializar o actualizar el modelo LLM
    inicializar_llm()

def inicializar_llm():
    global llm
    
    logging.info("Inicializando modelo LLM con LangChain...")
    
    # Configuración de seguridad para Gemini
    safety_settings = {
        HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
        HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
        HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
        HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
    }
    
    # Inicializar el modelo LLM
    llm = GoogleGenerativeAI(
        model="gemini-2.0-flash",
        google_api_key=GOOGLE_API_KEY,
        safety_settings=safety_settings,
        temperature=0.4,
        top_k=40,
        top_p=0.95,
        max_output_tokens=2048,
    )
    
    logging.info("Modelo LLM inicializado con éxito")

def generar_respuesta(pregunta, historial_mensajes):
    global system_prompt, llm
    
    # Esperar si hay actualizaciones en proceso
    if docs_actualizados.is_set():
        logging.info("Esperando a que se complete la actualización de documentos...")
        docs_actualizados.clear()
    
    if llm is None:
        inicializar_llm()
    
    logging.info(f"Generando respuesta para pregunta: {pregunta[:50]}...")
    
    # Crear la plantilla del prompt con el contexto del sistema
    template = f"""{{system_prompt}}

{{history}}
Usuario: {{input}}
Asistente:"""
    
    prompt = PromptTemplate.from_template(template)
    
    # Preparar el historial en formato de texto
    history_text = ""
    for msg in historial_mensajes:
        if msg["role"] == "user":
            history_text += f"Usuario: {msg['content']}\n"
        else:
            history_text += f"Asistente: {msg['content']}\n"
    
    # Crear la cadena con el prompt y el LLM
    try:
        respuesta = llm.invoke(
            prompt.format(
                system_prompt=system_prompt,
                history=history_text,
                input=pregunta
            )
        )
        logging.info(f"Respuesta generada: {respuesta[:50]}...")
        return respuesta
    except Exception as e:
        logging.error(f"Error al generar respuesta: {e}")
        return "Lo siento, ocurrió un error al procesar tu pregunta. Por favor, intenta de nuevo más tarde."

# Limitar historial a 20 mensajes (10 interacciones)
def limitar_historial(historial, max_mensajes=20):
    if len(historial) > max_mensajes:
        return historial[-max_mensajes:]
    return historial

# Función para convertir markdown estándar al formato de Slack
def convertir_a_slack_markdown(texto):
    # Convertir **texto** a *texto* (negrita)
    texto = re.sub(r'\*\*(.*?)\*\*', r'*\1*', texto)
    
    # Convertir listas con asteriscos a listas con bullet points
    texto = re.sub(r'^\* (.*)$', r'• \1', texto, flags=re.MULTILINE)
    
    # Convertir bloques de código
    texto = re.sub(r'```(.*?)```', r'```\1```', texto, flags=re.DOTALL)
    
    return texto

# Función para dividir mensajes largos en trozos que respeten el límite de Slack
def dividir_mensaje(texto, max_length=2900):
    """
    Divide un mensaje largo en partes más pequeñas que respeten el límite de caracteres de Slack.
    """
    if len(texto) <= max_length:
        return [texto]
    
    partes = []
    # Dividir por párrafos si es posible
    paragraphs = texto.split('\n\n')
    current_part = ""
    
    for paragraph in paragraphs:
        # Si el párrafo solo ya es demasiado grande, dividirlo por frases
        if len(paragraph) > max_length:
            sentences = re.split(r'(?<=[.!?])\s+', paragraph)
            for sentence in sentences:
                if len(current_part) + len(sentence) + 2 <= max_length:
                    if current_part:
                        current_part += "\n\n" if current_part.endswith(".") else " "
                    current_part += sentence
                else:
                    if current_part:
                        partes.append(current_part)
                    current_part = sentence
        else:
            # Verificar si podemos añadir este párrafo a la parte actual
            if len(current_part) + len(paragraph) + 2 <= max_length:
                if current_part:
                    current_part += "\n\n"
                current_part += paragraph
            else:
                if current_part:
                    partes.append(current_part)
                current_part = paragraph
    
    # No olvidar la última parte
    if current_part:
        partes.append(current_part)
    
    # Añadir indicadores de continuación
    for i in range(len(partes) - 1):
        partes[i] += "\n\n_(continúa...)_"
    
    return partes

# Mutex para operaciones críticas con el LLM
llm_mutex = threading.Lock()

@app.event("message")
def handle_message_events(body, logger):
    try:
        if "channel" in body["event"] and "text" in body["event"]:
            channel_id = body["event"]["channel"]
            user_id = body["event"]["user"]
            texto = body["event"]["text"]
            
            # Ignorar mensajes del bot
            if "bot_id" in body["event"]:
                return
            
            # Inicializar historial si es necesario
            if user_id not in conversaciones:
                conversaciones[user_id] = []
            
            # Generar respuesta (con mutex para operaciones críticas)
            with llm_mutex:
                respuesta = generar_respuesta(texto, conversaciones[user_id])
            
            # Convertir al formato de markdown de Slack
            respuesta_slack = convertir_a_slack_markdown(respuesta)
            
            # Actualizar historial (limitado)
            conversaciones[user_id].extend([
                {"role": "user", "content": texto},
                {"role": "assistant", "content": respuesta}
            ])
            conversaciones[user_id] = limitar_historial(conversaciones[user_id])
            
            # Dividir la respuesta si es demasiado larga
            partes_respuesta = dividir_mensaje(respuesta_slack)
            
            # Enviar cada parte como un mensaje separado
            for i, parte in enumerate(partes_respuesta):
                try:
                    app.client.chat_postMessage(
                        channel=channel_id,
                        text=parte,  # Fallback para notificaciones
                        blocks=[
                            {
                                "type": "section",
                                "text": {
                                    "type": "mrkdwn",
                                    "text": parte
                                }
                            }
                        ]
                    )
                    logging.info(f"Respuesta parte {i+1}/{len(partes_respuesta)} enviada al canal {channel_id}")
                except Exception as e:
                    error_msg = f"Error al enviar mensaje a Slack: {str(e)}"
                    logging.error(error_msg)
                    
                    # Intentar enviar un mensaje de error amigable
                    try:
                        app.client.chat_postMessage(
                            channel=channel_id,
                            text="Perdón por los inconvenientes, no estoy disponible en este momento. Por favor, intenta de nuevo más tarde."
                        )
                    except Exception:
                        logging.error("No se pudo enviar el mensaje de error amigable")
                    break
    except Exception as e:
        error_msg = f"Error al procesar mensaje: {str(e)}"
        logger.error(error_msg)
        
        # Intentar enviar un mensaje de error amigable si tenemos el channel_id
        try:
            if 'channel_id' in locals():
                app.client.chat_postMessage(
                    channel=channel_id,
                    text="Perdón por los inconvenientes, no estoy disponible en este momento. Por favor, intenta de nuevo más tarde."
                )
        except Exception:
            logger.error("No se pudo enviar el mensaje de error amigable")

@app.event("app_mention")
def handle_app_mention_events(body, logger):
    try:
        event = body["event"]
        channel_id = event["channel"]
        user_id = event["user"]
        texto = event["text"]
        
        # Quitar la mención del bot del texto
        texto = re.sub(r'<@[A-Z0-9]+>', '', texto).strip()
        
        # Inicializar historial si es necesario
        if user_id not in conversaciones:
            conversaciones[user_id] = []
        
        # Generar respuesta (con mutex para operaciones críticas)
        with llm_mutex:
            respuesta = generar_respuesta(texto, conversaciones[user_id])
        
        # Convertir al formato de markdown de Slack
        respuesta_slack = convertir_a_slack_markdown(respuesta)
        
        # Actualizar historial (limitado)
        conversaciones[user_id].extend([
            {"role": "user", "content": texto},
            {"role": "assistant", "content": respuesta}
        ])
        conversaciones[user_id] = limitar_historial(conversaciones[user_id])
        
        # Dividir la respuesta si es demasiado larga
        partes_respuesta = dividir_mensaje(respuesta_slack)
        
        # Enviar cada parte como un mensaje separado
        for i, parte in enumerate(partes_respuesta):
            try:
                app.client.chat_postMessage(
                    channel=channel_id,
                    text=parte, # Fallback para notificaciones
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": parte
                            }
                        }
                    ]
                )
                logging.info(f"Respuesta a mención parte {i+1}/{len(partes_respuesta)} enviada al canal {channel_id}")
            except Exception as e:
                error_msg = f"Error al enviar mensaje a Slack: {str(e)}"
                logging.error(error_msg)
                
                # Intentar enviar un mensaje de error amigable
                try:
                    app.client.chat_postMessage(
                        channel=channel_id,
                        text="Perdón por los inconvenientes, no estoy disponible en este momento. Por favor, intenta de nuevo más tarde."
                    )
                except Exception:
                    logging.error("No se pudo enviar el mensaje de error amigable")
                break
    except Exception as e:
        error_msg = f"Error al procesar mención: {str(e)}"
        logger.error(error_msg)
        
        # Intentar enviar un mensaje de error amigable si tenemos el channel_id
        try:
            if 'channel_id' in locals():
                app.client.chat_postMessage(
                    channel=channel_id,
                    text="Perdón por los inconvenientes, no estoy disponible en este momento. Por favor, intenta de nuevo más tarde."
                )
        except Exception:
            logger.error("No se pudo enviar el mensaje de error amigable")

# Función para monitorear la salud del chatbot
def health_check():
    """Monitorea la salud del chatbot y registra estadísticas básicas."""
    while True:
        try:
            # Contar usuarios activos
            usuarios_activos = len(conversaciones)
            # Verificar LLM
            llm_status = "Disponible" if llm is not None else "No inicializado"
            
            logging.info(f"Estado del chatbot - LLM: {llm_status}, Usuarios activos: {usuarios_activos}")
            
            # Liberar memoria si hay demasiados usuarios inactivos
            if usuarios_activos > 100:  # umbral arbitrario, ajustar según necesidades
                logging.info("Realizando limpieza de conversaciones antiguas...")
                ahora = time.time()
                usuarios_inactivos = 0
                for user_id in list(conversaciones.keys()):
                    # Este es un enfoque simple. En una implementación real, 
                    # deberías rastrear el tiempo del último mensaje
                    if len(conversaciones[user_id]) > 0 and random.random() < 0.1:
                        del conversaciones[user_id]
                        usuarios_inactivos += 1
                logging.info(f"Se eliminaron {usuarios_inactivos} conversaciones antiguas")
            
            # Verificar cada 5 minutos
            time.sleep(300)
        except Exception as e:
            logging.error(f"Error en health check: {e}")
            time.sleep(60)  # Si hay error, esperar menos tiempo antes de reintentar

if __name__ == "__main__":
    # Asegurar que el directorio de descargas existe
    if not os.path.exists(DOWNLOAD_PATH):
        os.makedirs(DOWNLOAD_PATH, exist_ok=True)
        logging.info(f"Directorio de descargas creado: {DOWNLOAD_PATH}")
    else:
        logging.info(f"Usando directorio de descargas existente: {DOWNLOAD_PATH}")
    
    # Mostrar información del entorno
    logging.info(f"Directorio actual: {os.getcwd()}")
    logging.info(f"Directorio de descargas: {DOWNLOAD_PATH}")
    
    # Verificar si hay archivos Excel directamente
    excel_files = glob.glob(os.path.join(DOWNLOAD_PATH, "*.xlsx"))
    logging.info(f"Archivos Excel encontrados directamente: {len(excel_files)}")
    if excel_files:
        for file in excel_files:
            logging.info(f"Excel encontrado: {file}")
    
    # Cargar documentos al iniciar
    cargar_documentos()
    print(system_prompt)
    # Imprimir longitud del system prompt para verificar
    if system_prompt:
        logging.info(f"System prompt cargado con {len(system_prompt)} caracteres")
        print(f"System prompt cargado con {len(system_prompt)} caracteres")
    else:
        logging.warning("System prompt no fue cargado correctamente")
    
    # Iniciar el monitoreo de Drive en un hilo separado
    drive_thread = threading.Thread(target=monitoreo_drive, daemon=True)
    drive_thread.start()
    
    # Iniciar el monitoreo de salud en un hilo separado
    health_thread = threading.Thread(target=health_check, daemon=True)
    health_thread.start()
    
    # Iniciar la aplicación en modo Socket
    try:
        logging.info("Iniciando la aplicación en modo Socket...")
        handler = SocketModeHandler(app, app_token)
        handler.start()
    except Exception as e:
        logging.error(f"Error al iniciar la aplicación: {e}")
