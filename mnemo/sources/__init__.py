from .agy import AgySource
from .claude import ClaudeSource
from .codex import CodexSource
from .dsh import DshSource
from .grok import GrokSource
from .kb import KbSource
from .opencode import OpenCodeSource
from .pi import PiSource

SOURCES = {
    "agy": AgySource,
    "claude": ClaudeSource,
    "codex": CodexSource,
    "dsh": DshSource,
    "grok": GrokSource,
    "kb": KbSource,
    "opencode": OpenCodeSource,
    "pi": PiSource,
}


def get_sources(names=None, home=None):
    if names:
        return [SOURCES[n](home=home) for n in names]
    return [cls(home=home) for cls in SOURCES.values()]
