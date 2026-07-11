import argparse
import asyncio
import os
import sys
import time
import logging
import websockets
from urllib.parse import urlsplit, parse_qs
from uuid import uuid4
from websockets.extensions import permessage_deflate

from pipeline_loader import PipelineLoader
from nexus_client import NexusClient

VK_URL = os.environ.get("VK_URL", "http://voight-kampff:8080")
MQTT_HOST = os.environ.get("MQTT_HOST", "mosquitto-broker")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_SERVICE_USERNAME = os.environ.get("MQTT_SERVICE_USERNAME")
MQTT_SERVICE_API_KEY = os.environ.get("MQTT_SERVICE_API_KEY")

MANIFEST = {
    "service": "joshua",
    "publishes": [
        {
            "topic": "common/user_connected",
            "description": "Connexion d'un utilisateur WebSocket",
            "payload": {
                "event": "user_connected",
                "username": "string",
                "session_id": "string (UUID unique par connexion WebSocket)",
                "password": "string (cookie de session VK — utilisable comme mot de passe MQTT)",
                "private_topics": "list[{agent, topics[]}] — topics privés de l'utilisateur par agent",
            },
        }
    ],
}

# Charger les variables d'environnement
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

connected_clients = set()
pipeline_args = None

# Historique de conversation persisté en mémoire, keyé par conversation_id
# (généré et stocké côté client dans localStorage, survit à une reconnexion
# websocket) — contrairement à session_id qui reste un identifiant technique
# unique par connexion. TTL courte : ceci ne sert qu'à absorber une coupure
# réseau brève au milieu d'une conversation active, pas à faire une mémoire
# longue durée entre deux sessions d'usage distinctes.
CONVERSATION_HISTORY_TTL = 30 * 60
_conversation_histories: dict[str, tuple[float, list]] = {}


def _prune_conversation_histories():
    now = time.time()
    expired = [cid for cid, (ts, _) in _conversation_histories.items() if now - ts > CONVERSATION_HISTORY_TTL]
    for cid in expired:
        del _conversation_histories[cid]


def _load_conversation_history(conversation_id: str | None) -> list:
    if not conversation_id:
        return []
    _prune_conversation_histories()
    entry = _conversation_histories.get(conversation_id)
    return list(entry[1]) if entry else []


def _save_conversation_history(conversation_id: str | None, history: list):
    if not conversation_id:
        return
    if not history:
        _conversation_histories.pop(conversation_id, None)
        return
    _conversation_histories[conversation_id] = (time.time(), history)
    _prune_conversation_histories()


def _extract_cookie(cookie_header: str, name: str) -> str:
    for part in cookie_header.split(";"):
        k, _, v = part.strip().partition("=")
        if k.strip() == name:
            return v.strip()
    return ""


class Client():

    @classmethod
    async def create(cls, pipeline_name: str, websocket, nexus: NexusClient | None, session_id: str = None,
                      conversation_id: str = None):
        """Factory method async pour créer un Client"""
        pipeline = run_pipeline(pipeline_name)
        if not pipeline:
            raise ValueError(f"Impossible de créer le pipeline: {pipeline_name}")

        success = await pipeline.start()
        if not success:
            raise ValueError(f"Impossible de démarrer le pipeline: {pipeline_name}")

        return cls(pipeline, websocket, nexus, session_id, conversation_id)

    def __init__(self, pipeline: str, websocket, nexus: NexusClient | None, session_id: str = None,
                 conversation_id: str = None):
        self.pipeline = pipeline
        self.pipeline_input = self.pipeline.get_step("websocket_server")
        self.ws = websocket
        self.username = nexus.username if nexus else "anonymous"
        self.session_id = session_id or str(uuid4())
        self.conversation_id = conversation_id

        # set_nexus must happen before set_ws_callback: set_ws_callback sends
        # UserConnectionMessage which triggers user_connected on MQTT; the
        # mqtt_step must already be subscribed to agent_topics at that point.
        mqtt_step = self.pipeline.get_step("mqtt_step")
        if mqtt_step and nexus:
            mqtt_step.set_nexus(nexus, self.session_id)

        self.chat_step = self.pipeline.get_step("openai_chat")
        if self.chat_step and self.conversation_id:
            restored = _load_conversation_history(self.conversation_id)
            if restored:
                self.chat_step.restore_history(restored)

        self.pipeline_input.set_ws_callback(self.sendToClient, self.username)

        tts_step = self.pipeline.get_step("tts_step")
        if tts_step:
            self.pipeline_input.register_interrupt_queue(tts_step.input_queue)

    def sendToClient(self, message):
        asyncio.get_running_loop().create_task(self.ws.send(message))

    async def handle_message(self, websocket):
        try:
            # Listen for messages from the chat client
            async for message in websocket:
                self.pipeline_input.handle_websocket(message)
        except websockets.exceptions.ConnectionClosed as ie:
            logging.info(f"Client connection closed: {ie}")
        finally:
            # Remove the client from the set of connected clients
            connected_clients.remove(self)

            if self.chat_step and self.conversation_id:
                _save_conversation_history(self.conversation_id, self.chat_step.export_history())

            # CLEANUP: Stop pipeline to free resources (TTS WebSocket, threads, etc.)
            try:
                if self.pipeline:
                    logging.info(f"Cleaning up pipeline for disconnected client...")
                    await self.pipeline.stop()
                    logging.info(f"Pipeline cleanup completed")
            except Exception as e:
                logging.error(f"Error during pipeline cleanup: {e}")
            
            logging.info(f"remaining number of client: {len(connected_clients)}")
        return

