# bench/

Benchmarks and experiments behind Mitschrift Live's design choices. They are working scripts,
not a polished suite: each one answers one question and prints (or saves) its numbers.

Install the extra tools first:

```bash
uv sync --extra cuda --group bench
```

(Use the runtime extra that fits your machine; `dml_test.py` needs `--extra dml`,
the NPU scripts `--extra npu`.)

## Checking a change

Most of what is here was written to find something out once. To check a change, three things
are enough:

- **Always:** `uv run pytest` (a few seconds). It includes the segmenter driven by a scripted
  recognizer: forced cuts, sentence cuts, skipped passages.
- **Segmenter, models or engine code:** one real recording with subtitles per language you
  care about, about three minutes each:

  ```bash
  uv run python bench/subtitle_eval.py check <recording dir> <subtitles>
  ```

  It replays the recording with live timing on `cuda` and `cpu` and prints word errors
  against the subtitles and how long the captions are, next to what the app produced when
  the recording was made. The unquantized engines (`cuda`, `npu`, `dml`) have scored within
  0.3 points of each other on every recording, so one of them stands for all three; add
  `--engines npu` (or run `dml` from an environment with onnxruntime-directml) only when
  that engine's own code or model changed. Recordings and subtitles stay out of the
  repository. To compare a segmenter setting with its old value on the same code, replay
  twice with `run --tag <name> --cfg <field>=<value>` and `score` both tags.
- **Echo or language-routing code:** `echo_eval.py` and `mixed_eval.py`.

## Test audio

The test speech is synthetic (Microsoft Edge TTS) and not stored in the repository. Make it with:

```bash
uv run python bench/make_samples.py bench/audio
```

```bash
uv run python bench/make_samples.py bench/audio_calib calib
```

```bash
uv run python bench/make_samples.py bench/audio_zh zh
```

`make_echo_test.py` builds the speaker-echo scenario (`bench/audio_echo`), `codeswitch_probe.py make`
the mixed-language clips, and `mixed_eval.py` writes `bench/results/mixed.wav` (German / Chinese /
English alternating), which `backend_test.py` and other end-to-end checks replay.

## Speech recognition speed and accuracy

| Script | Question |
|---|---|
| `parakeet_bench.py` | Latency, WER, memory and a two-stream real-time simulation for Parakeet on CUDA / CPU (fp32 / int8). Shared helpers for the other scripts. |
| `npu_test.py` | The same on an Intel NPU or iGPU through OpenVINO, with one static input window. |
| `npu_quantize.py` | Builds int8 OpenVINO encoders (weights-only and full post-training quantization, calibrated on `bench/audio_calib`). An experiment: the app used the full-int8 one on the NPU until 0.2.x, but on real recordings it made twice the errors of fp16. |
| `npu_breakdown.py` | Where the time goes on the NPU path: preprocessing, encoder, decoder. |
| `to_fp16.py` | Converts the fp32 encoder to fp16 for the NPU and DirectML (keeps the length arithmetic in fp32). |
| `subtitle_eval.py` | Scores the app on a real recording that has subtitles: replays it through the segmenter per engine and aligns the text with the subtitles word by word (missing / wrong / extra words, missing passages). This is what exposed the quantized encoders. |
| `to_int8.py` | Builds the CPU encoder: int8 matrix multiplications and 1x1 convolutions, the subsampling and depthwise convolutions in fp32. The ready-made int8 encoder quantizes those too and drops whole passages; numbers in the file. |
| `dml_test.py` | Parakeet's encoder on any DirectML GPU: fp16 vs int8, dynamic length vs padded window. |
| `slow_cpu_eval.py` | Simulates a weaker PC (few CPU threads) and checks that live captions keep up. |
| `summarize.py` | Prints one comparison table from `bench/results/*.json`. |

## Pipeline behaviour

| Script | Question |
|---|---|
| `replay_eval.py` | Test clips through the full live pipeline (VAD, segmenter, recognition): WER and latency. |
| `mixed_eval.py` | Alternating German / Chinese / English: is every utterance routed to the right model? |
| `lid_eval.py` | How well SenseVoice's language tag separates Chinese from German / English. |
| `codeswitch_probe.py` | What happens to German words inside Chinese sentences. |
| `echo_eval.py` | The speaker-echo scenario with echo handling off / on. |
| `translate_eval.py` | Latency, quality and token use of the translation backends (makes real API calls). |

## Desktop app checks

| Script | Question |
|---|---|
| `backend_test.py` | The backend end to end over its WebSocket, no window: replay, live target switch, token check. |
| `record_follow_test.py` | Recording during a replay, and restarting when the Windows default device changes. |
| `live_diag.py` | The command-line version with a status line every 2 s and crash reports (for hangs). |
| `transparency_probe.py` | Which way, if any, makes a WebView2 window see-through (none did: hence the native caption bar). |
| `window_probe.py` | Window styles, geometry and a screenshot of the running app, from outside. |

Results land in `bench/results/`. The JSON files from the development machine (Intel Core Ultra 9
275HX, RTX 5080 Laptop) are committed; audio, logs and screenshots are not.
