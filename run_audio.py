#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实时麦克风录音 → audio_pipeline.py (YamnetWhisperAudioPipeline)

用法:
    python run_audio.py
"""

import sys
import time
import signal
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from audio_pipeline import YamnetWhisperAudioPipeline


def main():
    # ── 模型路径（按需修改）──
    yamnet_model  = _HERE / "yamnet.tflite"
    whisper_model = _HERE / "ggml-tiny.en.bin"

    print("=" * 60)
    print("  实时麦克风音频管线 (Dual-YAMNet + Whisper)")
    print("  按 Ctrl+C 停止")
    print("=" * 60)

    pipeline = YamnetWhisperAudioPipeline(
        yamnet_model_path=str(yamnet_model),
        whisper_model_path=str(whisper_model),
    )

    # Ctrl+C 优雅退出
    def _shutdown(sig, frame):
        print("\n[Main] 正在停止…")
        pipeline.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)

    pipeline.start()

    # ── 主循环：每 0.5 秒打印一次状态 ──
    last_text = ""
    last_event = ""
    try:
        while True:
            status = pipeline.get_status()

            # 有新转录文本
            if status["text"] and status["text"] != last_text:
                last_text = status["text"]
                print(f"\n📝 [{status['time']}] {status['text']}")

            # 有新事件
            if status["event"] and status["event"] != last_event:
                last_event = status["event"]
                print(f"\n🔔 [{status['event_time']}] 事件: {status['event']} "
                      f"(置信度 {status['event_score']:.2f})")

            # 状态行
            gate = status["gate"]
            sp = status["speech_score"]
            bl = status["bell_score"]
            err = status.get("error", "")
            line = f"\r🎤 speech={sp:.2f}  bell={bl:.2f}  gate={gate}"
            if err:
                line += f"  ⚠️ {err}"
            print(line, end="", flush=True)

            time.sleep(0.5)

    except KeyboardInterrupt:
        pass
    finally:
        pipeline.stop()
        print("\n[Main] 已停止。")


if __name__ == "__main__":
    main()