async def publish_manifest():
    if not MQTT_SERVICE_USERNAME or not MQTT_SERVICE_API_KEY:
        logging.warning("MQTT_SERVICE_USERNAME/MQTT_SERVICE_API_KEY absents — manifeste non publié")
        return
    try:
        client = NexusClient.from_api_key(VK_URL, MQTT_HOST, MQTT_SERVICE_USERNAME, MQTT_SERVICE_API_KEY, MQTT_PORT)
        await client.publish("common/services/joshua", MANIFEST, retain=True)
        logging.info("Manifeste MQTT publié sur common/services/joshua")
    except Exception as e:
        logging.warning(f"Publication manifeste échouée: {e}")


async def start_server():
    global pipeline_args
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest='command')

    run_parser = subparsers.add_parser('run')
    run_parser.add_argument('--pipeline', required=True)
    run_parser.add_argument('--port', required=True)

    args = parser.parse_args()

    logging.info(f"🚀 Lancement du Pipeline: {args.pipeline}")
    pipeline_args = args.pipeline

    await publish_manifest()

    while True:
        try:
            connection = await websockets.serve(handle_client, '0.0.0.0', args.port, ping_timeout=None, extensions=[compression_config])
            await connection.wait_closed()
            logging.warning("WebSocket server closed unexpectedly, restarting in 1s...")
        except OSError as e:
            logging.error(f"WebSocket server bind error: {e}, retrying in 5s...")
            await asyncio.sleep(5)
            continue
        except Exception as e:
            logging.error(f"WebSocket server error: {e}, restarting in 1s...")
        await asyncio.sleep(1)

async def handle_client(websocket):
    cookie_header = websocket.request.headers.get("Cookie", "")
    session_cookie = _extract_cookie(cookie_header, "vk_session")

    query = parse_qs(urlsplit(websocket.request.path).query)
    conversation_id = (query.get("conversation_id") or [None])[0]

    nexus = None
    if session_cookie:
        nexus = await NexusClient.from_session_cookie(VK_URL, MQTT_HOST, session_cookie, MQTT_PORT)
        logging.info(f"Nouvelle connexion WebSocket: username={nexus.username}")
    else:
        logging.info("Nouvelle connexion WebSocket: pas de session cookie")

    session_id = str(uuid4())
    logging.info(f"Nouvelle session: {session_id} (conversation_id={conversation_id})")

    client = await Client.create(pipeline_args, websocket, nexus, session_id, conversation_id)
    connected_clients.add(client)
    await client.handle_message(websocket)
    
# Configure optimized deflate compression for real-time audio
compression_config = permessage_deflate.ServerPerMessageDeflateFactory(
    server_max_window_bits=12,      # Reduced memory usage (4KB vs 32KB)
    client_max_window_bits=12,      # Reduced memory usage
    server_no_context_takeover=False,  # Keep context for better compression
    client_no_context_takeover=False,  # Keep context for better compression
    compress_settings={'level': 4}   # Faster compression for real-time
)

def list_pipelines():
    loader = PipelineLoader()
    pipelines_info = loader.list_pipelines_info()
    
    for pipeline_info in pipelines_info:
        logging.info(f"📋 {pipeline_info['id']}")
        logging.info(f"   Nom: {pipeline_info['name']}")
        logging.info(f"   Description: {pipeline_info['description']}")
        logging.info()


def run_pipeline(pipeline_id: str, config_overrides=None, duration=None):
    logging.info("📦 Chargement des configurations...")
    
    loader = PipelineLoader()
    
    logging.info(f"🔧 Création du pipeline '{pipeline_id}'...")
    pipeline = loader.create_pipeline_from_definition(pipeline_id, config_overrides)
    
    if not pipeline:
        logging.info(f"❌ Pipeline '{pipeline_id}' non trouvé")
        available = loader.get_available_pipelines()
        logging.info(f"Pipelines disponibles: {available}")
        return None
    
    logging.info(f"✅ Pipeline créé avec {len(pipeline.steps)} étapes")
    for step_name in pipeline.steps.keys():
        logging.info(f"   - {step_name}")
    
    return pipeline

# Run the server
if __name__ == "__main__":
    asyncio.run(start_server())