import argparse
import asyncio
import json
import os
import sys
import time
import logging
import httpx
import websockets
from urllib.parse import urlsplit, parse_qs
from uuid import uuid4
from websockets.extensions import permessage_deflate

from pipeline_loader import PipelineLoader
from nexus_client import NexusClient

MQTT_HOST = os.environ.get("MQTT_HOST", "mosquitto-broker")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))

# Authentik OAuth
AUTHENTIK_URL = os.environ.get("AUTHENTIK_URL", "https://sso.caronboulme.fr")
JOSHUA_CLIENT_ID = os.environ.get("JOSHUA_CLIENT_ID")
JOSHUA_CLIENT_SECRET = os.environ.get("JOSHUA_CLIENT_SECRET")

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
                "authenticated": "bool (jamais le vrai token MQTT — voir mqtt_step.py)",
                "private_topics": "list[{agent, topics[]}] — topics privés de l'utilisateur par agent",
            },
        }
    ],
}


async def _new_mqtt_nexus() -> NexusClient | None:
    """Obtient un token OAuth Client Credentials via Authentik et construit un
    NexusClient authentifié — mosquitto-auth-authentik valide le mot de passe MQTT
    comme un JWT Authentik, un token frais est donc nécessaire à chaque connexion.
    Utilisé pour les connexions MQTT "service" de ce process (sweeper TTL,
    manifeste), qui n'ont pas de session websocket/nexus par-connexion à
    réutiliser. Même pattern que panoramix/server.py::_new_mqtt_nexus."""
    if not (JOSHUA_CLIENT_ID and JOSHUA_CLIENT_SECRET):
        logging.warning("JOSHUA_CLIENT_ID/JOSHUA_CLIENT_SECRET absents — MQTT service désactivé")
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{AUTHENTIK_URL}/application/o/token/",
                data={
                    "grant_type": "client_credentials",
                    "client_id": JOSHUA_CLIENT_ID,
                    "client_secret": JOSHUA_CLIENT_SECRET,
                },
            )
            if resp.status_code != 200:
                logging.error(f"Échec obtention token OAuth: {resp.status_code} {resp.text}")
                return None
            access_token = resp.json()["access_token"]

        return await NexusClient.from_authentik_token(
            AUTHENTIK_URL, MQTT_HOST, access_token,
            JOSHUA_CLIENT_ID, JOSHUA_CLIENT_SECRET, MQTT_PORT,
        )
    except Exception as e:
        logging.error(f"Échec création NexusClient OAuth: {e}")
        return None

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
_conversation_histories: dict[str, tuple[float, list, str]] = {}  # conversation_id -> (ts, history, username)

# Client MQTT service partagé, créé paresseusement, utilisé uniquement par le sweeper TTL
# ci-dessous (publication de discussions dont personne n'est plus propriétaire d'une
# connexion websocket active pour le faire via son propre nexus).
_service_nexus: NexusClient | None = None


def _load_conversation_history(conversation_id: str | None) -> list:
    if not conversation_id:
        return []
    entry = _conversation_histories.get(conversation_id)
    return list(entry[1]) if entry else []


def _save_conversation_history(conversation_id: str | None, history: list, username: str):
    if not conversation_id:
        return
    if not history:
        _conversation_histories.pop(conversation_id, None)
        return
    _conversation_histories[conversation_id] = (time.time(), history, username)


async def _publish_discussion(nexus: NexusClient, username: str, history: list):
    if not history:
        return
    try:
        await nexus.publish(f"users/{username}/discussions", history)
        logging.info(f"[{username}] Discussion publiée sur users/{username}/discussions ({len(history)} messages)")
    except Exception as e:
        logging.error(f"[{username}] Échec publication discussion: {e}")


