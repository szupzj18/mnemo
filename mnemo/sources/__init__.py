from .. import kb as kb_mod
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


def status_source_counts(counts):
    kb_enabled = kb_mod.enabled()
    return {
        name: counts.get(name, {"files": 0, "messages": 0})
        for name in sorted(SOURCES)
        if name != "kb" or kb_enabled
    }
