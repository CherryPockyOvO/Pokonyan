#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""process_wav.py — 复用 audio_pipeline.py 的全部预处理逻辑处理 WAV 文件。

处理链路（与实时管线完全一致）：
  1. 读取 WAV → resample_audio → 16 kHz
  2. detect_tonal_peaks → 检测门铃 / 警报纯音
  3. three_band_separate → 人声 / 纯音 / 残差三路分离
  4. 语音门控（speech gate）→ 按 speech 分数切分语句
  5. extract_dominant_speaker_timbre → 主导说话人提纯
  6. prepare_for_whisper → 带通 + 峰值归一化（Whisper 输入）
  7. （可选）YAMNet 推理 + Whisper 转录

用法：
    # 仅 DSP 处理（不需要模型文件），输出分离后的人声 WAV
    python process_wav.py input.wav -o voice_out.wav

    # 完整管线（YAMNet + Whisper）
    python process_wav.py input.wav \
        --yamnet-model yamnet.tflite \
        --whisper-model ggml-tiny.en.bin \
        -o voice_out.wav
"""

import sys
import argparse
import time
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from audio_pipeline import (
    resample_audio,
    detect_tonal_peaks,
    three_band_separate,
    extract_dominant_speaker_timbre,
    prepare_for_whisper,
    SPEECH_EVENTS,
    BELL_EVENTS,
)

TARGET_SAMPLE_RATE = 16000
WINDOW_SIZE = 15600   # 0.975 s @ 16 kHz — 与 YamnetWhisperAudioPipeline 一致


# ──────────────────────────────────────────────────────────────────────────
# I/O
# ──────────────────────────────────────────────────────────────────────────
def load_wav(path: str):
    """读取任意格式音频文件，返回 (mono_float32, sample_rate)。"""
    try:
        import soundfile as sf
    except ImportError:
        sys.exit("需要 soundfile 库：pip install soundfile")
    audio, sr = sf.read(path, dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio.astype(np.float32), sr


def save_wav(path: str, audio: np.ndarray, sr: int = TARGET_SAMPLE_RATE):
    import soundfile as sf
    sf.write(path, audio, sr)
    print(f"  → 已保存：{path}  ({len(audio)/sr:.2f}s)")


# ──────────────────────────────────────────────────────────────────────────
# 全文件音调扫描（按 WINDOW_SIZE 分段扫描，取并集）
# ──────────────────────────────────────────────────────────────────────────
def scan_tone_freqs(audio: np.ndarray, sr: int):
    all_freqs = set()
    step = max(1, WINDOW_SIZE // 2)
    for i in range(0, len(audio) - sr + 1, step):
        window = audio[i : i + sr]
        for f in detect_tonal_peaks(window, sr):
            all_freqs.add(round(f, 1))
    return sorted(all_freqs)


# ──────────────────────────────────────────────────────────────────────────
# YAMNet 推理（可选）
# ──────────────────────────────────────────────────────────────────────────
def _make_yamnet_interpreter(model_path: str, window_size: int):
    """加载一个 YAMNet TFLite 解释器并重置输入形状。"""
    try:
        from ai_edge_litert.interpreter import Interpreter
    except ImportError:
        try:
            from tflite_runtime.interpreter import Interpreter
        except ImportError:
            from tensorflow.lite import Interpreter

    interp = Interpreter(model_path=model_path)
    detail = interp.get_input_details()
    interp.resize_tensor_input(detail[0]["index"], [window_size])
    interp.allocate_tensors()
    return interp


def _yamnet_scores(interp, waveform: np.ndarray):
    """对 interp 运行 inference，返回平均分类概率向量。"""
    detail = interp.get_input_details()[0]
    tensor = np.asarray(waveform, dtype=np.float32)
    scale, zero_point = detail.get("quantization", (0.0, 0))
    if scale:
        info = np.iinfo(detail["dtype"])
        tensor = np.clip(np.round(tensor / scale + zero_point), info.min, info.max)
        tensor = tensor.astype(detail["dtype"])
    interp.set_tensor(detail["index"], tensor)
    interp.invoke()
    out = interp.get_output_details()[0]
    raw = interp.get_tensor(out["index"])
    out_scale, out_zero = out.get("quantization", (0.0, 0))
    if out_scale:
        raw = (raw.astype(np.float32) - out_zero) * out_scale
    return np.asarray(raw, dtype=np.float32).reshape(-1, raw.shape[-1]).mean(axis=0)


def _event_max(scores, event_dict):
    return {
        name: max((float(scores[i]) for i in idxs if i < len(scores)), default=0.0)
        for name, idxs in event_dict.items()
    }


# ──────────────────────────────────────────────────────────────────────────
# 主处理函数
# ──────────────────────────────────────────────────────────────────────────
def process(
    wav_path: str,
    *,
    speech_threshold: float = 0.35,
    min_speech_sec: float = 0.5,
    max_silence_sec: float = 0.8,
    yamnet_model: str | None = None,
    whisper_model: str | None = None,
    voice_out: str | None = None,
    whisper_out: str | None = None,
):
    print(f"[1/6] 加载音频  {wav_path}")
    audio, sr = load_wav(wav_path)
    print(f"       原始 {sr} Hz  长度 {len(audio)/sr:.2f}s")

    if sr != TARGET_SAMPLE_RATE:
        audio = resample_audio(audio, sr, TARGET_SAMPLE_RATE)
        print(f"       → resample → {TARGET_SAMPLE_RATE} Hz")

    # ── 音调检测 ──────────────────────────────────────────────────────────
    print("[2/6] 扫描纯音频率 …")
    tone_freqs = scan_tone_freqs(audio, TARGET_SAMPLE_RATE)
    print(f"       检测到：{[f'{f:.0f}Hz' for f in tone_freqs] or '无'}")

    # ── 三路分离 ──────────────────────────────────────────────────────────
    print("[3/6] 三路分离（人声 / 纯音 / 其他）…")
    voice, tone, other = three_band_separate(
        audio, tone_freqs, TARGET_SAMPLE_RATE,
    )
    if voice_out:
        save_wav(voice_out, voice, TARGET_SAMPLE_RATE)

    # ── 可选：YAMNet + 语音门控 ──────────────────────────────────────────
    speech_segments: list[np.ndarray] = []

    if yamnet_model and Path(yamnet_model).is_file():
        print(f"[4/6] 加载 YAMNet  {yamnet_model}")
        speech_interp = _make_yamnet_interpreter(yamnet_model, WINDOW_SIZE)
        bell_interp   = _make_yamnet_interpreter(yamnet_model, WINDOW_SIZE)

        chunk_size = int(TARGET_SAMPLE_RATE * 0.25)   # 与实时管线一致
        voice_ring  = np.zeros(WINDOW_SIZE, dtype=np.float32)
        bell_ring   = np.zeros(WINDOW_SIZE, dtype=np.float32)

        gate_open = False
        buf: list[np.ndarray] = []
        silence_since = None
        speech_started = 0.0

        print("       语音门控扫描中 …")
        for i in range(0, len(voice), chunk_size):
            vc = voice[i : i + chunk_size]
            tc = tone[i : i + chunk_size]

            voice_ring = np.roll(voice_ring, -len(vc))
            voice_ring[-len(vc):] = vc
            bell_ring  = np.roll(bell_ring,  -len(tc))
            bell_ring[-len(tc):]  = tc

            sp_scores = _yamnet_scores(speech_interp, voice_ring)
            bl_scores = _yamnet_scores(bell_interp,   bell_ring)
            sp = float(sp_scores[0]) if len(sp_scores) > 0 else 0.0
            bl_vals = _event_max(bl_scores, BELL_EVENTS)
            now = i / TARGET_SAMPLE_RATE

            # 语音门控逻辑
            if sp >= speech_threshold:
                if not gate_open:
                    gate_open = True
                    speech_started = now
                    buf = [vc.copy()]
                    print(f"         [{now:6.1f}s] 语句开始  speech={sp:.2f}")
                else:
                    buf.append(vc)
                silence_since = None
            elif gate_open:
                buf.append(vc)
                if silence_since is None:
                    silence_since = now

            # 检测铃声事件
            for ev_name, ev_score in bl_vals.items():
                if ev_score >= 0.45:
                    print(f"         [{now:6.1f}s] 🔔 事件: {ev_name}  score={ev_score:.2f}")

            # 语句结束判定
            timed_out  = gate_open and (now - speech_started) >= 10.0
            silent_end = gate_open and silence_since is not None and (now - silence_since) >= max_silence_sec
            file_end   = (i + chunk_size >= len(voice)) and gate_open

            if timed_out or silent_end or file_end:
                seg = np.concatenate(buf) if buf else np.array([])
                dur = len(seg) / TARGET_SAMPLE_RATE
                gate_open = False
                buf = []
                silence_since = None
                if dur >= min_speech_sec:
                    print(f"         [{now:6.1f}s] 语句结束  长度={dur:.2f}s")
                    speech_segments.append(seg)

        print(f"       共找到 {len(speech_segments)} 段语句")

    else:
        print("[4/6] YAMNet 未提供，跳过语音门控（整段作为单一语句处理）")
        speech_segments = [voice]

    # ── 主导说话人提纯 ────────────────────────────────────────────────────
    print(f"[5/6] 提取主导说话人（{len(speech_segments)} 段）…")
    purified: list[np.ndarray] = []
    for idx, seg in enumerate(speech_segments):
        clean = extract_dominant_speaker_timbre(seg, TARGET_SAMPLE_RATE)
        purified.append(clean)
        dur = len(clean) / TARGET_SAMPLE_RATE
        print(f"         语句 {idx+1}:  {dur:.2f}s")

    # ── Whisper 转录（可选）────────────────────────────────────────────────
    if whisper_model and Path(whisper_model).is_file():
        try:
            from pywhispercpp.model import Model as WhisperCPP
        except ImportError:
            print("[6/6] pywhispercpp 未安装，跳过转录")
            return purified

        print(f"[6/6] Whisper 转录  {whisper_model}")
        whisper = WhisperCPP(str(whisper_model), n_threads=4)
        transcripts = []
        for idx, seg in enumerate(purified):
            prepared = prepare_for_whisper(seg, TARGET_SAMPLE_RATE)
            t0 = time.monotonic()
            segments = whisper.transcribe(prepared)
            text = " ".join(s.text.strip() for s in segments).strip()
            cost = time.monotonic() - t0
            print(f"         语句 {idx+1}  [{cost:.2f}s]:  {text}")
            transcripts.append(text)

        if whisper_out and transcripts:
            Path(whisper_out).write_text("\n".join(transcripts), encoding="utf-8")
            print(f"  → 转录已保存：{whisper_out}")
    else:
        print("[6/6] Whisper 未提供，跳过转录")

    return purified


# ──────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="复用 audio_pipeline.py 预处理链路处理 WAV 文件",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input", help="输入音频文件路径")
    parser.add_argument("-o", "--voice-out", metavar="WAV",
                        help="保存预处理后的人声 WAV")
    parser.add_argument("--yamnet-model", metavar="PATH",
                        help="YAMNet .tflite（可选，用于语音门控 + 铃声检测）")
    parser.add_argument("--whisper-model", metavar="PATH",
                        help="Whisper .bin（可选，用于转录）")
    parser.add_argument("--whisper-out", metavar="TXT",
                        help="保存转录文本到 TXT")
    parser.add_argument("--speech-threshold", type=float, default=0.35)
    parser.add_argument("--min-speech-sec",  type=float, default=0.5)
    parser.add_argument("--max-silence-sec", type=float, default=0.8)
    args = parser.parse_args()

    print("=" * 58)
    print("  process_wav.py — 离线音频预处理")
    print("=" * 58)

    process(
        args.input,
        speech_threshold=args.speech_threshold,
        min_speech_sec=args.min_speech_sec,
        max_silence_sec=args.max_silence_sec,
        yamnet_model=args.yamnet_model,
        whisper_model=args.whisper_model,
        voice_out=args.voice_out,
        whisper_out=args.whisper_out,
    )

    print("\n✓ 处理完成")


if __name__ == "__main__":
    main()
