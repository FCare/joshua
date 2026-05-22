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
    # Priorité du message (0 = critique, 10 = haute, 20 = normale, 30 = basse)
    priority: int = 20
    
    def __new__(cls, *args, **kwargs):
        # Empêche l'instanciation directe de BaseMessage
        if cls is BaseMessage:
            raise TypeError("Cannot instantiate BaseMessage directly. Use specialized subclasses instead.")
        return super().__new__(cls)
    
    def copy(self) -> 'BaseMessage':
        """
        Crée une copie du message avec des métadonnées mises à jour.
   
        Returns:
            Nouvelle instance du même type de message
        """

        
        # Utiliser dataclasses.replace pour créer une copie avec les nouvelles métadonnées
        from dataclasses import replace
        return replace(self)