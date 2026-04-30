import audioop
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

SAMPLE_RATE = 48000      # VoxCPM2 native sample rate
OUTPUT_SAMPLE_RATE = 24000  # Pipeline standard
WAV_HEADER_SIZE = 44


class VoxCPM2Step(PipelineStep):
    def __init__(self, name: str = "VoxCPM2", config: Optional[Dict] = None):
        super().__init__(name, config, handler=self._handle_input_message)

        self.host = config.get("host", "https://voxcpm2.caronboulme.fr") if config else "https://voxcpm2.caronboulme.fr"
        self.voice_id = config.get("voice_id", None) if config else None
        self.control_instruction = config.get("control_instruction", "") if config else ""
        self.cfg_value = config.get("cfg_value", 2.0) if config else 2.0
        self.inference_timesteps = config.get("inference_timesteps", 10) if config else 10
        self.sample_rate = config.get("sample_rate", SAMPLE_RATE) if config else SAMPLE_RATE

        env_api_key = os.getenv("VOXCPM2_API_KEY")
        config_api_key = config.get("api_key") if config else None
        if env_api_key:
            self.api_key = env_api_key
        elif config_api_key and config_api_key != "your_api_key_here":
            self.api_key = config_api_key
        else:
            self.api_key = None

        self._session = requests.Session()
        if self.api_key:
            self._session.headers.update({"X-API-Key": self.api_key})
        self._lock = threading.Lock()
        self._current_response = None
        self._interrupted = False

        self._audio_buffer = bytearray()
        self._last_flush_time = 0.0
        self._flush_interval = config.get("flush_interval_ms", 100) / 1000.0 if config else 0.1
        self._resample_state = None

        self._debug_wav_file = None
        self._debug_wav_lock = threading.Lock()
        if DEBUG_WAV:
            self._init_debug_wav()

        print(f"VoxCPM2Step '{self.name}' configured for {self.host}")

    def _init_debug_wav(self):
        try:
            debug_dir = "debug_audio"
            os.makedirs(debug_dir, exist_ok=True)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            wav_filename = os.path.join(debug_dir, f"voxcpm2_{timestamp}.wav")
            self._debug_wav_file = wave.open(wav_filename, 'wb')
            self._debug_wav_file.setnchannels(1)
            self._debug_wav_file.setsampwidth(2)
            self._debug_wav_file.setframerate(OUTPUT_SAMPLE_RATE)
            logger.info(f"VoxCPM2 DEBUG_WAV: Recording to {wav_filename}")
        except Exception as e:
            logger.error(f"VoxCPM2 DEBUG_WAV: Error initializing WAV file: {e}")
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
            logger.error(f"VoxCPM2: Error handling input: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def _synthesize_text(self, text: str):
        start_time = time.time()
        first_chunk_time = None
        total_audio_bytes = 0
        header_bytes_remaining = WAV_HEADER_SIZE

        with self._lock:
            self._interrupted = False
        self._resample_state = None

        payload = {
            "text": text,
            "streaming": True,
            "cfg_value": self.cfg_value,
            "inference_timesteps": self.inference_timesteps,
        }
        if self.voice_id:
            payload["voice_id"] = self.voice_id
        if self.control_instruction:
            payload["control_instruction"] = self.control_instruction

        logger.info(f"VoxCPM2: Synthesizing '{text[:60]}'")

        self._audio_buffer = bytearray()
        self._last_flush_time = start_time

        try:
            with self._session.post(
                f"{self.host}/tts",
                json=payload,
                stream=True,
                timeout=120,
            ) as response:
                with self._lock:
                    self._current_response = response

                if not response.ok:
                    logger.error(f"VoxCPM2: HTTP {response.status_code}: {response.text[:200]}")
                    self._send_audio_finished()
                    return

                for chunk in response:
                    with self._lock:
                        if self._interrupted:
                            break

                    if not chunk:
                        continue

                    if first_chunk_time is None:
                        first_chunk_time = time.time()
                        ttft_ms = (first_chunk_time - start_time) * 1000
                        logger.info(f"VoxCPM2 TTFT: {ttft_ms:.1f}ms")

                    if header_bytes_remaining > 0:
                        if len(chunk) <= header_bytes_remaining:
                            header_bytes_remaining -= len(chunk)
                            continue
                        chunk = chunk[header_bytes_remaining:]
                        header_bytes_remaining = 0

                    total_audio_bytes += len(chunk)
                    chunk, self._resample_state = audioop.ratecv(
                        chunk, 2, 1, SAMPLE_RATE, OUTPUT_SAMPLE_RATE, self._resample_state
                    )
                    self._audio_buffer.extend(chunk)

                    if time.time() - self._last_flush_time >= self._flush_interval:
                        self._flush_audio_buffer()

                end_time = time.time()
                audio_duration = total_audio_bytes / (self.sample_rate * 2)
                rtf = (end_time - start_time) / audio_duration if audio_duration > 0 else 0
                logger.info(f"VoxCPM2: {total_audio_bytes} bytes ({audio_duration:.2f}s audio) RTF={rtf:.2f}x")

        except Exception as e:
            logger.error(f"VoxCPM2: Error during synthesis: {e}")
        finally:
            with self._lock:
                self._current_response = None
            self._flush_audio_buffer()
            self._send_audio_finished()

    def _flush_audio_buffer(self):
        if not self._audio_buffer:
            return
        data = bytes(self._audio_buffer)
        self._audio_buffer = bytearray()
        self._last_flush_time = time.time()
        self._send_audio_chunk(data)

    def _send_audio_chunk(self, chunk: bytes):
        if DEBUG_WAV and self._debug_wav_file:
            try:
                with self._debug_wav_lock:
                    self._debug_wav_file.writeframes(chunk)
                    self._debug_wav_file._file.flush()
            except Exception as e:
                logger.error(f"VoxCPM2 DEBUG_WAV: Error writing chunk: {e}")

        from messages.tts_message import AudioChunkOutputMessage
        message = AudioChunkOutputMessage(audio_data=chunk)
        if self.output_queue:
            self.output_queue.enqueue(message)
        logger.info(f"VoxCPM2: Sent audio chunk ({len(chunk)} bytes)")

    def _send_audio_finished(self):
        from messages.tts_message import AudioFinishedMessage
        finish_message = AudioFinishedMessage(total_chunks=0, total_bytes=0)
        if self.output_queue:
            self.output_queue.enqueue(finish_message)
        logger.info("VoxCPM2: Audio finish signal sent")

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
                logger.error(f"VoxCPM2 DEBUG_WAV: Error closing WAV file: {e}")
            finally:
                self._debug_wav_file = None

        print(f"VoxCPM2 {self.name} cleaned up")
