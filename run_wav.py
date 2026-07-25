import sys
import time
import threading
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from audio_pipeline import YamnetWhisperAudioPipeline, resample_audio

SR = 16000
CHUNK = int(SR * 0.25)
TAIL_SILENCE = int(SR * 2.0)


def load_wav(path):
    try:
        import soundfile as sf
    except ImportError:
        sys.exit("need soundfile: pip install soundfile")
    data, sr = sf.read(path, dtype="float32", always_2d=False)
    data = np.asarray(data, np.float32)
    if data.ndim > 1:
        data = data.mean(axis=1).astype(np.float32)
    if sr != SR:
        data = resample_audio(data, sr, SR)
    return data


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: python run_wav.py <file.wav>")

    audio = load_wav(sys.argv[1])
    print(f"loaded {sys.argv[1]}  {len(audio) / SR:.2f}s @ {SR}Hz")

    events = []

    def on_event(name, score):
        events.append((name, round(score, 3)))
        print(f"[event] {name}  score={score:.2f}")

    pipeline = YamnetWhisperAudioPipeline(
        yamnet_model_path=str(_HERE / "yamnet.tflite"),
        whisper_model_path=str(_HERE / "ggml-tiny.en.bin"),
        on_event=on_event,
    )

    pipeline.running = True
    threading.Thread(target=pipeline._process_loop, daemon=True).start()

    stream = np.concatenate([audio, np.zeros(TAIL_SILENCE, np.float32)])
    chunks = [stream[i:i + CHUNK] for i in range(0, len(stream), CHUNK)]
    done = threading.Event()

    def feed():
        for c in chunks:
            pipeline.audio_queue.put(c)
        done.set()

    threading.Thread(target=feed, daemon=True).start()

    last_tx = ""
    last_change = time.monotonic()
    while True:
        st = pipeline.get_status()
        now = time.monotonic()
        if st["text"] and st["text"] != last_tx:
            last_tx = st["text"]
            last_change = now
            print(f"[transcript] {last_tx}")
        if done.is_set() and pipeline.audio_queue.empty() and now - last_change > 2.0:
            break
        time.sleep(0.1)

    pipeline.stop()
    print(f"events: {[e[0] for e in events]}")


if __name__ == "__main__":
    main()
