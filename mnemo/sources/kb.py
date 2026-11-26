"""Knowledge cards (mnemo/kb.py) as a source: one indexed message per card file.

The kb is off by default; while it is off this source lists no files, so a sync
drops cards indexed while it was on.
"""
import os

from .. import kb
from ..model import Msg
from .base import Source


class KbSource(Source):
    name = "kb"
    # Cards are source data the user owns: a deleted card is gone on purpose.
    ARCHIVABLE = False

    def directory(self):
        return kb.kb_dir(self.home)

    def files(self):
        if not kb.enabled():
            return
        for path in kb.card_paths(self.directory()):
            yield path

    def account(self, path):
        return None

    def parse(self, path, clip_text=True):
        card = kb.load_card(path)
        msg = Msg(
            ts=kb.card_ts(card.get("verified")),
            role="assistant",
            kind=kb.KIND,
            text=card["body"] or card["text"],
            raw=card["text"],
        )
        return card["id"], kb.first_cwd(card["scope"]), [(1, msg)]
