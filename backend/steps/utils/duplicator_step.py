import logging
import time
from typing import Optional, Dict, List
from pipeline_framework import PipelineStep
from messages.base_message import BaseMessage
from messages.duplicator_message import InputMessage, OutputMessage
from utils.chunk_queue import ChunkQueue

logger = logging.getLogger(__name__)


class DuplicatorStep(PipelineStep):
    """
    Step qui duplique les messages reçus vers plusieurs output_queues.
    Permet de créer des branches dans le pipeline pour traitement parallèle.
    """
    
    def __init__(self, name: str = "Duplicator", config: Optional[Dict] = None):
        super().__init__(name, config, handler=self._handle_input_message)
        
        # Configuration
        self.duplication_count = config.get("duplication_count", 2) if config else 2
        
        # Liste des output_queues (sera configurée par le pipeline)
        self.output_queues = []
        
        logger.info(f"DuplicatorStep '{name}' configuré pour {self.duplication_count} sorties")
    
    def init(self) -> bool:
        """Initialise le duplicator"""
        try:
            logger.info(f"Duplicator '{self.name}' initialisé avec {len(self.output_queues)} queues de sortie")
            return True
        except Exception as e:
            logger.error(f"Erreur initialisation Duplicator: {e}")
            return False
    
    def cleanup(self):
        """Nettoyage du duplicator"""
        try:
            if hasattr(self, 'input_queue') and self.input_queue:
                self.input_queue.stop()
            logger.info(f"Duplicator '{self.name}' nettoyé")
        except Exception as e:
            logger.error(f"Erreur nettoyage Duplicator: {e}")
    
    def add_output_queue(self, output_queue: ChunkQueue):
        """Ajoute une queue de sortie"""
        self.output_queues.append(output_queue)
        logger.info(f"Output queue ajoutée au duplicator. Total: {len(self.output_queues)}")
    
    def _handle_input_message(self, input_message):
        """Handler pour traiter et dupliquer les messages via ChunkQueue - accepte tous types"""
        try:
            logger.info(f"🔄 Duplicator received message: {type(input_message).__name__}")
            
            # Vérifie qu'on a des queues de sortie
            if not self.output_queues:
                logger.warning("Aucune output queue configurée pour le duplicator")
                return
            
            # Duplique le message vers toutes les output_queues
            duplicated_count = 0
            for i, output_queue in enumerate(self.output_queues):
                try:
                    # Utiliser la méthode copy() héritée avec ajout des infos de duplication
                    duplication_metadata = {
                        'duplicator_branch': i,
                        'duplicated_at': time.time()
                    }
                    
                    # Fusionner avec les métadonnées existantes
                    if input_message.metadata:
                        duplication_metadata.update(input_message.metadata)
                    
                    duplicated_message = input_message.copy(new_metadata=duplication_metadata)
                    
                    # Envoie vers la queue de sortie
                    logger.info(f"🐛 DEBUG: Duplicator about to enqueue to branch {i}, queue: {output_queue}")
                    output_queue.enqueue(duplicated_message)
                    duplicated_count += 1
                    logger.info(f"🐛 DEBUG: Message successfully duplicated to branch {i}")
                    
                except Exception as e:
                    logger.error(f"Erreur duplication vers branche {i}: {e}")
            
            logger.info(f"Message dupliqué vers {duplicated_count}/{len(self.output_queues)} branches")
            
        except Exception as e:
            logger.error(f"Erreur handling duplicator input: {e}")