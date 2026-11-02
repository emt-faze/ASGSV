"""Conservative export-only trimming. Kept PCM samples are never altered.

Speech detection is advisory: sustained sound also protects quiet syllables
and non-word vocalizations. Only the two outside edges are removed, with
extra onset/release room. No inference is performed on the playback path.
"""
from functools import lru_cache
from pathlib import Path
from uuid import uuid4
import wave

import numpy as np

EDGE_VERSION = 1


def _cancel(stop):
    if stop is not None and stop.is_set():
        from wuwa_dubber.speech_cache import SpeechCancelled
        raise SpeechCancelled("已停止音频整理")


@lru_cache(maxsize=1)
def _vad_session():
    import onnxruntime as ort
    options = ort.SessionOptions()
    options.inter_op_num_threads = options.intra_op_num_threads = 1
    options.enable_cpu_mem_arena = False
    options.log_severity_level = 4
    return ort.InferenceSession(
        str(Path(__file__).parent / "assets/audio-edges/silero_vad_v6.onnx"),
        sess_options=options, providers=["CPUExecutionProvider"])


def _runs(mask):
    edges = np.diff(np.r_[False, mask, False].astype(np.int8))
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)))


def _speech_windows(samples, rate, stop):
    """Silero v6 inference adapted from faster-whisper; see asset licenses."""
    # Pick a real channel, avoiding cancellation in antiphase stereo recordings.
    channel = samples[:, np.argmax(np.mean(samples * samples, axis=0))]
    positions = np.arange(int(np.ceil(len(channel) * 16000 / rate))) * rate / 16000
    mono = np.interp(positions, np.arange(len(channel)), channel).astype(np.float32)
    mono = np.pad(mono, (0, (-len(mono)) % 512))
    frames = mono.reshape(-1, 512)
    context = np.zeros((len(frames), 64), dtype=np.float32)
    context[1:] = frames[:-1, -64:]
    inputs = np.concatenate((context, frames), axis=1)
    h = np.zeros((1, 1, 128), dtype=np.float32)
    c = h.copy()
    predictions = []
    session = _vad_session()
    for start in range(0, len(inputs), 512):
        _cancel(stop)
        values, h, c = session.run(None, {"input": inputs[start:start+512], "h": h, "c": c})
        predictions.extend(values.reshape(-1))
    probabilities = np.asarray(predictions)
    windows = []
    for start, end in _runs(probabilities >= .15):
        if np.count_nonzero(probabilities[start:end] >= .35) >= 2:
            windows.append((start * .032, end * .032))
    return windows


def _decode_pcm(raw, width, channels):
    if width == 1:
        values = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128) / 128
    elif width in (2, 4):
        values = np.frombuffer(raw, dtype=f"<i{width}").astype(np.float32) / (2 ** (width*8-1))
    elif width == 3:
        triples = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        values = triples[:, 0] | (triples[:, 1] << 8) | (triples[:, 2] << 16)
        values = ((values ^ 0x800000) - 0x800000).astype(np.float32) / 8388608
    else:
        raise ValueError("不支持的 PCM 位深")
    return values.reshape(-1, channels)


def _bounds(samples, rate, stop):
    count = len(samples)
    duration = count / rate
    if duration < .35:
        return 0, count, "short_clip"
    step = max(1, round(rate * .01))
    padded = np.pad(samples, ((0, (-count) % step), (0, 0)))
    levels = np.sqrt(np.mean(padded.reshape(-1, step, samples.shape[1]) ** 2, axis=1)).max(axis=1)
    db = 20 * np.log10(np.maximum(levels, 1e-10))
    # No confident foreground: leave whispers, nearly silent clips and DC alone.
    if np.max(db) < -45 or np.ptp(db) < 8:
        return 0, count, "uncertain_level"
    _cancel(stop)
    try:
        windows = _speech_windows(samples, rate, stop)
    except Exception:
        _cancel(stop)
        windows = []
    if windows:
        reference = float(np.percentile(db[db > np.max(db)-20], 75))
        threshold = max(-45, min(-35, reference - 20))
        # At least 60 ms of sound protects humming/soft words missed by VAD.
        active = [(a*step/rate, b*step/rate) for a, b in _runs(db >= threshold)
                  if (b-a)*step/rate >= .06]
        first = min([windows[0][0] - .26] + [a-.10 for a, _ in active])
        last = max([windows[-1][1] + .16] + [b+.18 for _, b in active])
        method = "speech_and_energy"
    else:
        # Without speech evidence only remove near-silence, including in tones.
        active = np.flatnonzero(db >= max(-65, min(-55, float(np.max(db))-40)))
        if not len(active):
            return 0, count, "no_foreground"
        first, last = active[0]*step/rate-.10, (active[-1]+1)*step/rate+.18
        method = "near_silence_only"
    start = max(0, int(np.floor(first*rate)))
    end = min(count, int(np.ceil(last*rate)))
    # Tiny savings are not worth disturbing an already compact clip.
    if start < rate * .08:
        start = 0
    if count-end < rate * .08:
        end = count
    if end-start < rate * .20:
        return 0, count, "uncertain_bounds"
    return start, end, method


def trim_wav_edges(source, destination, stop=None, *, text=""):
    """Atomically write an export (also supports source == destination).

    The raw cache is never changed by callers. Result metadata enables a
    once-only upgrade of existing exports and exact sample-level audits.
    """
    source, destination = Path(source), Path(destination)
    _cancel(stop)
    with wave.open(str(source), "rb") as audio:
        params = audio.getparams()
        raw = audio.readframes(params.nframes)
    frame_bytes = params.nchannels * params.sampwidth
    if len(raw) != params.nframes * frame_bytes or params.comptype != "NONE":
        raise ValueError("音频不完整，未整理首尾")
    samples = _decode_pcm(raw, params.sampwidth, params.nchannels)
    start, end, method = _bounds(samples, params.framerate, stop)
    # One-/two-character utterances (e.g. 嗯哼) can start with an unusually
    # quiet non-word vocalization. Prefer its intact onset over a small saving.
    short_utterance = 0 < sum(c.isalnum() for c in text) <= 2
    if short_utterance:
        start = 0
    _cancel(stop)
    staging = destination.with_name(f".{destination.name}.{uuid4().hex}.edge.tmp")
    try:
        with wave.open(str(staging), "wb") as audio:
            audio.setparams(params)
            audio.writeframes(raw[start*frame_bytes:end*frame_bytes])
        _cancel(stop)
        staging.replace(destination)
    finally:
        staging.unlink(missing_ok=True)
    return dict(version=EDGE_VERSION, method=method, onset_protected=short_utterance, sample_rate=params.framerate,
                original_frames=params.nframes, start_frame=start, end_frame=end,
                removed_head_seconds=round(start/params.framerate, 4),
                removed_tail_seconds=round((params.nframes-end)/params.framerate, 4))
