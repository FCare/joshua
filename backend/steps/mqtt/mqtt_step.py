import asyncio
import logging
from typing import Optional, Dict

from pipeline_framework import PipelineStep
from messages.websocket_message import UserConnectionMessage
from messages.chat_message import DiscussionHistoryMessage

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

    def set_nexus(self, nexus) -> None:
        self._nexus = nexus
        self._loop = asyncio.get_event_loop()

    def init(self) -> bool:
        return True

    def cleanup(self):
        if hasattr(self, "input_queue") and self.input_queue:
            self.input_queue.stop()

    def _handle_message(self, message):
        if not self._nexus or not self._loop:
            logger.warning("MqttStep: nexus non configuré, message ignoré")
            return

        if isinstance(message, UserConnectionMessage):
            self._handle_user_connected(message)
        elif isinstance(message, DiscussionHistoryMessage):
            self._handle_discussion_history(message)

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

    def _handle_discussion_history(self, message: DiscussionHistoryMessage):
        username = self._nexus.username
        topic = f"users/{username}/discussions"
        asyncio.run_coroutine_threadsafe(
            self._nexus.publish(topic, list(message.history)),
            self._loop,
        )
        logger.info(f"MQTT discussion history publiée sur {topic} ({len(message.history)} messages)")
