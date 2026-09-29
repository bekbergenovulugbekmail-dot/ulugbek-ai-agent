"""Speech in and speech out.

The agent itself knows nothing about audio. Voice is a shell around the text
path that already exists: audio becomes text before a run starts, and an answer
becomes audio after one finishes. Nothing in :mod:`ulugbek_ai.agent` changes,
which is why a speech provider being down costs the console its microphone and
nothing else.
"""

from ulugbek_ai.voice.base import (
    Speech,
    SpeechToText,
    TextToSpeech,
    Transcript,
)
from ulugbek_ai.voice.factory import build_speech_to_text

__all__ = [
    "Speech",
    "SpeechToText",
    "TextToSpeech",
    "Transcript",
    "build_speech_to_text",
]
