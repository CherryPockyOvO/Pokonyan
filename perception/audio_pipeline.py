# -*- coding: utf-8 -*-
"""Dual-YAMNet (speech + bell) with speaker separation and Whisper transcription.

Architecture:
  1. Microphone audio is captured and resampled to 16 kHz.
  2. Each chunk is processed in two parallel paths:
       - Voice: raw audio (no filter) → YAMNet-Speech
       - Bell:  bandpass filter (500-4000 Hz) → YAMNet-Bell
  3. Two YAMNet interpreters run on their respective inputs in parallel.
  4. When YAMNet-Speech detects speech, audio accumulates.
     After silence, the utterance goes through speaker separation
     (SepFormer or STFT fallback) to extract the dominant speaker,
     then Whisper transcribes the result.
  5. When YAMNet-Bell detects alarm / doorbell, the on_event callback fires.
"""

import os
import sys
import time
import queue
import tempfile
import threading

import numpy as np
import scipy.signal as signal
import sounddevice as sd
from pathlib import Path

# ---------------------------------------------------------------------------
# TFLite / LiteRT interpreter (for YAMNet)
# ---------------------------------------------------------------------------
try:
    from ai_edge_litert.interpreter import Interpreter
    LITE_ENGINE = "ai_edge_litert"
except ImportError:
    try:
        from tflite_runtime.interpreter import Interpreter
        LITE_ENGINE = "tflite_runtime"
    except ImportError:
        try:
            from tensorflow.lite import Interpreter
            LITE_ENGINE = "tensorflow.lite"
        except (ImportError, AttributeError):
            try:
                import tensorflow as tf
                Interpreter = tf.lite.Interpreter
                LITE_ENGINE = "tensorflow.lite.Interpreter"
            except (ImportError, AttributeError):
                try:
                    from tensorflow.lite.python.interpreter import Interpreter
                    LITE_ENGINE = "tensorflow.lite.python.interpreter"
                except ImportError:
                    Interpreter = None
                    LITE_ENGINE = "unavailable"

# ---------------------------------------------------------------------------
# Whisper C++
# ---------------------------------------------------------------------------
try:
    from pywhispercpp.model import Model as WhisperCPP
except ImportError:
    WhisperCPP = None

# ---------------------------------------------------------------------------
# 本地 DSP 工具函数（自包含，不依赖父模块）
# ---------------------------------------------------------------------------

def extract_dominant_speaker_timbre(audio_data, sample_rate=16000):
    """STFT 谐波掩膜提取主导说话人音色，过滤次要人声与杂音。"""
    if len(audio_data) < int(sample_rate * 0.2):
        return audio_data

    f, t_spec, Zxx = signal.stft(audio_data, fs=sample_rate, nperseg=512, noverlap=384)
    magnitude = np.abs(Zxx)
    freq_energy = np.mean(magnitude, axis=1)

    pitch_mask = (f >= 80) & (f <= 350)
    if not np.any(pitch_mask):
        return audio_data

    pitch_indices = np.where(pitch_mask)[0]
    dominant_f0_idx = pitch_indices[np.argmax(freq_energy[pitch_indices])]
    dominant_f0 = f[dominant_f0_idx]

    harmonic_mask = np.full_like(f, 0.15, dtype=np.float32)
    bandwidth = 35.0
    for k in range(1, 12):
        target_freq = k * dominant_f0
        if target_freq > sample_rate / 2:
            break
        harmonic_mask[np.abs(f - target_freq) <= bandwidth] = 1.0

    masked_Zxx = Zxx * harmonic_mask[:, np.newaxis]
    _, cleaned_audio = signal.istft(masked_Zxx, fs=sample_rate, nperseg=512, noverlap=384)

    max_val = np.max(np.abs(cleaned_audio))
    if max_val > 1e-5:
        cleaned_audio = (cleaned_audio / max_val) * 0.95
    return cleaned_audio.astype(np.float32)


