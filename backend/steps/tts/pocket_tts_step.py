import time
import threading
import requests
import os
import wave
import logging
from typing import Optional, Dict

from pipeline_framework import PipelineStep
from messages.base_message import BaseMessage

logger = logging.getLogger(__name__)

DEBUG_WAV = os.environ.get('DEBUG_WAV', 'False').lower() == 'true'

SAMPLE_RATE = 24000  # Mimi codec sample rate used by pocket-tts
WAV_HEADER_SIZE = 44


class PocketTTSStep(PipelineStep):
    def __init__(self, name: str = "PocketTTS", config: Optional[Dict] = None):
        super().__init__(name, config, handler=self._handle_input_message)

        self.host = config.get("host", "https://pocket-tts.caronboulme.fr") if config else "https://pocket-tts.caronboulme.fr"

        # voice: server-stored name, http/https/hf:// URL, or None for server default
        self.voice = config.get("voice", "fip1") if config else "fip1"
        self.sample_rate = config.get("sample_rate", SAMPLE_RATE) if config else SAMPLE_RATE

        # Session HTTP persistante — gère automatiquement les cookies de session
        self._session = requests.Session()

        self._lock = threading.Lock()
        self._current_response = None
        self._interrupted = False

        self._debug_wav_file = None
        self._debug_wav_lock = threading.Lock()
        if DEBUG_WAV:
            self._init_debug_wav()

        print(f"PocketTTSStep '{self.name}' configured for {self.host}")

    def _init_debug_wav(self):
        try:
            debug_dir = "debug_audio"
            os.makedirs(debug_dir, exist_ok=True)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            wav_filename = os.path.join(debug_dir, f"pocket_tts_{timestamp}.wav")
            self._debug_wav_file = wave.open(wav_filename, 'wb')
            self._debug_wav_file.setnchannels(1)
            self._debug_wav_file.setsampwidth(2)
            self._debug_wav_file.setframerate(SAMPLE_RATE)
            logger.info(f"PocketTTS DEBUG_WAV: Recording to {wav_filename}")
        except Exception as e:
            logger.error(f"PocketTTS DEBUG_WAV: Error initializing WAV file: {e}")
            self._debug_wav_file = None

    def init(self) -> bool:
        return True

    def _handle_input_message(self, message: BaseMessage):
        from messages.text_message import SentenceMessage

        if not isinstance(message, SentenceMessage):
            return

        try:
            text_data = message.text

            if message.is_last:
                if text_data and text_data.strip():
                    self._synthesize_text(text_data.strip())
                else:
                    self._send_audio_finished()
                return

            if text_data and text_data.strip():
                self._synthesize_text(text_data.strip())

        except Exception as e:
            logger.error(f"PocketTTS: Error handling input: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def _synthesize_text(self, text: str):
        start_time = time.time()
        first_chunk_time = None
        total_audio_bytes = 0
        header_bytes_remaining = WAV_HEADER_SIZE

        with self._lock:
            self._interrupted = False

        data = {"text": text}
        if self.voice:
            data["voice_url"] = self.voice

        logger.info(f"PocketTTS: Synthesizing '{text[:60]}'")

        try:
            with self._session.post(
                f"{self.host}/tts",
                data=data,
                stream=True,
                timeout=120,
            ) as response:
                with self._lock:
                    self._current_response = response

                if not response.ok:
                    logger.error(f"PocketTTS: HTTP {response.status_code}: {response.text[:200]}")
                    self._send_audio_finished()
                    return

                for chunk in response.iter_content(chunk_size=4096):
                    with self._lock:
                        if self._interrupted:
                            break

                    if not chunk:
                        continue

                    if first_chunk_time is None:
                        first_chunk_time = time.time()
                        ttft_ms = (first_chunk_time - start_time) * 1000
                        logger.info(f"PocketTTS TTFT: {ttft_ms:.1f}ms")

                    # Strip WAV header from the stream
                    if header_bytes_remaining > 0:
                        if len(chunk) <= header_bytes_remaining:
                            header_bytes_remaining -= len(chunk)
                            continue
                        chunk = chunk[header_bytes_remaining:]
                        header_bytes_remaining = 0

                    total_audio_bytes += len(chunk)
                    self._send_audio_chunk(chunk)

                end_time = time.time()
                audio_duration = total_audio_bytes / (self.sample_rate * 2)
                rtf = (end_time - start_time) / audio_duration if audio_duration > 0 else 0
                logger.info(f"PocketTTS: {total_audio_bytes} bytes ({audio_duration:.2f}s audio) RTF={rtf:.2f}x")

        except Exception as e:
            logger.error(f"PocketTTS: Error during synthesis: {e}")
        finally:
            with self._lock:
                self._current_response = None
            self._send_audio_finished()

    def _send_audio_chunk(self, chunk: bytes):
        if DEBUG_WAV and self._debug_wav_file:
            try:
                with self._debug_wav_lock:
                    self._debug_wav_file.writeframes(chunk)
                    self._debug_wav_file._file.flush()
            except Exception as e:
                logger.error(f"PocketTTS DEBUG_WAV: Error writing chunk: {e}")

        from messages.tts_message import AudioChunkOutputMessage
        message = AudioChunkOutputMessage(audio_data=chunk)
        if self.output_queue:
            self.output_queue.enqueue(message)
        logger.info(f"PocketTTS: Sent audio chunk ({len(chunk)} bytes)")

    def _send_audio_finished(self):
        from messages.tts_message import AudioFinishedMessage
        finish_message = AudioFinishedMessage(total_chunks=0, total_bytes=0)
        if self.output_queue:
            self.output_queue.enqueue(finish_message)
        logger.info("PocketTTS: Audio finish signal sent")

    def cleanup(self):
        with self._lock:
            self._interrupted = True
            if self._current_response:
                try:
                    self._current_response.close()
                except Exception:
                    pass

        self._session.close()

        if DEBUG_WAV and self._debug_wav_file:
            try:
                with self._debug_wav_lock:
                    self._debug_wav_file.close()
            except Exception as e:
                logger.error(f"PocketTTS DEBUG_WAV: Error closing WAV file: {e}")
            finally:
                self._debug_wav_file = None

        print(f"PocketTTS {self.name} cleaned up")
