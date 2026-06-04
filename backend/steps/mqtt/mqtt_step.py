import asyncio
import json
import logging
import time
from typing import Optional, Dict

CONTEXT_OVERRIDE_TTL = 30 * 60  # 30 min watchdog

from pipeline_framework import PipelineStep
from messages.websocket_message import UserConnectionMessage
from messages.chat_message import DiscussionHistoryMessage, MqttWriteMessage

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
                "topic": "users/{username}/agent_topics",
                "description": "Topics publiés par les agents",
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
        self._read_topics_meta: dict = {}
        self._write_topics_meta: dict = {}
        self._context_override_topics: set = set()
        self._watchdog_task: asyncio.Task | None = None
        self._pending_writes: dict = {}  # response_topic → (write_topic, payload)
        self._agent_intents: dict = {}   # agent_name → list of intents

    def set_nexus(self, nexus) -> None:
        self._nexus = nexus
        self._loop = asyncio.get_event_loop()
        username = nexus.username
        agent_topics_topic = f"users/{username}/agent_topics"
        nexus.subscribe(agent_topics_topic, self._on_agent_topics)
        nexus.start_listening()
        logger.info(f"MqttStep: souscrit à {agent_topics_topic}")

    def init(self) -> bool:
        return True

    def cleanup(self):
        if hasattr(self, "input_queue") and self.input_queue:
            self.input_queue.stop()

    def _handle_message(self, message):
        if isinstance(message, UserConnectionMessage):
            if not self._nexus or not self._loop:
                logger.warning("MqttStep: nexus non configuré, message ignoré")
                return
            self._handle_user_connected(message)
        elif isinstance(message, DiscussionHistoryMessage):
            if not self._nexus or not self._loop:
                return
            self._handle_discussion_history(message)
        elif isinstance(message, MqttWriteMessage):
            if not self._nexus or not self._loop:
                logger.warning("MqttStep: nexus non configuré, MqttWriteMessage ignoré")
                return
            self._handle_mqtt_write(message)

    def _handle_user_connected(self, message: UserConnectionMessage):
        username = message.username
        payload = {
            "event": "user_connected",
            "username": username,
            "password": self._nexus.password,
            "private_topics": [
                {
                    "agent": entry["agent"],
                    "topics": [
                        {**t, "topic": t["topic"].format(username=username)}
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

    def _handle_mqtt_write(self, message: MqttWriteMessage):
        asyncio.run_coroutine_threadsafe(
            self._nexus.publish(message.topic, message.payload),
            self._loop,
        )
        logger.info(f"MqttStep: publié sur {message.topic}")
        # Track pending: if this write topic has a response topic, save for replay on reconnect
        meta = self._write_topics_meta.get(message.topic, {})
        response_topic = meta.get("response_topic")
        if response_topic:
            self._pending_writes[response_topic] = (message.topic, message.payload)

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

        # Collect intents declared by agents
        intents_changed = False
        for agent_entry in payload:
            agent_name = agent_entry.get("agent", "")
            agent_intents = agent_entry.get("intents", [])
            if not agent_intents:
                continue
            # Enrich each intent with its resolved write_topic and response_topic
            # An intent may carry an explicit write_topic (e.g. when an agent has multiple write topics)
            enriched = []
            for intent in agent_intents:
                explicit_write = intent.get("write_topic")
                if explicit_write:
                    response_topic = next(
                        (t.get("response_topic") for t in agent_entry.get("topics", [])
                         if t.get("access") == "write" and t["topic"] == explicit_write),
                        None,
                    )
                    enriched.append({**intent, "write_topic": explicit_write, "response_topic": response_topic})
                else:
                    write_topic = None
                    response_topic = None
                    for t in agent_entry.get("topics", []):
                        if t.get("access") == "write":
                            write_topic = t["topic"]
                            response_topic = t.get("response_topic")
                            break
                    enriched.append({**intent, "write_topic": write_topic, "response_topic": response_topic})
            if self._agent_intents.get(agent_name) != enriched:
                self._agent_intents[agent_name] = enriched
                intents_changed = True

        if intents_changed:
            self._send_intents_update()

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

    def _send_intents_update(self):
        all_intents = [i for intents in self._agent_intents.values() for i in intents]
        from messages.chat_message import IntentsUpdateMessage
        if self.output_queue:
            self.output_queue.enqueue(IntentsUpdateMessage(intents=tuple(all_intents)))
            logger.info(f"MqttStep: IntentsUpdateMessage émis ({len(all_intents)} intents)")

    def _send_write_tool_update(self):
        topic_lines = []
        for write_topic, meta in self._write_topics_meta.items():
            line = f"- {write_topic} : {meta['description']}. Format: {json.dumps(meta['format'], ensure_ascii=False)}"
            if meta.get("response_topic"):
                line += f". La réponse arrive ensuite automatiquement via: {meta['response_topic']}"
            topic_lines.append(line)
        description = (
            "Envoie une requête à l'un des agents disponibles. "
            "Choisis le topic correspondant au service demandé selon les descriptions ci-dessous.\n"
            "Topics disponibles :\n" + "\n".join(topic_lines)
        )

        tool_definition = {
            "type": "function",
            "function": {
                "name": "write_topic",
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "enum": sorted(list(self._write_topics_meta.keys())),
                            "description": "Le topic MQTT à cibler parmi ceux listés",
                        },
                        "payload": {
                            "type": "object",
                            "description": "Le payload JSON à publier sur le topic",
                        },
                    },
                    "required": ["topic", "payload"],
                },
            },
        }

        response_map = {
            topic: meta["response_topic"]
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

    def _handle_discussion_history(self, message: DiscussionHistoryMessage):
        username = self._nexus.username
        topic = f"users/{username}/discussions"
        asyncio.run_coroutine_threadsafe(
            self._nexus.publish(topic, list(message.history)),
            self._loop,
        )
        logger.info(f"MQTT discussion history publiée sur {topic} ({len(message.history)} messages)")