def bandpass_filter(audio_data, sample_rate=16000, lowcut=500.0, highcut=4000.0, order=5):
    """带通滤波器：保留 [lowcut, highcut] 之间的频率，用于提取门铃声特征频段。"""
    if len(audio_data) == 0:
        return audio_data
    nyquist = 0.5 * sample_rate
    low = max(0.001, min(lowcut / nyquist, 0.99))
    high = max(low + 0.001, min(highcut / nyquist, 0.99))
    sos = signal.butter(order, [low, high], btype='bandpass', output='sos')
    filtered = signal.sosfiltfilt(sos, audio_data)
    return filtered.astype(np.float32)


# ===================================================================
# YAMNet class-index groups (official 521-class AudioSet ontology)
# ===================================================================
SPEECH_EVENTS = {
    "speech": (0, 1, 2, 3),
}

BELL_EVENTS = {
    "alarm":       (382, 390, 391, 393, 394),
    "alarm_clock": (389,),
    "doorbell":    (349, 350),
    "bell":        (173, 195, 196, 197, 198, 200, 201),
    "ring":        (202, 384, 385),
    "siren":       (395, 396, 397),
    "laughter":    (16, 17, 18),
    "cough":       (45,),
    "snore":       (48,),
    "whistling":   (51,),
    "music":       (137,),
    "applause":    (57, 58),
    "dog_bark":    (74, 75),
}


# ===================================================================
# Helper functions
# ===================================================================
def resample_audio(audio, source_rate, target_rate=16000):
    """Resample mono float audio to *target_rate*."""
    if source_rate == target_rate:
        return audio.astype(np.float32, copy=False)
    sample_count = int(len(audio) * target_rate / source_rate)
    return signal.resample(audio, sample_count).astype(np.float32)


def prepare_for_whisper(audio, sample_rate=16000):
    """Conservative band-pass + peak normalisation for Whisper input."""
    if len(audio) == 0:
        return audio
    sos = signal.butter(
        4,
        [80.0 / (sample_rate / 2.0), 7500.0 / (sample_rate / 2.0)],
        btype="bandpass",
        output="sos",
    )
    filtered = signal.sosfilt(sos, audio)
    peak = float(np.max(np.abs(filtered)))
    if peak > 1e-5:
        filtered = filtered * (0.95 / peak)
    return filtered.astype(np.float32)


def _extract_loudest_speaker_sepformer(
    speech_audio,
    sample_rate,
    output_path,
    model_name="speechbrain/sepformer-whamr16k",
):
    """Run SepFormer source-separation and save the loudest speaker track.

    Adapted from process_multi_speaker.py.  Returns True on success.
    """
    import torch
    import soundfile as sf
    import pyloudnorm as pyln
    import torchaudio.transforms as T
    from speechbrain.inference.separation import SepformerSeparation

    device = "cuda" if torch.cuda.is_available() else "cpu"

    # -- load model (cached after first download) --
    sep_model = SepformerSeparation.from_hparams(
        source=model_name,
        savedir=os.path.join("pretrained_models", model_name.replace("/", "_")),
        run_opts={"device": device},
    )
    model_sr = 16000

    # -- prepare waveform --
    sig = np.asarray(speech_audio, dtype=np.float32)
    if sig.ndim > 1:
        sig = sig.mean(axis=1)
    peak = float(np.max(np.abs(sig)))
    if peak > 0:
        sig = sig / peak * 0.95

    tensor = torch.from_numpy(sig).float().unsqueeze(0)
    if sample_rate != model_sr:
        tensor = T.Resample(sample_rate, model_sr)(tensor)
    tensor = tensor.to(device)

    # -- inference --
    with torch.no_grad():
        est = sep_model.separate_batch(tensor)

    n_speakers = est.shape[2]

    # -- resample back --
    if sample_rate != model_sr:
        rev = T.Resample(model_sr, sample_rate).to(device)
        est_r = rev(est[0].transpose(0, 1)).transpose(0, 1).unsqueeze(0)
    else:
        est_r = est

    # -- pick loudest speaker by active LUFS --
    best_track, best_lufs = None, -99.0
    meter = pyln.Meter(sample_rate)
    for i in range(n_speakers):
        track = est_r[0, :, i].detach().cpu().numpy()
        try:
            lufs = meter.integrated_loudness(track)
        except Exception:
            lufs = -99.0
        if lufs > best_lufs:
            best_lufs = lufs
            best_track = track

    if best_track is None:
        return False
    sf.write(output_path, best_track, sample_rate)
    return True


