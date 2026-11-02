silero_vad_v6.onnx
Source: faster-whisper 1.2.1 distribution, faster_whisper/assets/silero_vad_v6.onnx
Model: Silero VAD v6, https://github.com/snakers4/silero-vad (MIT)
Inference layout adapted from https://github.com/SYSTRAN/faster-whisper (MIT).
Both licenses are included in this directory.

Used offline on CPU only when exporting generated dialogue audio.
No download, faster-whisper installation, torch or ASR model is needed.
numpy and onnxruntime are already dependencies of the OCR application.
The model only estimates outer speech boundaries; exported PCM is a direct
slice of the original. It never resamples or changes the volume of the WAV.
