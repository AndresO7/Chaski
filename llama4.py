from langchain_community.document_loaders import UnstructuredExcelLoader
import glob
import os
import threading
import time
import io
import pickle
import re
import logging
from openai import OpenAI
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload
from flask import Flask

app_token= os.getenv('APP_TOKEN')
bot_token=os.getenv('BOT_TOKEN')

# Crear app Flask para health checks
app_health = Flask(__name__)

@app_health.route('/')
def home():
    return "Bot de Slack en ejecución"

def run_health_server():
    port = int(os.environ.get('PORT', 10000))
    app_health.run(host='0.0.0.0', port=port)

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
Tu base de conocimiento es la siguiente, recuerda solo responder informacion que este aqui y sueguir la estructura de los excels para que des informacion correcta y detallada:
"""

# Configurar logging
logging.basicConfig(level=logging.INFO)

# Configuración de Google Drive
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
FOLDER_ID = '1O297YnAdRz0iwber4ey1NC3xrlyzTn7Y'
DOWNLOAD_PATH = 'descargas_drive'
TOKEN_PICKLE = 'token.pickle'
STATE_FILE = 'drive_sync_state.pkl'
CHECK_INTERVAL_SECONDS = 86400  # 24 horas

# Inicializar app de Slack
app = App(token=bot_token)

# Inicializar cliente de OpenAI para Llama 4
llama_client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv('OPEN_ROUTER_API_KEY'),
)

# Variables globales
docs_string = ""
ultimo_update = 0
system_prompt = ""
docs_actualizados = threading.Event()

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
            drive_files_map = {item['id']: {'name': item['name'], 'modifiedTime': item['modifiedTime']} for item in items}
            ids_in_drive = set(drive_files_map.keys())

            for file_id, file_info in drive_files_map.items():
                file_name = file_info['name']
                drive_modified_time = file_info['modifiedTime']
                local_file_path = os.path.join(download_path, file_name)

                if file_id not in current_state or current_state[file_id] != drive_modified_time:
                    if file_id not in current_state:
                        logging.info(f"Archivo nuevo detectado: {file_name}")
                        added_count += 1
                    else:
                        logging.info(f"Archivo modificado detectado: {file_name}")
                        updated_count += 1

                    if download_file(service, file_id, file_name, local_file_path):
                        new_state[file_id] = drive_modified_time
                        cambios = True
                    else:
                        logging.warning(f"Fallo al descargar {file_name}, se reintentará en el próximo ciclo.")

        ids_in_state = set(new_state.keys())
        ids_to_remove_from_state = ids_in_state - ids_in_drive
        if ids_to_remove_from_state:
            for id_to_remove in ids_to_remove_from_state:
                if id_to_remove in new_state:
                    del new_state[id_to_remove]
                    cambios = True

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

        if not os.path.exists(DOWNLOAD_PATH):
            os.makedirs(DOWNLOAD_PATH)

        current_state = load_state()
        logging.info(f"Estado inicial cargado con {len(current_state)} archivos rastreados.")

        while True:
            logging.info(f"Verificando cambios en Drive ({time.strftime('%Y-%m-%d %H:%M:%S')})")
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

            logging.info(f"Esperando {CHECK_INTERVAL_SECONDS} segundos para la próxima verificación...")
            time.sleep(CHECK_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        logging.info("Monitoreo de Drive interrumpido.")
        if 'current_state' in locals():
            save_state(current_state)
    except Exception as e:
        logging.error(f"Error crítico en el monitoreo de Drive: {e}")
        if 'current_state' in locals():
            save_state(current_state)

def cargar_documentos():
    global docs_string, system_prompt, ultimo_update
    
    logging.info("Cargando documentos Excel...")
    archivos_excel = glob.glob(f"{DOWNLOAD_PATH}/*.xlsx")
    if not archivos_excel:
        archivos_excel = glob.glob("./*.xlsx")  # Fallback a directorio actual
    
    todos_docs = []
    for archivo in archivos_excel:
        try:
            loader = UnstructuredExcelLoader(archivo, mode="elements")
            docs = loader.load()
            todos_docs.extend(docs)
        except Exception as e:
            logging.error(f"Error al cargar {archivo}: {e}")
    
    if todos_docs:
        docs_string = "\n".join([doc.page_content for doc in todos_docs])
        system_prompt = prompt_base + docs_string + "\n" 
        ultimo_update = time.time()
        logging.info(f"Documentos cargados: {len(todos_docs)} elementos de {len(archivos_excel)} archivos")
    else:
        logging.warning("No se encontraron documentos para cargar")

def generar_respuesta(pregunta, historial):
    global system_prompt
    
    # Esperar si hay actualizaciones en proceso
    if docs_actualizados.is_set():
        logging.info("Esperando a que se complete la actualización de documentos...")
        docs_actualizados.clear()
    
    # Convertir historial al formato esperado por la API de Llama 4
    mensajes_formateados = []
    
    # Agregar el system prompt como mensaje del sistema
    if system_prompt:
        mensajes_formateados.append({
            "role": "system",
            "content": system_prompt
        })
    
    # Agregar historial de mensajes
    for i in range(0, len(historial), 2):
        if i < len(historial):
            mensajes_formateados.append({
                "role": "user",
                "content": historial[i]["content"]
            })
        if i+1 < len(historial):
            mensajes_formateados.append({
                "role": "assistant",
                "content": historial[i+1]["content"]
            })
    
    # Agregar la pregunta actual
    mensajes_formateados.append({
        "role": "user",
        "content": pregunta
    })
    
    try:
        # Llamar a la API de Llama 4 a través de OpenRouter
        completion = llama_client.chat.completions.create(
            model="meta-llama/llama-4-scout:free",
            messages=mensajes_formateados
        )
        
        # Extraer la respuesta
        respuesta = completion.choices[0].message.content
        return respuesta
    except Exception as e:
        logging.error(f"Error al generar respuesta con Llama 4: {e}")
        return "Lo siento, ocurrió un error al procesar tu pregunta. Por favor, intenta nuevamente más tarde."

# Limitar historial a un número máximo de mensajes
def limitar_historial(historial, max_mensajes=20):
    if len(historial) > max_mensajes:
        return historial[-max_mensajes:]
    return historial

# Diccionario para almacenar el historial de conversaciones por usuario
conversaciones = {}

# Función para convertir markdown estándar al formato de Slack
def convertir_a_slack_markdown(texto):
    # Convertir **texto** a *texto* (negrita)
    texto = re.sub(r'\*\*(.*?)\*\*', r'*\1*', texto)
    
    # Convertir listas con asteriscos a listas con bullet points
    texto = re.sub(r'^\* (.*)$', r'• \1', texto, flags=re.MULTILINE)
    
    # Convertir bloques de código
    texto = re.sub(r'```(.*?)```', r'```\1```', texto, flags=re.DOTALL)
    
    return texto

@app.event("message")
def handle_message_events(body, logger):
    # Desactivado para que el bot solo responda cuando sea mencionado con @chaski
    pass

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
        
        # Generar respuesta
        respuesta = generar_respuesta(texto, conversaciones[user_id])
        
        # Convertir al formato de markdown de Slack
        respuesta_slack = convertir_a_slack_markdown(respuesta)
        
        # Actualizar historial
        conversaciones[user_id].extend([
            {"role": "user", "content": texto},
            {"role": "assistant", "content": respuesta}
        ])
        conversaciones[user_id] = limitar_historial(conversaciones[user_id])
        
        # Enviar respuesta al canal
        app.client.chat_postMessage(
            channel=channel_id,
            text=respuesta_slack,  # Fallback para notificaciones
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": respuesta_slack
                    }
                }
            ]
        )
    except Exception as e:
        logger.error(f"Error al procesar mención: {e}")

if __name__ == "__main__":
    # Cargar documentos al iniciar
    cargar_documentos()
    
    # Iniciar el monitoreo de Drive en un hilo separado
    drive_thread = threading.Thread(target=monitoreo_drive, daemon=True)
    drive_thread.start()
    
    # Iniciar el servidor de health checks en un hilo separado
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()
    
    # Iniciar la aplicación en modo Socket
    handler = SocketModeHandler(app, app_token)
    handler.start()
