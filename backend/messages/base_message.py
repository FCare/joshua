from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class BaseMessage(ABC):
    """Classe de base abstraite pour tous les messages du système.
    
    Empêche l'instanciation directe et force l'utilisation des sous-classes spécialisées.
    Les instances sont immutables après création.
    """
    data: Any
    metadata: Optional[Dict] = None
    
    def __new__(cls, *args, **kwargs):
        # Empêche l'instanciation directe de BaseMessage
        if cls is BaseMessage:
            raise TypeError("Cannot instantiate BaseMessage directly. Use specialized subclasses instead.")
        return super().__new__(cls)
    
    def copy(self, new_metadata: Optional[Dict] = None) -> 'BaseMessage':
        """
        Crée une copie du message avec des métadonnées mises à jour.
        
        Args:
            new_metadata: Nouvelles métadonnées (fusionnées avec les existantes)
            
        Returns:
            Nouvelle instance du même type de message
        """
        # Fusionner les métadonnées
        updated_metadata = self.metadata.copy() if self.metadata else {}
        if new_metadata:
            updated_metadata.update(new_metadata)
        
        # Créer une nouvelle instance de la même classe
        return self.__class__(data=self.data, metadata=updated_metadata)