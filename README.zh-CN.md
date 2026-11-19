# 随堂字幕（Mitschrift Live）

[![tests](https://github.com/QinBojia/mitschrift-live/actions/workflows/tests.yml/badge.svg)](https://github.com/QinBojia/mitschrift-live/actions/workflows/tests.yml)

Windows 上的实时字幕和翻译。它同时听电脑里播放的声音（Zoom 里的对方、网课视频）和你的麦克风，
在**本地**做语音识别，再用大模型逐句翻译。结果显示成聊天式的记录，外加一个浮在通话上方的字幕条。

*Mitschrift* 是德语里"课堂上边听边记的笔记"。它最初是为一门用 Zoom 上的德语课做的：识别德语、
英语和中文，翻译成中文、英文或德文。界面有中文和英文。

[English](README.md)

![主窗口：对方在左，我在右，每句话下面是译文](docs/images/main-window.png)

![浮在视频通话上的字幕条：不透明（上），只显示文字（下）](docs/images/caption-bar.png)

*悬浮字幕条。文字和背景的不透明度分开设置：默认不透明（上），也可以只留文字浮在通话上（下）。*

## 下载

安装包在 [Releases](https://github.com/QinBojia/mitschrift-live/releases) 页面。按电脑选一个版本，
模型都已打包在内，不用另外下载：

| 版本 | 适用 | 大小 |
|---|---|---|
| **CPU** | 任何 64 位 Windows 10 / 11 电脑（建议 4 核以上） | 约 0.65 GB |
| **NPU** | 带 NPU 的 Intel Core Ultra；不可用时改用 CPU | 约 1.7 GB |
| **AMD** | 通过 DirectML 用核显（AMD Radeon，也支持 Intel）；不可用时改用 CPU | 约 1.7 GB |

安装包没有代码签名，Windows SmartScreen 会先拦一下（点"更多信息"→"仍要运行"）。只装给当前用户，
不需要管理员权限。体积很小的 `…-Update.exe` 更新包可以直接升级任何版本，不用重装。

翻译需要在程序里点 ⚙ → 翻译，粘贴一个 [DeepSeek](https://platform.deepseek.com/)（默认，便宜）或
[Anthropic](https://console.anthropic.com/) 的 API key，或者 Azure
[翻译资源](https://learn.microsoft.com/azure/ai-services/translator/)的 key（机器翻译，见下文）。没有 key 也能看字幕。

## 功能

- **两路声音分开显示**：系统声音（WASAPI 回环录音，"对方"）和麦克风（"我"）。采集过程中可以随时
  关掉或打开任意一路：看视频时不想让麦克风乱入，或者只用麦克风听现场的课。
- **本地语音识别**：NVIDIA Parakeet TDT 0.6B v3（德语、英语等 25 种欧洲语言），可以跑在 NVIDIA
  显卡、Intel NPU、AMD / Intel 核显（DirectML）或 CPU 上。每句话自动判断是不是中文，中文交给
  SenseVoice Small 识别。
- **边说边出字，一句一条**：每句话说完约 1.5 秒后就定稿成一条字幕并立刻翻译，对方不停顿也一样。
- **逐句翻译**：带上前几句作为上下文，并告诉模型场景（默认 *a German language class*）。
  支持 DeepSeek、Claude 和微软翻译（Microsoft Translator）；译文可选 中文 / English / Deutsch，或关闭。
- **外放也能用**：用 WebRTC 回声消除处理麦克风（以系统声音为参考），再加一层文字级的回声过滤。
- **悬浮字幕条**：总在最上层，不抢 Zoom 的焦点；可以开"鼠标穿透"，用全局快捷键 Ctrl+Alt+L 切换；
  文字和背景的不透明度分开调。
- 自动跟随 Windows 默认设备；可以录音（双方声音 + 字幕，也包括点录音之前的字幕）；导出 TXT / SRT / Markdown；托盘常驻。
  界面中文或英文（默认跟随 Windows）。

声音不会离开你的电脑。发给翻译服务的只有说完的句子文字（加上前几句作为上下文）。在程序里填的
API key 用 Windows DPAPI 为当前用户加密保存。

## 原理

```
系统声音（回环录音）───────┐
                          ├─> 16 kHz ─> Silero VAD ─> 分句器 ─┬─> Parakeet   （德 / 英 / …）
麦克风 ─> 回声消除 ────────┘                                  └─> SenseVoice （中文 + 语种识别）
                                                                       │
                              字幕 ─> 翻译（DeepSeek / Claude / 微软）─> WebSocket ─> 主窗口（WebView2）
                                                                       └─> 字幕条（原生绘制，逐像素透明）
```

- **分句器**按停顿（0.5 秒）切出一段话；说话过程中每约 0.5 秒重新识别一次（电脑慢时会自动放慢）。
  字幕按句子走：一句话结束后再过 1.5 秒，这句话就定稿成一条字幕，识别从它的最后一个词接着往下，
  这个词会重听一遍，保证接缝处不漏字（不足三个词的短句会和下一句合在一起；8 秒还没有句末标点时，
  在逗号处切开）。在一段电视剧对话的录音上，字幕长度的 90 分位从 23 个词降到 14 个，最长的从 41 个
  降到 22 个，准确率不变。一直没有可用的句末标点时，仍然每约 9 秒强制切一次。如果某次识别漏掉了
  语音检测听到的一段话，会换个方式再识别几次；上一段已经听到的词也不会丢。
- **语种分流**：每句话的前 2.5 秒里，由 SenseVoice 的语种识别决定交给 SenseVoice（中文）还是
  Parakeet（其他语言）。
- **主窗口**是 WebView2 里的网页，通过本机 WebSocket（随机端口 + 令牌）和 Python 后台通信。
  **字幕条**用 Pillow + numpy 自己绘制，再用 `UpdateLayeredWindow` 显示，因为 WebView2 窗口只能整体透明。

## 识别引擎实测

除特别说明外，都在开发用的电脑上测（Intel Core Ultra 9 275HX，RTX 5080 Laptop）。
"10 秒一句"指识别一段 10 秒语音的耗时；CPU 占用是两路实时模拟时占整机的比例。

| 引擎 | 硬件 | 编码器 | 10 秒一句 | CPU 占用 | 备注 |
|---|---|---|---|---|---|
| `cuda` | NVIDIA 显卡（RTX 5080 Laptop） | fp32 | 66 ms | 约 2% | 显存约 4 GB |
| `npu` | Intel NPU（Core Ultra "AI Boost"） | fp16，OpenVINO | 约 155 ms | 0.5% | 首次启动编译约 1 分钟（之后缓存）；10 秒窗口 |
| `dml` | Intel 核显，4 个 Xe 核 | fp16，DirectML | 约 200 ms | 约 4% | 10 秒窗口 |
| `dml` | AMD Radeon 780M（用户日志） | fp16，DirectML | 约 130 ms | 约 6% | |
| `cpu` | 8 线程 | int8，不耐量化的卷积层保留 fp32 | 约 245 ms | 约 10% | 电脑较慢时，说话过程中的刷新会变慢 |

量化过的编码器速度快，但曾经是准确率的短板。下面是在真实录音上的结果：一段 13 分钟的电视剧
（有配乐、多人对话），用本程序识别后和官方字幕逐词比对。官方字幕不是逐字稿，所以绝对数值偏高，
要看的是差距：

| 编码器 | 引擎 | 词错误率 |
|---|---|---|
| fp32 | `cuda` | 15.3% |
| fp16 | `npu`、`dml` | 15.2%、15.3% |
| int8，下采样和深度卷积保留 fp32（`bench/to_int8.py`） | `cpu` | 15.9% |
| 现成的 int8，全部量化（0.2.x 及之前的 CPU 编码器） | `cpu` | 18.3% |
| NPU 用的全 int8（0.2.x 及之前） | `npu` | 26.2% |

另外，在一段单人长时间讲话的录音里，现成的 int8 编码器有 10.5% 的 12 秒窗口只识别出不到六成的词
（新编码器是 0.9%），表现出来就是整段话丢失。

每句翻译的延迟：DeepSeek `deepseek-flash` 中位数约 0.7 秒，Claude Haiku 约 1.0 秒，微软翻译约 0.1 秒
（`bench/mt_compare.py`，两部电视剧各 40 句译成中文）。微软翻译是机器翻译，不是大模型：识别正确的句子
翻得和大模型差不多，但它看不到前文和场景，识别错的词会照着直译，称呼的语气也可能不对（大约每 10 句有
1 句明显更差）。免费档（F0）每月 200 万字符，一节课每小时大约 4–5 万字符。
原始数据在 `bench/results/*.json`，测法见 [`bench/README.md`](bench/README.md)。

## 从源码运行

需要：Windows 10 / 11 64 位（在 Windows 11 上开发），WebView2 运行时（Windows 11 自带），
Python 3.12 和 [uv](https://docs.astral.sh/uv/)。

```bash
git clone https://github.com/QinBojia/mitschrift-live.git
cd mitschrift-live
```

按硬件选一种语音运行库（`cpu` / `cuda` / `dml` 三选一，`npu` 是附加项）：

```bash
uv sync --extra cuda
```

```bash
uv sync --extra cpu
```

```bash
uv sync --extra dml
```

```bash
uv sync --extra cpu --extra npu
```

启动桌面程序（`ui.bat` 会用仓库里的 `.venv`），然后在 ⚙ → 翻译 里填 key：

```bash
ui.bat
```

其他启动方式：

- `ui.bat --demo`：脚本生成的演示字幕，不需要声音、模型和 key。
- `ui.bat --autostart`：启动后直接开始识别。
- `run.bat --engine cpu`：在终端里显示字幕（全部参数见 `run.bat --help`）。

key 也可以来自环境变量（`DEEPSEEK_API_KEY`、`ANTHROPIC_API_KEY`、`AZURE_TRANSLATOR_KEY`；按区域创建的
微软翻译资源还要 `AZURE_TRANSLATOR_REGION`，或者在设置里填区域），优先级：进程环境变量 >
程序里保存的 key > 用户环境变量（`setx`）。

### 模型

模型第一次用到时从 Hugging Face 下载到 `~/.livecapture/models`（设置 `LIVECAPTURE_HOME` 可以把
程序存的所有东西搬到别处）。连不上 huggingface.co 时（比如在国内）会自动改用 hf-mirror.com 镜像；
设置了 `HF_ENDPOINT` 时以它为准。

| 引擎 | 下载内容 |
|---|---|
| `cpu`、`npu`、`dml` | Parakeet int8（约 0.65 GB） |
| `cuda`，以及下面的转换各需一次 | Parakeet fp32（约 2.4 GB） |
| 开启中文识别时都需要 | SenseVoice Small int8（约 0.23 GB），Silero VAD（2 MB） |

各引擎配上转换过的编码器效果最好，用 `bench/` 里的脚本各转换一次（先 `uv sync --group bench`；
两个脚本都需要 fp32 模型，会自动下载）：

- `cpu`，以及其他引擎退回 CPU 时：`uv run python bench/to_int8.py` 生成 int8 编码器，其中不耐量化的
  卷积层保留 fp32。不做这一步，程序会用现成的 int8 编码器，它偶尔会整段漏掉内容。
- `npu` 和 `dml`：`uv run python bench/to_fp16.py` 生成 fp16 编码器。不做这一步，NPU 会直接用
  fp32 文件（结果一样），`dml` 会退回 CPU 识别。

## 使用

- **开始 / 暂停**；**中文 / English / Deutsch / 关**：译文语言。
- 状态栏里的**对方 / 我**是两个开关（托盘菜单里也有）：点一下就停止听系统声音或麦克风。关掉的一路
  不识别、不显示、不录音，再点一下立刻恢复。只开麦克风时，这一路的字幕标为"现场"。（开着回声消除时，
  系统声音仍会被采集，用作麦克风的回声参考，但不会被转写。）
- **悬浮字幕**：显示 / 隐藏字幕条。拖动字幕条本身可以移动，拖边缘可以改大小；鼠标移上去会出现
  按钮（A− A+ 显示内容 穿透 ×）。最新的字幕在最下面，前面的字幕往上填满整个字幕条：拉高字幕条就能
  同时看到更多前文。所有行都是同一个字号（就是设置里的字号），不分语言；原文粗体、译文常规；每句左边一条
  彩色竖线表示是谁说的（青色是对方 / 现场，绿色是我）。显示内容有三档：原文加译文、仅原文、仅译文（选仅译文时，译文出来之前
  先用较淡的颜色显示原文）。
- **穿透**：点击会落到下面的窗口。按 **Ctrl+Alt+L** 关闭（被占用时改用 Ctrl+Alt+C 或
  Ctrl+Alt+F12，设置里会显示实际是哪个）。
- **⚙ 设置**：界面语言、设备（默认跟随 Windows）、回声消除、识别引擎、中文识别、翻译模型、
  API key 和场景、字幕条字号和文字 / 背景不透明度。
- **● 录音**：把双方声音（MP3，每路每小时约 30 MB）和字幕保存到 `文档\Mitschrift Live`，每次录音一个
  文件夹（以开始时间命名）。录音一直持续到再点一次 ● 录音或退出程序：中途暂停、关掉某一路、自动重启
  （默认设备变了、换了引擎）都不会拆成几段，那段时间的音频是静音。录音会先带上点录音之前的字幕
  （从启动或上次"清空"算起）。文件夹里有 `other.mp3` / `me.mp3`、`captions.jsonl`（每行一句：开始和结束的
  Unix 时间、`offset` = 在音频里第几秒，录音之前的字幕为负数，以及说话人、语言、译文）和 `meta.json`
  （精确的开始时间、版本、设置、暂停和开关记录）。
- **清空**（底部栏）：清空主窗口和悬浮字幕条；之后开始的录音从这里算起，已经录下的录音不受影响。
- 关闭主窗口后程序留在托盘继续运行；从托盘菜单退出。

设置、key 和日志在 `~/.livecapture`（`settings.json`、`ui.json`、`keys.json`、`logs/live-capture.log`）。
日志里只有耗时和错误信息，没有说话内容。

## 目录结构

项目最初叫 *Live Capture*；Python 包名（`livecapture`）、数据目录（`~/.livecapture`）和安装程序的
AppId 都沿用旧名，这样已经装好的版本可以直接升级。

```
livecapture/      程序本体
  app.py            桌面外壳：主窗口、字幕条、托盘、全局快捷键
  server.py         后台：设置、模型加载、WebSocket、状态和速度日志
  pipeline.py       声音来源 -> 分句器 -> 翻译，可重启
  audio.py, vad.py  WASAPI 采集（回环 + 麦克风），Silero VAD
  segmenter.py      分句、实时结果、强制切分、按句号拆分、语种分流
  stt.py            Parakeet：cuda / npu / dml / cpu
  sensevoice.py     中文识别 + 语种识别
  echo.py           回声消除和回声过滤
  translate.py      DeepSeek / Claude / 微软翻译
  keys.py           API key：环境变量、程序内加密保存
  i18n.py           Python 一侧的界面语言（页面用 ui/i18n.js）
  caption_bar.py    原生悬浮字幕条
  __main__.py       命令行版本（run.bat）
ui/               主窗口（HTML / JS）；overlay.html 用于在浏览器里预览字幕条
bench/            设计决策背后的测速和实验脚本
tests/            单元测试（uv run pytest）
build/            Windows 安装包工具（见下）
```

## 打包 Windows 安装包

`build/build_installer.py` 生成一个自带运行环境的文件夹（官方嵌入版 Python、依赖包、模型），
并用 [Inno Setup 6](https://jrsoftware.org/isinfo.php) 打成安装包：`--edition cpu`、`npu` 或 `amd`。
`--patch` 生成只替换代码的小更新包。

```bash
uv run python build/build_installer.py --edition cpu --installer
```

`--friends` 生成给信任的人用的私人版本：中文界面、DeepSeek 用内嵌的 `FRIENDS_DEEPSEEK_KEY`（Claude Haiku
和微软翻译用他们自己的 key）。
key 只做了混淆，拿到安装包的人都能取出来：用一个单独的、设了消费上限的 key，并且不要公开发布这类安装包。

## 已知限制

- 只支持 Windows（WASAPI 回环录音、Win32 窗口、DirectML）。
- 一种语言的句子里夹杂另一种语言的词（比如中文里夹德语词），常常识别不准；模型不认识的产品名会听错。
- 两个人同时说话时，仍可能丢掉几秒。
- 压在很响的配乐下的对白现在能被检测到，但模型常常听错，有时会出来一行别的欧洲语言。
- 0.3.0 之前安装的版本，用 `…-Update.exe` 升级后仍然使用旧的语音模型（CPU 版是现成的 int8 编码器，
  NPU 版是全 int8）。升级包本身也有改善，但要用上更好的编码器，需要用 0.3.0 的安装包重新安装。
- `npu` 和 `dml` 引擎用固定的 10 秒窗口，长句会被切成 10 秒以内的几段。
- DirectML 上 int8 比 fp16 慢；而且把窗口声明成固定形状会算出错误结果（ONNX Runtime 1.24），
  所以代码改用补零。
- 安装包没有代码签名，Windows SmartScreen 会提示风险。

## 许可证

随堂字幕自己的代码使用 MIT 许可证，见 [`LICENSE`](LICENSE)。模型和依赖各有自己的许可证
（Parakeet：CC BY 4.0；SenseVoice：FunASR 模型协议，需要署名），见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。
