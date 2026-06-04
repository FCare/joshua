import json
import logging
import os
import threading
import uuid
from typing import Optional, Dict, List

import openai

from pipeline_framework import PipelineStep
from messages.base_message import BaseMessage
from messages.websocket_message import TextInputMessage
from messages.asr_message import TranscriptionMessage
from messages.chat_message import IntentsUpdateMessage, NLUToolCallMessage, NLUMultiIntentMessage, MqttWriteMessage, AgentTopicMessage

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 0.60
REFORMULATION_MAX_TOKENS = 256


class NLUStep(PipelineStep):

    def __init__(self, name: str = "NLUStep", config: Optional[Dict] = None):
        super().__init__(name, config, handler=self._handle_message)
        self._intents: List[dict] = []
        self._embeddings: Dict[str, list] = {}   # example_text → embedding vector
        self._lock = threading.Lock()
        self._llm: Optional[openai.OpenAI] = None
        self._model = config.get("model", "qwen3-vl-8b-instruct") if config else "qwen3-vl-8b-instruct"
        self._ef = None
        self._conversation_history: List[dict] = []
        self._user_profile: str = ""

    def init(self) -> bool:
        try:
            api_key = os.getenv("LLAMACPP_API_KEY", "none")
            endpoint = self.config.get("endpoint", "http://localhost:9000/v1")
            self._llm = openai.OpenAI(api_key=api_key, base_url=endpoint)
            self._ef = self._load_embedding_function()
            logger.info(f"NLUStep initialisé — endpoint: {endpoint}, model: {self._model}")
            return True
        except Exception as e:
            logger.error(f"NLUStep init error: {e}")
            return False

    def cleanup(self):
        if hasattr(self, "input_queue") and self.input_queue:
            self.input_queue.stop()

    def _load_embedding_function(self):
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
        logger.info("NLUStep: modèle d'embeddings chargé")
        return model

    def _handle_message(self, message: BaseMessage):
        with self._lock:
            if isinstance(message, IntentsUpdateMessage):
                self._handle_intents_update(message)
            elif isinstance(message, AgentTopicMessage) and not message.is_response:
                if isinstance(message.payload, dict) and message.payload.get("summary"):
                    self._user_profile = message.payload["summary"]
                    logger.info(f"NLUStep: profil utilisateur reçu ({len(self._user_profile)} chars)")
                else:
                    self._passthrough(message)
            elif isinstance(message, TranscriptionMessage) and message.is_final:
                self._handle_user_input(message.text, source=message)
            elif isinstance(message, TextInputMessage):
                self._handle_user_input(message.text, source=message)
            else:
                self._passthrough(message)

    def _handle_intents_update(self, message: IntentsUpdateMessage):
        self._intents = list(message.intents)
        # Pre-compute embeddings for all examples
        if self._ef:
            all_examples = [ex for intent in self._intents for ex in intent.get("examples", [])]
            if all_examples:
                vectors = self._ef.encode(all_examples, convert_to_numpy=True)
                self._embeddings = {ex: vec for ex, vec in zip(all_examples, vectors)}
        logger.info(f"NLUStep: {len(self._intents)} intents chargés, {len(self._embeddings)} exemples embarqués")

    def _handle_user_input(self, text: str, source: BaseMessage):
        if not self._intents or not self._ef:
            self._passthrough(source)
            return

        try:
            # Step 1 — LLM reformulation
            phrases = self._reformulate(text)
            if not phrases:
                self._passthrough(source)
                return

            # Step 2 — collect all matching intents
            matches = []
            for phrase in phrases:
                intent, score = self._match_intent(phrase)
                if intent is None or score < SIMILARITY_THRESHOLD:
                    continue
                params = self._extract_params(phrase, intent)
                if params is None:
                    continue
                payload = self._build_payload(intent, params)
                write_topic = intent.get("write_topic")
                response_topic = intent.get("response_topic")
                if not write_topic:
                    continue
                matches.append({
                    "name": intent["name"],
                    "phrase": phrase,
                    "write_topic": write_topic,
                    "payload": payload,
                    "response_topic": response_topic or "",
                    "tool_call_id": f"nlu-{uuid.uuid4().hex[:12]}",
                })
                logger.info(f"NLUStep: intent={intent['name']} score={score:.2f} params={params}")

            if not matches:
                self._passthrough(source)
                return

            # Step 3 — send group message first (race-condition safe), then MQTT writes
            if self.output_queue:
                self.output_queue.enqueue(NLUMultiIntentMessage(
                    user_text=text,
                    matches=tuple(matches),
                ))
                for m in matches:
                    self.output_queue.enqueue(MqttWriteMessage(topic=m["write_topic"], payload=m["payload"]))

        except Exception as e:
            logger.error(f"NLUStep error: {e}")
            self._passthrough(source)

    def _reformulate(self, text: str) -> List[str]:
        intents_desc = "\n".join(
            f"- {i['name']}: {i['description']}"
            + (f" (ex: {', '.join(repr(e) for e in i.get('examples', [])[:2])})" if i.get('examples') else "")
            for i in self._intents
        )
        history_block = ""
        if self._conversation_history:
            last = self._conversation_history[-4:]
            history_block = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in last)

        profile_block = f"Profil utilisateur :\n{self._user_profile}\n\n" if self._user_profile else ""
        prompt = (
            "Tu es un assistant de classification et reformulation.\n"
            "TÂCHE : identifie toutes les intentions présentes dans le message et reformule chacune "
            "en une phrase canonique courte, une par ligne.\n\n"
            "RÈGLES :\n"
            "- Un message peut contenir PLUSIEURS intentions : traite-les toutes, une par ligne.\n"
            "- Si une partie du message est une déclaration personnelle (goût, préférence, habitude), "
            "reformule-la comme intention 'mémoriser un fait'.\n"
            "- Si une partie est une conversation vide ('merci', 'c'est sympa', 'bonjour'), ignore-la.\n"
            "- Si le message entier ne contient AUCUNE intention disponible, réponds uniquement : AUCUNE\n"
            "- Ne propose JAMAIS d'options. Ne pose JAMAIS de questions.\n"
            "- Si le message contient une référence vague (ex: 'ici', 'chez moi'), "
            "utilise le profil utilisateur pour la résoudre.\n\n"
            "EXEMPLE :\n"
            "Message : 'J'aime le jazz. C'est quoi les nouvelles ?'\n"
            "Réponse :\n"
            "J'aime le jazz.\n"
            "Quelles sont les nouvelles du jour ?\n\n"
            f"Intentions disponibles :\n{intents_desc}\n\n"
            f"{profile_block}"
            + (f"Conversation récente :\n{history_block}\n\n" if history_block else "")
            + f"Message utilisateur : \"{text}\"\n\n"
            "Réponse :"
        )

        response = self._llm.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=REFORMULATION_MAX_TOKENS,
            temperature=0.1,
            stream=False,
        )
        raw = response.choices[0].message.content.strip()
        if raw.strip().upper() == "AUCUNE":
            logger.info("NLUStep reformulation: aucune intention détectée → passthrough")
            return []
        phrases = [line.strip() for line in raw.splitlines()
                   if line.strip() and line.strip().upper() != "AUCUNE"]
        logger.info(f"NLUStep reformulation: {phrases}")
        return phrases

    def _match_intent(self, phrase: str):
        if not self._embeddings or not self._intents:
            return None, 0.0
        import numpy as np
        phrase_vec = self._ef.encode([phrase], convert_to_numpy=True)[0]
        best_intent = None
        best_score = -1.0
        for intent in self._intents:
            for example in intent.get("examples", []):
                ex_vec = self._embeddings.get(example)
                if ex_vec is None:
                    continue
                score = float(np.dot(phrase_vec, ex_vec) / (np.linalg.norm(phrase_vec) * np.linalg.norm(ex_vec) + 1e-8))
                if score > best_score:
                    best_score = score
                    best_intent = intent
        return best_intent, best_score

    def _extract_params(self, phrase: str, intent: dict) -> Optional[dict]:
        payload_template = intent.get("payload", {})
        slots = [k for k, v in payload_template.items() if isinstance(v, str) and v.startswith("{") and v.endswith("}")]
        if not slots:
            return {}

        slots_desc = ", ".join(f'"{s}"' for s in slots)
        profile_block = f"Profil utilisateur :\n{self._user_profile}\n\n" if self._user_profile else ""
        prompt = (
            f"Phrase : \"{phrase}\"\n"
            f"{profile_block}"
            f"Extrait les valeurs suivantes de la phrase : {slots_desc}.\n"
            "Si la phrase contient une référence vague (ex: 'ici', 'chez moi', 'mon endroit'), "
            "utilise le profil utilisateur pour résoudre la valeur réelle.\n"
            f"Retourne un objet JSON avec uniquement ces clés. Exemple : {{\"publisher\": \"France Info\"}}"
        )
        try:
            response = self._llm.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=64,
                temperature=0.0,
                stream=False,
            )
            raw = response.choices[0].message.content.strip()
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start == -1 or end == 0:
                return None
            return json.loads(raw[start:end])
        except Exception as e:
            logger.warning(f"NLUStep param extraction failed: {e}")
            return None

    def _build_payload(self, intent: dict, params: dict) -> dict:
        payload = {}
        for k, v in intent.get("payload", {}).items():
            if isinstance(v, str) and v.startswith("{") and v.endswith("}"):
                # The outer key IS the slot name; the template value {xxx} is just a hint
                payload[k] = params.get(k, "")
            else:
                payload[k] = v
        return payload

    def _passthrough(self, message: BaseMessage):
        if self.output_queue:
            self.output_queue.enqueue(message)

    def update_history(self, history: list):
        self._conversation_history = list(history)
