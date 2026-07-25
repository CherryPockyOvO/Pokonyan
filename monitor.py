# -*- coding: utf-8 -*-
"""Real-time microphone monitor: doorbell + speech confidence & transcription."""

import sys
import time

from audio_pipeline import YamnetWhisperAudioPipeline


def on_event(name, score):
    print(f"\n  >>> EVENT: {name} (score={score:.2f})")


def main():
    print("Initializing audio pipeline...")
    pipeline = YamnetWhisperAudioPipeline(on_event=on_event)
    pipeline.start()
    print("Listening... (Ctrl+C to stop)\n")

    last_text = ""
    try:
        while True:
            s = pipeline.get_status()
            line = (
                f"\r  speech={s['speech_score']:.3f}  "
                f"bell={s['bell_score']:.3f}  "
                f"event={s['event']}({s['event_score']:.3f})  "
                f"gate={s['gate']}  "
            )
            sys.stdout.write(line)
            sys.stdout.flush()

            if s["text"] and s["text"] != last_text:
                last_text = s["text"]
                print(f"\n  [Transcript] ({s['time']}) {s['text']}")

            if s["error"]:
                print(f"\n  [Error] {s['error']}")

            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        pipeline.stop()


if __name__ == "__main__":
    main()
