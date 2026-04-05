import argparse
import asyncio
import sys
import logging
import websockets
from websockets.extensions import permessage_deflate

from pipeline_loader import PipelineLoader

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

class Client():
    def __init__(self, pipeline: str, websocket):
        self.pipeline = run_pipeline(pipeline)
        self.pipeline_input = self.pipeline.get_step("websocket_server")
        self.pipeline_input.set_ws_callback(self.sendToClient)
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
            logging.info(f"remaining number of client: {len(connected_clients)}")
        return

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

    connection = await websockets.serve(handle_client, '0.0.0.0', args.port, ping_timeout=None, extensions=[compression_config])
    try:
        await connection.wait_closed()
    except:
        pass
    sys.exit("Server closed")

async def handle_client(websocket):
    client = Client(pipeline_args, websocket)
    connected_clients.add(client)            
    client.handle_message(websocket)
    
    

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
    
    async def execute():
        try:
            logging.info("🚀 Démarrage du pipeline...")
            success = await pipeline.start()
            
            if not success:
                logging.info("❌ Échec démarrage pipeline")
                return
                
            logging.info("✅ Pipeline démarré avec succès")
            logging.info("🔄 Pipeline en cours d'exécution...")
            
            if duration:
                logging.info(f"⏱️  Arrêt automatique dans {duration} secondes")
                await asyncio.sleep(duration)
            else:
                logging.info("💡 Appuyez sur Ctrl+C pour arrêter")
                while True:
                    await asyncio.sleep(1)
                    
        except KeyboardInterrupt:
            logging.info("🛑 Arrêt demandé par l'utilisateur")
        except Exception as e:
            logging.info(f"💥 Erreur pipeline: {e}")
        finally:
            logging.info("🧹 Nettoyage du pipeline...")
            await pipeline.stop()
            logging.info("✅ Pipeline arrêté")
    
    try:
        asyncio.run(execute())
        return pipeline
    except Exception as e:
        logging.info(f"💥 Erreur fatale: {e}")
        return None


# Run the server
if __name__ == "__main__":
    asyncio.run(start_server())