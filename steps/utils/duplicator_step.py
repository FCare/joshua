import logging
import time
from typing import Optional, Dict, List
from pipeline_framework import PipelineStep
from messages.base_message import Message, InputMessage, OutputMessage
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
        """Handler pour traiter et dupliquer les messages via ChunkQueue"""
        try:
            logger.info(f"Duplicator received message: {type(input_message).__name__}")
            
            # Vérifie qu'on a des queues de sortie
            if not self.output_queues:
                logger.warning("Aucune output queue configurée pour le duplicator")
                return
            
            # Préserve les métadonnées originales
            original_metadata = {}
            if hasattr(input_message, 'metadata') and input_message.metadata:
                original_metadata = input_message.metadata.copy()
            
            # Duplique le message vers toutes les output_queues
            duplicated_count = 0
            for i, output_queue in enumerate(self.output_queues):
                try:
                    # Crée une copie du message pour chaque sortie
                    if hasattr(input_message, 'data'):
                        # Message avec data (ex: OutputMessage)
                        duplicated_message = OutputMessage(
                            result=input_message.data,
                            metadata=original_metadata
                        )
                    else:
                        # Autre type de message
                        duplicated_message = input_message
                    
                    # Ajoute info de duplication dans metadata
                    if hasattr(duplicated_message, 'metadata'):
                        if duplicated_message.metadata is None:
                            duplicated_message.metadata = {}
                        duplicated_message.metadata['duplicator_branch'] = i
                        duplicated_message.metadata['duplicated_at'] = time.time()
                    
                    # Envoie vers la queue de sortie
                    output_queue.enqueue(duplicated_message)
                    duplicated_count += 1
                    logger.debug(f"Message dupliqué vers branche {i}")
                    
                except Exception as e:
                    logger.error(f"Erreur duplication vers branche {i}: {e}")
            
            logger.info(f"Message dupliqué vers {duplicated_count}/{len(self.output_queues)} branches")
            
        except Exception as e:
            logger.error(f"Erreur handling duplicator input: {e}")