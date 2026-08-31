"""Local Meeting Scribe.

A self-contained desktop application that transcribes an audio recording,
attributes speech to speakers, drafts minutes / synopsis / action items with a
local LLM, runs accuracy checks, and exports the results as DOCX / ODT / PDF
next to the source audio.

Everything runs on the local machine. Nothing is uploaded.
"""

__version__ = "1.0.0"
APP_NAME = "Meeting Scribe"
APP_SLUG = "MeetingScribe"

# ctranslate2 (via faster-whisper) imports the deprecated pkg_resources and emits
# a noisy UserWarning on every startup; silence just that one.
import warnings as _warnings

_warnings.filterwarnings(
    "ignore", message="pkg_resources is deprecated", category=UserWarning)
