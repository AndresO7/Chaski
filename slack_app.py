import re
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

import config
from logger_config import logger
import chatbot


app = App(token=config.SLACK_BOT_TOKEN)
'''
@app.event("message")
def handle_message_events(body, say):
    event = body["event"]
    channel_id = event["channel"]
    user_id = event["user"]
    texto = event["text"]

    if "bot_id" in event or event.get("subtype") == "message_changed":
        return

    try:
        bot_user_id = body.get("authorizations", [{}])[0].get("user_id")
        if bot_user_id and texto.strip().startswith(f'<@{bot_user_id}>'):
            logger.debug(f"Mensaje ignorado en handle_message_events por ser mención directa: {texto[:50]}...")
            return 
    except (IndexError, KeyError, TypeError) as e:
        logger.warning(f"No se pudo obtener bot_user_id para verificar mención en handle_message_events: {e}")


    logger.info(f"Mensaje recibido en canal {channel_id} de usuario {user_id}: {texto[:50]}...")

    if user_id not in chatbot.conversaciones:
        logger.info(f"Nuevo usuario detectado: {user_id}. Inicializando historial.")
        chatbot.conversaciones[user_id] = []

    respuesta_llm = chatbot.generar_respuesta(texto, chatbot.conversaciones[user_id])

    respuesta_slack = chatbot.convertir_a_slack_markdown(respuesta_llm)
    partes_respuesta = chatbot.dividir_mensaje(respuesta_slack)

    chatbot.conversaciones[user_id].extend([
        {"role": "user", "content": texto},
        {"role": "assistant", "content": respuesta_llm} 
    ])
    chatbot.conversaciones[user_id] = chatbot.limitar_historial(chatbot.conversaciones[user_id])
    logger.debug(f"Historial actualizado para {user_id}. Tamaño: {len(chatbot.conversaciones[user_id])}.")

    for i, parte in enumerate(partes_respuesta):
        try:
            say(channel=channel_id, text=parte)
            logger.info(f"Respuesta parte {i+1}/{len(partes_respuesta)} enviada a {channel_id}")
        except Exception as e:
            logger.error(f"Error al enviar parte {i+1} a Slack ({channel_id}): {e}")
            try:
                say(channel=channel_id, text="Hubo un problema al enviar la respuesta. Intenta de nuevo.")
            except Exception as final_e:
                logger.error(f"No se pudo enviar mensaje de error a Slack: {final_e}")
            break
'''
@app.event("app_mention")
def handle_app_mention_events(body, say):
    event = body["event"]
    channel_id = event["channel"]
    user_id = event["user"]
    texto_completo = event["text"]
    try:
        bot_user_id = body["authorizations"][0]["user_id"]
        texto_limpio = re.sub(rf'^<@{bot_user_id}>\s*', '', texto_completo).strip()
    except (IndexError, KeyError, TypeError) as e:
        logger.error(f"No se pudo obtener bot_user_id para limpiar mención: {e}. Procesando texto completo.")
        texto_limpio = texto_completo 

    logger.info(f"Mención recibida en {channel_id} de {user_id}. Texto procesado: {texto_limpio[:50]}...")

    if user_id not in chatbot.conversaciones:
        logger.info(f"Nuevo usuario detectado (vía mención): {user_id}. Inicializando historial.")
        chatbot.conversaciones[user_id] = []

    respuesta_llm = chatbot.generar_respuesta(texto_limpio, chatbot.conversaciones[user_id])

    respuesta_slack = chatbot.convertir_a_slack_markdown(respuesta_llm)
    partes_respuesta = chatbot.dividir_mensaje(respuesta_slack)

    chatbot.conversaciones[user_id].extend([
        {"role": "user", "content": texto_limpio}, 
        {"role": "assistant", "content": respuesta_llm}
    ])
    chatbot.conversaciones[user_id] = chatbot.limitar_historial(chatbot.conversaciones[user_id])
    logger.debug(f"Historial actualizado para {user_id} (vía mención). Tamaño: {len(chatbot.conversaciones[user_id])}.")

    for i, parte in enumerate(partes_respuesta):
        try:
            say(channel=channel_id, text=parte)
            logger.info(f"Respuesta a mención parte {i+1}/{len(partes_respuesta)} enviada a {channel_id}")
        except Exception as e:
            logger.error(f"Error al enviar parte {i+1} de mención a Slack ({channel_id}): {e}")
            try:
                say(channel=channel_id, text="Hubo un problema al enviar la respuesta. Intenta de nuevo.")
            except Exception as final_e:
                logger.error(f"No se pudo enviar mensaje de error a Slack: {final_e}")
            break


def start_slack_app():
    logger.info("Iniciando Slack App en modo Socket...")
    try:
        if not config.SLACK_APP_TOKEN:
             logger.error("SLACK_APP_TOKEN no encontrado. La aplicación no puede iniciar.")
             return

        handler = SocketModeHandler(app, config.SLACK_APP_TOKEN)
        handler.start() 
    except Exception as e:
        logger.error(f"Error fatal en SocketModeHandler: {e}")