async def _conversation_ttl_sweeper():
    """Publie vers agent-profiler (topic users/{username}/discussions) les conversations
    dont le conversation_id a expiré (30 min sans reconnexion) — la meilleure définition
    disponible de "discussion réellement terminée" : contrairement à une simple
    déconnexion websocket (qui peut n'être qu'une coupure réseau suivie d'une reconnexion
    sur le même conversation_id grâce à la persistance de conversation), ici on sait que
    rien n'est revenu la reprendre depuis 30 min. Complète le déclenchement explicite sur
    logout (voir Client._publish_discussion_now) pour le cas où l'utilisateur ferme
    l'onglet/perd la connexion sans jamais se déconnecter proprement."""
    global _service_nexus
    while True:
        await asyncio.sleep(60)
        now = time.time()
        expired = [cid for cid, (ts, _, _) in _conversation_histories.items() if now - ts > CONVERSATION_HISTORY_TTL]
        if not expired:
            continue
        if _service_nexus is None:
            _service_nexus = await _new_mqtt_nexus()
            if _service_nexus is None:
                logging.warning("Sweeper TTL conversations: nexus MQTT indisponible, publication ignorée")
                for cid in expired:
                    _conversation_histories.pop(cid, None)
                continue
        for cid in expired:
            _, history, username = _conversation_histories.pop(cid)
            await _publish_discussion(_service_nexus, username, history)


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
        self.nexus = nexus
        self.username = nexus.username if nexus else "anonymous"
        self.session_id = session_id or str(uuid4())
        self.conversation_id = conversation_id
        self._discussion_published = False

        # Les topics MQTT (agent_topics, requêtes/réponses des agents) sont scopés sur
        # cet identifiant — conversation_id plutôt que session_id : conversation_id est
        # stable à travers les reconnexions (persisté en localStorage côté client), alors
        # que session_id est un UUID neuf à chaque connexion WebSocket. Scoper sur
        # session_id orphelinait toute réponse d'outil encore en vol au moment d'une
        # reconnexion (le topic de réponse n'était plus écouté par personne). Fallback sur
        # session_id si conversation_id est absent (ex: navigation privée sans localStorage).
        mqtt_scope_id = self.conversation_id or self.session_id

        # set_nexus must happen before set_ws_callback: set_ws_callback sends
        # UserConnectionMessage which triggers user_connected on MQTT; the
        # mqtt_step must already be subscribed to agent_topics at that point.
        mqtt_step = self.pipeline.get_step("mqtt_step")
        if mqtt_step and nexus:
            mqtt_step.set_nexus(nexus, mqtt_scope_id)

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

    async def _publish_discussion_now(self):
        """Déclenché sur logout explicite (voir handle_message) : publie immédiatement la
        discussion pour agent-profiler au lieu d'attendre les 30 min du sweeper TTL, et
        retire l'entrée de _conversation_histories pour éviter une double publication si
        le sweeper la voyait passer plus tard."""
        if not self.chat_step or not self.nexus:
            return
        history = self.chat_step.export_history()
        await _publish_discussion(self.nexus, self.username, history)
        if self.conversation_id:
            _conversation_histories.pop(self.conversation_id, None)
        self._discussion_published = True

    async def handle_message(self, websocket):
        try:
            # Listen for messages from the chat client
            async for message in websocket:
                if isinstance(message, str):
                    try:
                        data = json.loads(message)
                    except (json.JSONDecodeError, TypeError):
                        data = None
                    if isinstance(data, dict) and data.get("type") == "logout":
                        await self._publish_discussion_now()
                        continue
                self.pipeline_input.handle_websocket(message)
        except websockets.exceptions.ConnectionClosed as ie:
            logging.info(f"Client connection closed: {ie}")
        finally:
            # Remove the client from the set of connected clients
            connected_clients.remove(self)

            # Sur logout explicite, la discussion a déjà été publiée et l'entrée retirée
            # de _conversation_histories — une simple déconnexion (reconnexion possible,
            # cf. persistance de conversation) ne republie PAS, seul le sweeper TTL ou un
            # futur logout explicite le fera.
            if not self._discussion_published and self.chat_step and self.conversation_id:
                _save_conversation_history(self.conversation_id, self.chat_step.export_history(), self.username)

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
    client = await _new_mqtt_nexus()
    if client is None:
        logging.warning("Nexus MQTT indisponible — manifeste non publié")
        return
    try:
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
    asyncio.create_task(_conversation_ttl_sweeper())

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
    query = parse_qs(urlsplit(websocket.request.path).query)
    conversation_id = (query.get("conversation_id") or [None])[0]
    access_token = (query.get("access_token") or [None])[0]

    nexus = None

    if access_token and JOSHUA_CLIENT_ID and JOSHUA_CLIENT_SECRET:
        try:
            nexus = await NexusClient.from_authentik_token(
                AUTHENTIK_URL, MQTT_HOST, access_token,
                JOSHUA_CLIENT_ID, JOSHUA_CLIENT_SECRET, MQTT_PORT
            )
            logging.info(f"Nouvelle connexion WebSocket (OAuth): username={nexus.username}")
        except Exception as e:
            logging.error(f"Échec création NexusClient OAuth: {e}")

    if not nexus:
        logging.warning("Nouvelle connexion WebSocket: pas d'authentification valide")

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