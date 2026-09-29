from .claude import ClaudeSource
from .codex import CodexSource
from .opencode import OpenCodeSource
from .pi import PiSource

SOURCES = {
    "claude": ClaudeSource,
    "codex": CodexSource,
    "opencode": OpenCodeSource,
    "pi": PiSource,
}


def get_sources(names=None, home=None):
    if names:
        return [SOURCES[n](home=home) for n in names]
    return [cls(home=home) for cls in SOURCES.values()]
