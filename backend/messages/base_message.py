from abc import ABC
from dataclasses import dataclass, field
from typing import Optional

from .message_uuid import MessageUUID


@dataclass(frozen=True)
class BaseMessage(ABC):
    """Classe de base abstraite pour tous les messages du système."""

    # kw_only évite le conflit d'héritage dataclass entre champs avec/sans défaut
    id: Optional[MessageUUID] = field(default=None, kw_only=True)

    def __new__(cls, *args, **kwargs):
        if cls is BaseMessage:
            raise TypeError("Cannot instantiate BaseMessage directly. Use specialized subclasses instead.")
        return super().__new__(cls)

    def is_more_recent_than(self, other: 'BaseMessage') -> bool:
        """Retourne True si ce message appartient à une session ASR plus récente."""
        if self.id is None or other.id is None:
            raise ValueError("Cannot compare messages without an id")
        return self.id > other.id

    def copy(self) -> 'BaseMessage':
        from dataclasses import replace
        return replace(self)