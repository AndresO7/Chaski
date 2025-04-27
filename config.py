import os
from dotenv import load_dotenv


load_dotenv()


SLACK_APP_TOKEN = os.getenv('SLACK_APP_TOKEN')
SLACK_BOT_TOKEN = os.getenv('SLACK_BOT_TOKEN')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')


GOOGLE_DRIVE_FOLDER_ID = os.getenv('GOOGLE_DRIVE_FOLDER_ID')
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
DOWNLOAD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'descargas_drive')
TOKEN_PICKLE = 'token.pickle'
STATE_FILE = 'drive_sync_state.pkl'
CREDENTIALS_FILE = 'credentials.json' 
CHECK_INTERVAL_SECONDS = 86400  
MODEL_NAME = "gemini-2.5-flash-preview-04-17"
LLM_TEMPERATURE = 0.4
LLM_TOP_K = 40
LLM_TOP_P = 0.95
LLM_MAX_OUTPUT_TOKENS = 2048
SLACK_MAX_MESSAGE_LENGTH = 2900
HISTORY_MAX_MESSAGES = 20

# --- INICIO: Nueva lista de usuarios autorizados ---
# Lista de IDs de usuario de Slack autorizados para interactuar con el bot
SLACK_AUTHORIZED_USERS = [
  
    "U08JKL1TEE9",
    "UN8K24ZGR",
    "U02AYGA1HHU",
    "U01HLRZLP70",
    "U03JECFEESC",
]
# --- FIN: Nueva lista de usuarios autorizados ---

required_vars = ['SLACK_APP_TOKEN', 'SLACK_BOT_TOKEN', 'GOOGLE_API_KEY', 'GOOGLE_DRIVE_FOLDER_ID']
missing_vars = [var for var in required_vars if not globals().get(var)]

if missing_vars:
    raise ValueError(f"Error: Faltan las siguientes variables de entorno en el archivo .env: {', '.join(missing_vars)}") 