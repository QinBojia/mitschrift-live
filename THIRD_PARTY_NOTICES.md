# Third-party notices

Mitschrift Live's own code is MIT-licensed (see `LICENSE`). It downloads speech models at
runtime and depends on the Python packages below; none of them is vendored in this
repository except where noted.

## Models (downloaded on first use into `~/.livecapture/models`)

| Model | Used for | Source | License |
|---|---|---|---|
| NVIDIA Parakeet TDT 0.6B v3 (ONNX export by istupakov) | German / English / European speech recognition | [nvidia/parakeet-tdt-0.6b-v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3), [istupakov/parakeet-tdt-0.6b-v3-onnx](https://huggingface.co/istupakov/parakeet-tdt-0.6b-v3-onnx) | CC BY 4.0 (attribution required) |
| SenseVoice Small (FunAudioLLM / Alibaba, ONNX by k2-fsa) | Chinese recognition and spoken-language ID | [FunAudioLLM/SenseVoiceSmall](https://huggingface.co/FunAudioLLM/SenseVoiceSmall), [csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17](https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17) | FunASR Model Open Source License 1.1 (not an OSI license): use, copy, modify and share allowed; credit the source and author and keep the model name. Text: `build/licenses/FunASR-MODEL_LICENSE.txt` |
| Silero VAD (ONNX) | voice activity detection | [snakers4/silero-vad](https://github.com/snakers4/silero-vad), [istupakov/silero-vad-onnx](https://huggingface.co/istupakov/silero-vad-onnx) | MIT |

Derived files made by the scripts in `bench/` (the int8 OpenVINO encoder from
`npu_quantize.py`, the fp16 encoder from `to_fp16.py`) are conversions of the Parakeet
model and carry its license.

## Python packages

| Package | License |
|---|---|
| onnx-asr | MIT |
| onnxruntime / onnxruntime-gpu / onnxruntime-directml | MIT |
| openvino (Intel NPU / iGPU, optional) | Apache-2.0 |
| numpy | BSD-3-Clause (and bundled permissive licenses) |
| soundfile | BSD-3-Clause |
| soxr | LGPL-2.1-or-later |
| PyAudioWPatch | Apache-2.0 |
| livekit (WebRTC echo cancellation) | Apache-2.0 |
| pywebview | BSD-3-Clause |
| pystray | LGPL-3.0 |
| Pillow | MIT-CMU |
| pycaw | MIT |
| websockets | BSD-3-Clause |
| kaldi-native-fbank | Apache-2.0 |
| openai (DeepSeek's OpenAI-compatible API) | Apache-2.0 |
| anthropic | Apache-2.0 |
| rich (command-line view) | MIT |
| huggingface-hub | Apache-2.0 |
| nvidia-ml-py (optional, GPU name) | BSD |

soxr and pystray are LGPL: they are used unmodified, as separate packages that can be
replaced. Installers built with `build/build_friends.py` ship them as ordinary files in
`python/Lib/site-packages` and copy every package's license file into `licenses/`.

## Other files in this repository

- `build/lang/ChineseSimplified.isl` - unofficial Simplified Chinese messages for Inno
  Setup, from the [Inno Setup repository](https://github.com/jrsoftware/issrc) (translation
  credits in the file header).
- Fonts are not included; the caption bar uses Microsoft YaHei UI from Windows.
