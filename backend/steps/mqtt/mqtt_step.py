import asyncio
import json
import logging
import time
from typing import Optional, Dict

CONTEXT_OVERRIDE_TTL = 30 * 60  # 30 min watchdog

from pipeline_framework import PipelineStep
from messages.websocket_message import UserConnectionMessage
from messages.chat_message import MqttWriteMessage

logger = logging.getLogger(__name__)

PRIVATE_TOPICS = [
    {
        "agent": "joshua",
        "topics": [
            {
                "topic": "users/{username}/discussions",
                "description": "Historique de conversation",
                "access": "read",
                "format": [{"role": "user | assistant", "content": "string"}],
            },
            {
                "topic": "users/{username}/{session_id}/agent_topics",
                "description": "Topics publiés par les agents pour cette session",
                "access": "write",
                "format": [{"agent": "string", "topics": [{"topic": "string", "description": "string", "access": "read | write | readwrite", "format": {}}]}],
            },
        ],
    }
]


class MqttStep(PipelineStep):

    def __init__(self, name: str = "MqttStep", config: Optional[Dict] = None):
        super().__init__(name, config, handler=self._handle_message)
        self._nexus = None
        self._loop = None
        self._session_id: str = "default"
        self._read_topics_meta: dict = {}
        self._write_topics_meta: dict = {}
        self._context_override_topics: set = set()
        self._watchdog_task: asyncio.Task | None = None
        self._pending_writes: dict = {}  # response_topic → (write_topic, payload)
        self._alias_to_topic: dict = {}  # alias stable (ex: "news/request") → topic MQTT réel

    def set_nexus(self, nexus, session_id: str = None) -> None:
        self._nexus = nexus
        self._loop = asyncio.get_event_loop()
        self._session_id = session_id or "default"
        username = nexus.username
        agent_topics_topic = f"users/{username}/{self._session_id}/agent_topics"
        nexus.subscribe(agent_topics_topic, self._on_agent_topics)
        nexus.start_listening()
        logger.info(f"MqttStep: souscrit à {agent_topics_topic} (session={self._session_id})")

    def init(self) -> bool:
        return True

    def cleanup(self):
        if self._nexus:
            self._nexus.stop_listening()
        if hasattr(self, "input_queue") and self.input_queue:
            self.input_queue.stop()

    def _handle_message(self, message):
        if isinstance(message, UserConnectionMessage):
            if not self._nexus or not self._loop:
                logger.warning("MqttStep: nexus non configuré, message ignoré")
                return
            self._handle_user_connected(message)
        elif isinstance(message, MqttWriteMessage):
            if not self._nexus or not self._loop:
                logger.warning("MqttStep: nexus non configuré, MqttWriteMessage ignoré")
                return
            self._handle_mqtt_write(message)

    def _handle_user_connected(self, message: UserConnectionMessage):
        username = message.username
        session_id = self._session_id
        payload = {
            "event": "user_connected",
            "username": username,
            "session_id": session_id,
            # Booléen de présence, jamais le vrai token MQTT : ce topic
            # (common/user_connected) est lu par tout agent abonné — y diffuser
            # self._nexus.password (le token OAuth réel de CE client) permettrait à
            # n'importe quel agent authentifié de s'authentifier en usurpant cette
            # identité MQTT. Aucun consommateur (wiki-agent, weather, contes-agent)
            # n'utilise ce champ au-delà d'un test de présence.
            "authenticated": bool(self._nexus.password),
            "private_topics": [
                {
                    "agent": entry["agent"],
                    "topics": [
                        {**t, "topic": t["topic"].format(username=username, session_id=session_id)}
                        for t in entry["topics"]
                    ],
                }
                for entry in PRIVATE_TOPICS
            ],
        }
        asyncio.run_coroutine_threadsafe(
            self._nexus.publish("common/user_connected", payload),
            self._loop,
        )
        logger.info(f"MQTT user_connected publié pour {username}")

    @staticmethod
    def _topic_alias(topic: str) -> str:
        """Alias stable (ex: 'news/request') dérivé d'un topic MQTT complet (ex:
        'users/alice/6e6afc97-.../news/request') en retirant le préfixe
        users/{username}/{session_id}/, qui varie par utilisateur et par session.
        Exposer cet alias au LLM plutôt que le topic complet garde la description
        et l'enum de l'outil write_topic identiques d'une conversation à l'autre,
        ce qui permet au cache de préfixe de vLLM de s'appliquer (sinon 0% de hit
        rate, ~8s de préfill en plus à chaque nouvelle conversation)."""
        parts = topic.split("/")
        return "/".join(parts[3:]) if len(parts) > 3 else topic

    def _handle_mqtt_write(self, message: MqttWriteMessage):
        real_topic = self._alias_to_topic.get(message.topic, message.topic)
        asyncio.run_coroutine_threadsafe(
            self._nexus.publish(real_topic, message.payload),
            self._loop,
        )
        logger.info(f"MqttStep: publié sur {real_topic} (alias={message.topic})")
        # Track pending: if this write topic has a response topic, save for replay on reconnect
        meta = self._write_topics_meta.get(real_topic, {})
        response_topic = meta.get("response_topic")
        if response_topic:
            self._pending_writes[response_topic] = (real_topic, message.payload)

    async def _on_agent_topics(self, topic: str, payload):
        if not isinstance(payload, list):
            return

        # First pass: collect write topics and which read topics are responses
        response_topic_set = set()
        write_changed = False
        for agent_entry in payload:
            for t in agent_entry.get("topics", []):
                if t.get("access") == "write":
                    write_topic = t["topic"]
                    response_topic = t.get("response_topic")
                    new_meta = {
                        "description": t.get("description", ""),
                        "format": t.get("format", {}),
                        "response_topic": response_topic,
                    }
                    if self._write_topics_meta.get(write_topic) != new_meta:
                        self._write_topics_meta[write_topic] = new_meta
                        write_changed = True
                    if response_topic:
                        response_topic_set.add(response_topic)

        # Second pass: subscribe to context_override topics
        for agent_entry in payload:
            for t in agent_entry.get("topics", []):
                if t.get("access") == "context_override":
                    ctx_topic = t["topic"]
                    if ctx_topic not in self._context_override_topics:
                        self._context_override_topics.add(ctx_topic)
                        self._nexus.subscribe(ctx_topic, self._on_context_override)
                        logger.info(f"MqttStep: souscription context_override: {ctx_topic}")

        # Third pass: subscribe to read topics
        for agent_entry in payload:
            for t in agent_entry.get("topics", []):
                if t.get("access") == "read":
                    read_topic = t["topic"]
                    already_subscribed = read_topic in self._read_topics_meta
                    self._read_topics_meta[read_topic] = {
                        "description": t.get("description", ""),
                        "format": t.get("format", {}),
                        "is_response": read_topic in response_topic_set,
                    }
                    if not already_subscribed:
                        logger.info(f"MqttStep: souscription topic read-access: {read_topic} ({t.get('description', '')})")
                        self._nexus.subscribe(read_topic, self._on_read_topic)
                    else:
                        logger.debug(f"MqttStep: déjà souscrit à {read_topic}, skip")

        if write_changed and self._write_topics_meta:
            self._send_write_tool_update()
            # Replay any pending requests that agents may have missed (e.g. after agent restart)
            if self._pending_writes:
                logger.info(f"MqttStep: {len(self._pending_writes)} requête(s) en attente — republication")
                for response_topic, (write_topic, payload) in list(self._pending_writes.items()):
                    if write_topic in self._write_topics_meta:
                        asyncio.run_coroutine_threadsafe(
                            self._nexus.publish(write_topic, payload),
                            self._loop,
                        )
                        logger.info(f"MqttStep: republié {write_topic} (en attente de {response_topic})")

    def _send_write_tool_update(self):
        self._alias_to_topic = {self._topic_alias(t): t for t in self._write_topics_meta}
        topic_lines = []
        # Union de tous les champs de tous les 'format' déclarés par les agents, exposés
        # comme paramètres de PREMIER NIVEAU de write_topic (plutôt qu'imbriqués sous un
        # 'payload' opaque) : le modèle remplit directement les champs pertinents pour le
        # topic choisi, sans avoir à construire lui-même un objet JSON imbriqué — c'est
        # cette construction manuelle qui causait des erreurs récurrentes d'imbrication
        # (payload contenant un payload, topic égaré à l'intérieur, etc.), quel que soit
        # le backend LLM utilisé.
        merged_properties: dict[str, list[str]] = {}
        for write_topic, meta in self._write_topics_meta.items():
            alias = self._topic_alias(write_topic)
            line = f"- {alias} : {meta['description']}. Format: {json.dumps(meta['format'], ensure_ascii=False)}"
            if meta.get("response_topic"):
                line += f". La réponse arrive ensuite automatiquement via: {self._topic_alias(meta['response_topic'])}"

            fmt = meta.get("format")
            if isinstance(fmt, dict):
                if "type" in fmt:
                    # 'type' n'est qu'un paramètre optionnel parmi d'autres dans le schéma
                    # (il ne s'applique pas à tous les topics) — sans ce rappel explicite,
                    # le modèle l'omet facilement, et la requête échoue silencieusement
                    # (dispatch renvoie 'type inconnu') sans qu'aucune erreur ne remonte à
                    # l'utilisateur, qui reçoit alors une réponse inventée.
                    line += " ⚠️ Le paramètre 'type' est OBLIGATOIRE pour ce topic — sans lui la requête échoue silencieusement."
                for key, val in fmt.items():
                    desc = val if isinstance(val, str) else f"exemple: {json.dumps(val, ensure_ascii=False)}"
                    merged_properties.setdefault(key, []).append(f"[{alias}] {desc}")
            topic_lines.append(line)
        description = (
            "Envoie une requête à l'agent du topic choisi (descriptions ci-dessous). Remplis "
            "en paramètres de PREMIER NIVEAU uniquement les champs du 'Format' de CE topic — "
            "jamais regroupés dans un objet imbriqué. Un champ 'type' listé est OBLIGATOIRE : "
            "sans lui la requête échoue silencieusement.\n"
            "Topics disponibles :\n" + "\n".join(topic_lines)
        )

        properties = {
            "topic": {
                "type": "string",
                "enum": sorted(self._alias_to_topic.keys()),
                "description": "Le topic à cibler parmi ceux listés",
            },
        }
        for key, descs in merged_properties.items():
            properties[key] = {"description": " | ".join(descs)}

        tool_definition = {
            "type": "function",
            "function": {
                "name": "write_topic",
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": ["topic"],
                },
            },
        }

        response_map = {
            self._topic_alias(topic): meta["response_topic"]
            for topic, meta in self._write_topics_meta.items()
            if meta.get("response_topic")
        }
        from messages.chat_message import MqttToolUpdateMessage
        if self.output_queue:
            self.output_queue.enqueue(MqttToolUpdateMessage(
                tool_definition=tool_definition,
                response_map=response_map,
            ))
            logger.info(f"MqttStep: tool write_topic mis à jour ({len(self._write_topics_meta)} topics write, {len(response_map)} avec réponse)")

    async def _on_read_topic(self, topic: str, payload):
        if not isinstance(payload, (dict, list)):
            return
        meta = self._read_topics_meta.get(topic, {})
        logger.info(f"MqttStep: données reçues sur {topic} ({meta.get('description', '')})")
        # Response received: remove from pending queue
        self._pending_writes.pop(topic, None)
        from messages.chat_message import AgentTopicMessage
        if self.output_queue:
            self.output_queue.enqueue(AgentTopicMessage(
                topic=topic,
                description=meta.get("description", ""),
                payload=payload,
                is_response=meta.get("is_response", False),
            ))

    async def _on_context_override(self, topic: str, payload):
        if not isinstance(payload, dict):
            return
        from messages.chat_message import SystemPromptMessage
        prompt = payload.get("prompt")
        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
            self._watchdog_task = None
        if self.output_queue:
            self.output_queue.enqueue(SystemPromptMessage(prompt=prompt))
        if prompt is not None:
            logger.info(f"MqttStep: context override activé — watchdog {CONTEXT_OVERRIDE_TTL}s")
            self._watchdog_task = asyncio.create_task(self._context_watchdog())
        else:
            logger.info("MqttStep: context override restauré")

    async def _context_watchdog(self):
        await asyncio.sleep(CONTEXT_OVERRIDE_TTL)
        logger.warning("MqttStep: watchdog context override déclenché — restauration du prompt original")
        from messages.chat_message import SystemPromptMessage
        if self.output_queue:
            self.output_queue.enqueue(SystemPromptMessage(prompt=None))
        self._watchdog_task = None

