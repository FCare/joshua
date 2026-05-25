import uuid
import time
from dataclasses import dataclass, field


@dataclass(frozen=True, order=True)
class MessageUUID:
    """UUID ordonné temporellement identifiant une session de parole ASR.

    Seuls les steps ASR doivent instancier cette classe.
    Les autres steps propagent l'id reçu dans les messages entrants.

    L'ordre est déterminé par le timestamp nanoseconde de création (_timestamp_ns).
    Le champ _uid (uuid4) fournit un identifiant unique et est ignoré dans les comparaisons.
    """
    _timestamp_ns: int = field(default_factory=time.time_ns)
    _uid: uuid.UUID = field(default_factory=uuid.uuid4, compare=False)

    def __str__(self) -> str:
        return str(self._uid)

    def __repr__(self) -> str:
        return f"MessageUUID({self._uid})"
