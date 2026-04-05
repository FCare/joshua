import argparse
import asyncio
import sys
import logging
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


def list_pipelines():
    loader = PipelineLoader()
    pipelines_info = loader.list_pipelines_info()
    
    for pipeline_info in pipelines_info:
        print(f"📋 {pipeline_info['id']}")
        print(f"   Nom: {pipeline_info['name']}")
        print(f"   Description: {pipeline_info['description']}")
        print()


def run_pipeline(pipeline_id: str, config_overrides=None, duration=None):
    print("📦 Chargement des configurations...")
    
    loader = PipelineLoader()
    
    print(f"🔧 Création du pipeline '{pipeline_id}'...")
    pipeline = loader.create_pipeline_from_definition(pipeline_id, config_overrides)
    
    if not pipeline:
        print(f"❌ Pipeline '{pipeline_id}' non trouvé")
        available = loader.get_available_pipelines()
        print(f"Pipelines disponibles: {available}")
        return False
    
    print(f"✅ Pipeline créé avec {len(pipeline.steps)} étapes")
    for step_name in pipeline.steps.keys():
        print(f"   - {step_name}")
    
    async def execute():
        try:
            print("🚀 Démarrage du pipeline...")
            success = await pipeline.start()
            
            if not success:
                print("❌ Échec démarrage pipeline")
                return
                
            print("✅ Pipeline démarré avec succès")
            print("🔄 Pipeline en cours d'exécution...")
            
            if duration:
                print(f"⏱️  Arrêt automatique dans {duration} secondes")
                await asyncio.sleep(duration)
            else:
                print("💡 Appuyez sur Ctrl+C pour arrêter")
                while True:
                    await asyncio.sleep(1)
                    
        except KeyboardInterrupt:
            print("🛑 Arrêt demandé par l'utilisateur")
        except Exception as e:
            print(f"💥 Erreur pipeline: {e}")
        finally:
            print("🧹 Nettoyage du pipeline...")
            await pipeline.stop()
            print("✅ Pipeline arrêté")
    
    try:
        asyncio.run(execute())
        return True
    except Exception as e:
        print(f"💥 Erreur fatale: {e}")
        return False


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest='command')
    
    run_parser = subparsers.add_parser('run')
    run_parser.add_argument('--pipeline', required=True)
    run_parser.add_argument('--config')
    run_parser.add_argument('--duration', type=int)
    
    args = parser.parse_args()
    
    if args.command == 'list':
        list_pipelines()
        return 0
    elif args.command == 'run':
        config_overrides = None
        if args.config:
            import json
            config_overrides = json.loads(args.config)
        
        print(f"🚀 Lancement du Pipeline: {args.pipeline}")
        print()
        
        success = run_pipeline(args.pipeline, config_overrides, args.duration)
        return 0 if success else 1
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())