# ===================================================================
# Main pipeline class
# ===================================================================
class YamnetWhisperAudioPipeline:
    """Dual-YAMNet (speech + bell) with speaker separation + Whisper."""

    def __init__(
        self,
        yamnet_model_path="yamnet.tflite",
        whisper_model_path="ggml-tiny.en.bin",
        target_sample_rate=16000,
        chunk_duration=0.25,
        speech_threshold=0.35,
        event_threshold=0.45,
        trigger_hits=2,
        event_cooldown_sec=5.0,
        max_silence_sec=0.8,
        min_speech_sec=0.5,
        max_speech_sec=10.0,
        on_event=None,
    ):
        if Interpreter is None:
            raise RuntimeError("LiteRT/TFLite interpreter is not installed")
        if whisper_model_path is not None and WhisperCPP is None:
            print("[AudioPipeline] Warning: pywhispercpp is not installed. Whisper speech recognition will be disabled.")
            whisper_model_path = None

        self.yamnet_model_path = Path(yamnet_model_path).expanduser().resolve()
        self.whisper_model_path = Path(whisper_model_path).expanduser().resolve() if whisper_model_path else None
        if not self.yamnet_model_path.is_file():
            raise FileNotFoundError(f"YAMNet model not found: {self.yamnet_model_path}")
        if self.whisper_model_path and not self.whisper_model_path.is_file():
            raise FileNotFoundError(f"Whisper model not found: {self.whisper_model_path}")

        self.target_sample_rate = target_sample_rate
        self.window_size = 15600           # 0.975 s at 16 kHz
        self.chunk_duration = chunk_duration
        self.speech_threshold = speech_threshold
        self.event_threshold = event_threshold
        self.trigger_hits = trigger_hits
        self.event_cooldown_sec = event_cooldown_sec
        self.max_silence_sec = max_silence_sec
        self.min_speech_sec = min_speech_sec
        self.max_speech_sec = max_speech_sec
        self.on_event = on_event

        self.audio_queue = queue.Queue(maxsize=64)
        self.running = False
        self.lock = threading.Lock()
        self.stream = None
        self.process_thread = None
        self.transcribe_thread = None

        # -- public status fields --
        self.latest_transcript = ""
        self.latest_transcript_time = ""
        self.latest_event = ""
        self.latest_event_score = 0.0
        self.latest_event_time = ""
        self.gate_open = False
        self.speech_score = 0.0
        self.bell_score = 0.0
        self.ready = False
        self.error = ""

        # -- event-hit counters (separate for each YAMNet) --
        self.bell_hits = {n: 0 for n in BELL_EVENTS}
        self.last_bell_at = {n: 0.0 for n in BELL_EVENTS}
        self.speech_hits = {n: 0 for n in SPEECH_EVENTS}
        self.last_speech_at = {n: 0.0 for n in SPEECH_EVENTS}

        # -- speaker-separation backend --
        self._sepformer_available = False
        self._sepformer_model = None

        # ── load models ──
        self._load_yamnets()
        self._load_whisper()
        self._try_load_sepformer()

        # ── detect microphone hardware ──
        self.input_sample_rate = self._detect_sample_rate()
        self.chunk_size = int(self.input_sample_rate * self.chunk_duration)
        print(
            f"[Audio] Microphone {self.input_sample_rate} Hz → "
            f"YAMNet/Whisper {self.target_sample_rate} Hz"
        )

    # ------------------------------------------------------------------
    # Model loading
    # ------------------------------------------------------------------
    def _make_interpreter(self):
        """Create one TFLite interpreter instance for the YAMNet model."""
        interp = Interpreter(model_path=str(self.yamnet_model_path))
        details = interp.get_input_details()
        interp.resize_tensor_input(details[0]["index"], [self.window_size])
        interp.allocate_tensors()
        return interp

    def _load_yamnets(self):
        model = str(self.yamnet_model_path)
        print(f"[Audio] Loading YAMNet-Speech with {LITE_ENGINE}: {model}")
        self.speech_interp = self._make_interpreter()
        print(f"[Audio] Loading YAMNet-Bell   with {LITE_ENGINE}: {model}")
        self.bell_interp = self._make_interpreter()

    def _load_whisper(self):
        if not self.whisper_model_path or WhisperCPP is None:
            print("[Audio] Whisper.cpp speech transcription disabled for this pipeline.")
            self.whisper_model = None
            return
        print(f"[Audio] Loading Whisper.cpp: {self.whisper_model_path}")
        self.whisper_model = WhisperCPP(str(self.whisper_model_path), n_threads=4)

    def _try_load_sepformer(self):
        """Attempt to load SepFormer for neural speaker separation."""
        try:
            import torch                          # noqa: F401
            from speechbrain.inference.separation import SepformerSeparation  # noqa: F401
            print("[Audio] SepFormer available — will use neural speaker separation")
            self._sepformer_available = True
        except ImportError:
            print(
                "[Audio] SepFormer not available — "
                "falling back to STFT-based dominant speaker extraction"
            )
            self._sepformer_available = False

    # ------------------------------------------------------------------
    # Hardware
    # ------------------------------------------------------------------
    def _detect_sample_rate(self):
        try:
            default_rate = int(sd.query_devices(kind="input")["default_samplerate"])
        except Exception:
            default_rate = 48000
        for sample_rate in dict.fromkeys((16000, default_rate, 48000, 44100)):
            try:
                sd.check_input_settings(
                    samplerate=sample_rate, channels=1, dtype="float32",
                )
                return sample_rate
            except Exception:
                continue
        raise RuntimeError("no usable mono microphone input")

    # ------------------------------------------------------------------
    # Microphone callback
    # ------------------------------------------------------------------
    def _audio_callback(self, input_data, _frames, _time_info, status):
        if status:
            with self.lock:
                self.error = f"microphone status: {status}"
        chunk = input_data.copy().reshape(-1)
        if self.input_sample_rate != self.target_sample_rate:
            chunk = resample_audio(chunk, self.input_sample_rate, self.target_sample_rate)
        try:
            self.audio_queue.put_nowait(chunk)
        except queue.Full:
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                pass
            self.audio_queue.put_nowait(chunk)

    # ------------------------------------------------------------------
    # YAMNet inference (works for either interpreter)
    # ------------------------------------------------------------------
    def _scores(self, waveform, interpreter):
        """Run *interpreter* on *waveform* and return averaged class scores."""
        detail = interpreter.get_input_details()[0]
        tensor = np.asarray(waveform, dtype=np.float32)
        scale, zero_point = detail.get("quantization", (0.0, 0))
        if scale:
            dtype = detail["dtype"]
            info = np.iinfo(dtype)
            tensor = np.clip(np.round(tensor / scale + zero_point), info.min, info.max)
            tensor = tensor.astype(dtype)

        interpreter.set_tensor(detail["index"], tensor)
        interpreter.invoke()

        out_detail = interpreter.get_output_details()[0]
        raw = interpreter.get_tensor(out_detail["index"])
        out_scale, out_zero = out_detail.get("quantization", (0.0, 0))
        if out_scale:
            raw = (raw.astype(np.float32) - out_zero) * out_scale
        scores = np.asarray(raw, dtype=np.float32)
        return scores.reshape(-1, scores.shape[-1]).mean(axis=0)

    @staticmethod
    def _event_scores(scores, event_dict):
        """Extract per-event max score from YAMNet output *scores*."""
        values = {}
        for name, indices in event_dict.items():
            valid = [i for i in indices if i < len(scores)]
            values[name] = max((float(scores[i]) for i in valid), default=0.0)
        return values

    # ------------------------------------------------------------------
    # Event triggering (consecutive-hit confirmation + cooldown)
    # ------------------------------------------------------------------
    def _update_events(self, values, event_dict, hits_dict, last_dict, label):
        """Generic event-trigger logic shared by both YAMNets."""
        now = time.monotonic()
        for name, score in values.items():
            threshold = (
                self.speech_threshold if name == "speech" else self.event_threshold
            )
            hits_dict[name] = (
                hits_dict[name] + 1 if score >= threshold else 0
            )
            if (
                hits_dict[name] >= self.trigger_hits
                and now - last_dict[name] >= self.event_cooldown_sec
            ):
                hits_dict[name] = 0
                last_dict[name] = now
                ts = time.strftime("%H:%M:%S")
                with self.lock:
                    self.latest_event = name
                    self.latest_event_score = score
                    self.latest_event_time = ts
                print(f"\n[Audio-{label}] Event {name}: {score:.2f}")
                if self.on_event is not None:
                    self.on_event(name, score)

    # ------------------------------------------------------------------
    # Speaker separation
    # ------------------------------------------------------------------
    def _separate_speaker(self, speech_audio):
        """Extract the dominant speaker from *speech_audio* (numpy float32).

        Tries SepFormer first; falls back to STFT harmonic extraction.
        Returns the separated waveform as a numpy array.
        """
        if self._sepformer_available:
            tmp_in = tempfile.mktemp(suffix=".wav", prefix="speech_")
            tmp_out = tempfile.mktemp(suffix=".wav", prefix="dominant_")
            try:
                import soundfile as sf
                sf.write(tmp_in, speech_audio, self.target_sample_rate)
                ok = _extract_loudest_speaker_sepformer(
                    speech_audio, self.target_sample_rate, tmp_out,
                )
                if ok and os.path.isfile(tmp_out):
                    data, _sr = sf.read(tmp_out)
                    if data.ndim > 1:
                        data = data.mean(axis=1)
                    return data.astype(np.float32)
                print("[Audio] SepFormer returned no output, using STFT fallback")
            except Exception as exc:
                print(f"[Audio] SepFormer failed ({exc}), using STFT fallback")
            finally:
                for p in (tmp_in, tmp_out):
                    try:
                        os.remove(p)
                    except OSError:
                        pass

        # STFT-based dominant speaker extraction (always available)
        return extract_dominant_speaker_timbre(speech_audio, self.target_sample_rate)

    # ------------------------------------------------------------------
    # Main processing loop
    # ------------------------------------------------------------------
    def _process_loop(self):
        speech_ring = np.zeros(self.window_size, dtype=np.float32)
        bell_ring = np.zeros(self.window_size, dtype=np.float32)

        speech_chunks = []
        gate_open = False
        silence_since = None
        speech_started = 0.0

        while self.running:
            try:
                chunk = self.audio_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            # ── raw audio → speech ring (no filter for voice) ──
            if len(chunk) >= len(speech_ring):
                speech_ring[:] = chunk[-len(speech_ring):]
            else:
                speech_ring = np.roll(speech_ring, -len(chunk))
                speech_ring[-len(chunk):] = chunk

            # ── bandpass filter (500-4000Hz) → bell ring ──
            bell_chunk = bandpass_filter(chunk, self.target_sample_rate)
            if len(bell_chunk) >= len(bell_ring):
                bell_ring[:] = bell_chunk[-len(bell_ring):]
            else:
                bell_ring = np.roll(bell_ring, -len(bell_chunk))
                bell_ring[-len(bell_chunk):] = bell_chunk

            # ── YAMNet-Speech inference ──
            try:
                speech_values = self._event_scores(
                    self._scores(speech_ring, self.speech_interp),
                    SPEECH_EVENTS,
                )
            except Exception as exc:
                with self.lock:
                    self.error = f"YAMNet-Speech failed: {exc}"
                continue

            # ── YAMNet-Bell inference ──
            try:
                bell_values = self._event_scores(
                    self._scores(bell_ring, self.bell_interp),
                    BELL_EVENTS,
                )
            except Exception as exc:
                with self.lock:
                    self.error = f"YAMNet-Bell failed: {exc}"
                bell_values = {n: 0.0 for n in BELL_EVENTS}

            # ── update event counters ──
            self._update_events(
                speech_values, SPEECH_EVENTS,
                self.speech_hits, self.last_speech_at, "Speech",
            )
            self._update_events(
                bell_values, BELL_EVENTS,
                self.bell_hits, self.last_bell_at, "Bell",
            )

            sp = speech_values.get("speech", 0.0)
            bl = max(bell_values.values()) if bell_values else 0.0
            now = time.monotonic()

            # ── speech-gate logic ──
            if sp >= self.speech_threshold:
                if not gate_open:
                    gate_open = True
                    speech_started = now
                    speech_chunks = [chunk.copy()]
                    print(f"\n[Audio-Speech] Gate OPEN  (score {sp:.2f})")
                else:
                    speech_chunks.append(chunk)
                silence_since = None

            elif gate_open:
                speech_chunks.append(chunk)
                if silence_since is None:
                    silence_since = now

            timed_out = gate_open and now - speech_started >= self.max_speech_sec
            silent_long = (
                gate_open
                and silence_since is not None
                and now - silence_since >= self.max_silence_sec
            )

            if timed_out or silent_long:
                gate_open = False
                audio = (
                    np.concatenate(speech_chunks) if speech_chunks else np.array([])
                )
                duration = len(audio) / self.target_sample_rate

                if duration >= self.min_speech_sec:
                    # ── 1. speaker separation ──
                    print(
                        f"\n[Audio-Speech] Gate CLOSED ({duration:.1f}s). "
                        f"Separating speakers…"
                    )
                    separated = self._separate_speaker(audio)

                    # ── 2. Whisper transcription ──
                    t0 = time.monotonic()
                    try:
                        segments = self.whisper_model.transcribe(
                            prepare_for_whisper(separated, self.target_sample_rate),
                        )
                        text = " ".join(
                            s.text.strip() for s in segments
                        ).strip()
                        cost = time.monotonic() - t0
                        if text:
                            ts = time.strftime("%H:%M:%S")
                            with self.lock:
                                self.latest_transcript = text
                                self.latest_transcript_time = ts
                            print(
                                f"\n[Whisper] {text}  "
                                f"({cost:.2f}s, {duration:.1f}s speech)"
                            )
                    except Exception as exc:
                        with self.lock:
                            self.error = f"Whisper failed: {exc}"

                speech_chunks = []
                silence_since = None

            # ── update shared status ──
            with self.lock:
                self.speech_score = sp
                self.bell_score = bl
                self.gate_open = gate_open
                self.ready = True
                if not self.error.startswith("microphone"):
                    self.error = ""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self):
        if self.running:
            return
        self.running = True
        self.process_thread = threading.Thread(
            target=self._process_loop, name="yamnet-dual", daemon=True,
        )
        self.process_thread.start()
        self.stream = sd.InputStream(
            samplerate=self.input_sample_rate,
            channels=1,
            dtype="float32",
            blocksize=self.chunk_size,
            callback=self._audio_callback,
        )
        self.stream.start()
        print("[Audio] Microphone stream started (dual-YAMNet)")

    def stop(self):
        self.running = False
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
        if self.process_thread is not None:
            self.process_thread.join(timeout=3.0)

    # ------------------------------------------------------------------
    # Status API (for web server / top.py)
    # ------------------------------------------------------------------
    def get_status(self):
        with self.lock:
            return {
                "ready": self.ready,
                "error": self.error,
                "text": self.latest_transcript,
                "time": self.latest_transcript_time,
                "event": self.latest_event,
                "event_score": round(self.latest_event_score, 3),
                "event_time": self.latest_event_time,
                "speech_score": round(self.speech_score, 3),
                "bell_score": round(self.bell_score, 3),
                "gate": "OPEN" if self.gate_open else "CLOSED",
            }

    def get_latest_transcript_info(self):
        return self.get_status()
