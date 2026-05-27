import asyncio
import json
import logging
from typing import Optional, Dict

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

        # Second pass: subscribe to read topics
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

    def _send_write_tool_update(self):
        lines = ["Publie un message sur un topic MQTT. Topics disponibles :"]
        for write_topic, meta in self._write_topics_meta.items():
            line = f"- {write_topic} : {meta['description']}. Format: {json.dumps(meta['format'], ensure_ascii=False)}"
            if meta.get("response_topic"):
                line += f". Réponse asynchrone sur: {meta['response_topic']}"
            lines.append(line)
        description = "\n".join(lines)

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

        from messages.chat_message import MqttToolUpdateMessage
        if self.output_queue:
            self.output_queue.enqueue(MqttToolUpdateMessage(tool_definition=tool_definition))
            logger.info(f"MqttStep: tool write_topic mis à jour ({len(self._write_topics_meta)} topics write)")

    async def _on_read_topic(self, topic: str, payload):
        if not isinstance(payload, (dict, list)):
            return
        meta = self._read_topics_meta.get(topic, {})
        logger.info(f"MqttStep: données reçues sur {topic} ({meta.get('description', '')})")
        from messages.chat_message import AgentTopicMessage
        if self.output_queue:
            self.output_queue.enqueue(AgentTopicMessage(
                topic=topic,
                description=meta.get("description", ""),
                payload=payload,
                is_response=meta.get("is_response", False),
            ))

    def _handle_discussion_history(self, message: DiscussionHistoryMessage):
        username = self._nexus.username
        topic = f"users/{username}/discussions"
        asyncio.run_coroutine_threadsafe(
            self._nexus.publish(topic, list(message.history)),
            self._loop,
        )
        logger.info(f"MQTT discussion history publiée sur {topic} ({len(message.history)} messages)")
