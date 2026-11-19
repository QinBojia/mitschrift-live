"""Mitschrift Live (随堂字幕): real-time captions and translation for system audio + microphone.

The package keeps its original name `livecapture` (and ~/.livecapture for data) so that
installed copies can be updated in place."""

__version__ = "0.3.1"

APP_NAME = "Mitschrift Live"
APP_NAME_ZH = "随堂字幕"  # what the (Chinese) interface calls the app

SAMPLE_RATE = 16_000

STREAM_LABELS = {"other": "对方", "me": "我"}
