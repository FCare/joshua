import argparse
import asyncio
import os
import sys
import logging
import websockets
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
                "password": "string (cookie de session VK — utilisable comme mot de passe MQTT)",
                "private_topics": "list[string] — topics privés de l'utilisateur (discussions, agent_topics, …)",
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

def _extract_cookie(cookie_header: str, name: str) -> str:
    for part in cookie_header.split(";"):
        k, _, v = part.strip().partition("=")
        if k.strip() == name:
            return v.strip()
    return ""


class Client():

    @classmethod
    async def create(cls, pipeline_name: str, websocket, nexus: NexusClient | None):
        """Factory method async pour créer un Client"""
        pipeline = run_pipeline(pipeline_name)
        if not pipeline:
            raise ValueError(f"Impossible de créer le pipeline: {pipeline_name}")

        success = await pipeline.start()
        if not success:
            raise ValueError(f"Impossible de démarrer le pipeline: {pipeline_name}")

        return cls(pipeline, websocket, nexus)

    def __init__(self, pipeline: str, websocket, nexus: NexusClient | None):
        self.pipeline = pipeline
        self.pipeline_input = self.pipeline.get_step("websocket_server")
        self.ws = websocket
        username = nexus.username if nexus else "anonymous"
        self.pipeline_input.set_ws_callback(self.sendToClient, username, nexus)

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

    connection = await websockets.serve(handle_client, '0.0.0.0', args.port, ping_timeout=None, extensions=[compression_config])
    try:
        await connection.wait_closed()
    except:
        pass
    sys.exit("Server closed")

async def handle_client(websocket):
    cookie_header = websocket.request.headers.get("Cookie", "")
    session_cookie = _extract_cookie(cookie_header, "vk_session")

    nexus = None
    if session_cookie:
        nexus = await NexusClient.from_session_cookie(VK_URL, MQTT_HOST, session_cookie, MQTT_PORT)
        logging.info(f"Nouvelle connexion WebSocket: username={nexus.username}")
    else:
        logging.info("Nouvelle connexion WebSocket: pas de session cookie")

    client = await Client.create(pipeline_args, websocket, nexus)
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