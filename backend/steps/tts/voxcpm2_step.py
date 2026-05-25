import audioop
import time
import threading
import queue as stdlib_queue
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
        self.voice_name = config.get("voice_name", None) if config else None
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
        self._resample_state = None
        self._current_start = None

        # Dedicated background thread processes synthesis requests sequentially,
        # keeping the ChunkQueue worker thread free to handle SpeechStartMessage.
        self._synth_queue = stdlib_queue.Queue()
        self._synth_thread = threading.Thread(target=self._synth_worker, daemon=True)
        self._synth_thread.start()

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
            print(f"VoxCPM2 DEBUG_WAV: Recording to {wav_filename}")
        except Exception as e:
            print(f"VoxCPM2 DEBUG_WAV: Error initializing WAV file: {e}")
            self._debug_wav_file = None

    def init(self) -> bool:
        if self.voice_name and not self.voice_id:
            self.voice_id = self._resolve_voice_id(self.voice_name)
        return True

    def _resolve_voice_id(self, name: str) -> Optional[str]:
        try:
            response = self._session.get(f"{self.host}/voices", timeout=10)
            if not response.ok:
                print(f"VoxCPM2: Failed to fetch voices: HTTP {response.status_code}")
                return None
            voices = response.json()
            for voice in voices:
                if voice.get("name") == name:
                    voice_id = voice["voice_id"]
                    print(f"VoxCPM2: Resolved voice '{name}' -> {voice_id}")
                    return voice_id
            print(f"VoxCPM2: Voice '{name}' not found on server")
        except Exception as e:
            print(f"VoxCPM2: Error resolving voice name: {e}")
        return None

    def _handle_input_message(self, message: BaseMessage):
        from messages.text_message import SentenceMessage
        from messages.asr_message import SpeechStartMessage

        if isinstance(message, SpeechStartMessage):
            print(f"VoxCPM2: Received SpeechStart - Interrupt")
            self._current_start = message
            self._interrupt()
            return

        if not isinstance(message, SentenceMessage):
            return

        if (
            self._current_start is not None
            and self._current_start.id is not None
            and message.id is not None
            and self._current_start.is_more_recent_than(message)
        ):
            print(f"VoxCPM2: Dropping outdated SentenceMessage (session antérieure au dernier SpeechStart)")
            return

        try:
            text_data = message.text
            print(f"VoxCPM2: Received Sentence - {text_data}")

            if text_data and text_data.strip():
                self._synth_queue.put((text_data.strip(), message.is_last))
            elif message.is_last:
                # Empty text on last sentence — still signal end of audio
                self._send_audio_finished()

        except Exception as e:
            print(f"VoxCPM2: Error handling input: {e}")
            import traceback
            print(traceback.format_exc())

    def _synth_worker(self):
        """Processes synthesis requests sequentially in a dedicated thread."""
        while True:
            item = self._synth_queue.get()
            if item is None:  # shutdown signal from cleanup()
                break
            text, is_last = item

            # Reset interrupt flag and resampler state for this new sentence.
            with self._lock:
                self._interrupted = False
            self._resample_state = None

            self._synthesize_text(text)

            # Send audio_finished only after the last sentence and only if we
            # completed without being interrupted (_interrupt sends its own).
            with self._lock:
                interrupted = self._interrupted
            if is_last and not interrupted:
                self._send_audio_finished()

    def _interrupt(self):
        """Interrupts ongoing synthesis by closing the HTTP connection."""
        print("VoxCPM2: Interrupting TTS due to speech start")

        # Drop pending SentenceMessages from the ChunkQueue handler
        if self.input_queue:
            self.input_queue.flush()
            print("VoxCPM2: Input queue flushed - removed pending SentenceMessages")

        # Drop pending synthesis requests from the background worker queue
        while True:
            try:
                self._synth_queue.get_nowait()
            except stdlib_queue.Empty:
                break

        # Close the active HTTP response to unblock the streaming loop
        with self._lock:
            self._interrupted = True
            if self._current_response is not None:
                try:
                    self._current_response.close()
                except Exception:
                    pass

        self._send_audio_finished()

    def _synthesize_text(self, text: str):
        start_time = time.time()
        first_chunk_time = None
        total_audio_bytes = 0
        header_bytes_remaining = WAV_HEADER_SIZE

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

        print(f"VoxCPM2: Synthesizing '{text[:60]}'")

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
                    print(f"VoxCPM2: HTTP {response.status_code}: {response.text[:200]}")
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
                        print(f"VoxCPM2 TTFT: {ttft_ms:.1f}ms")

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
                    self._send_audio_chunk(chunk)

                end_time = time.time()
                audio_duration = total_audio_bytes / (self.sample_rate * 2)
                rtf = (end_time - start_time) / audio_duration if audio_duration > 0 else 0
                print(f"VoxCPM2: {total_audio_bytes} bytes ({audio_duration:.2f}s audio) RTF={rtf:.2f}x")

        except Exception as e:
            print(f"VoxCPM2: Error during synthesis: {e}")
        finally:
            with self._lock:
                self._current_response = None

    def _send_audio_chunk(self, chunk: bytes):
        if DEBUG_WAV and self._debug_wav_file:
            try:
                with self._debug_wav_lock:
                    self._debug_wav_file.writeframes(chunk)
                    self._debug_wav_file._file.flush()
            except Exception as e:
                print(f"VoxCPM2 DEBUG_WAV: Error writing chunk: {e}")

        from messages.tts_message import AudioChunkOutputMessage
        message = AudioChunkOutputMessage(audio_data=chunk)
        if self.output_queue:
            self.output_queue.enqueue(message)

    def _send_audio_finished(self):
        from messages.tts_message import AudioFinishedMessage
        finish_message = AudioFinishedMessage(total_chunks=0, total_bytes=0)
        if self.output_queue:
            self.output_queue.enqueue(finish_message)
        print("VoxCPM2: Audio finish signal sent")

    def cleanup(self):
        with self._lock:
            self._interrupted = True
            if self._current_response:
                try:
                    self._current_response.close()
                except Exception:
                    pass

        self._synth_queue.put(None)  # unblock and stop _synth_worker
        self._session.close()

        if DEBUG_WAV and self._debug_wav_file:
            try:
                with self._debug_wav_lock:
                    self._debug_wav_file.close()
            except Exception as e:
                print(f"VoxCPM2 DEBUG_WAV: Error closing WAV file: {e}")
            finally:
                self._debug_wav_file = None

        print(f"VoxCPM2 {self.name} cleaned up")
