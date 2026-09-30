"""XDG base directories, resolved the way OpenCode (xdg-basedir) does.

$XDG_DATA_HOME / $XDG_CONFIG_HOME apply to the user's own home only: code
pointed at another home (tests, demos, `home=` arguments) gets that home's
defaults, so a developer's XDG settings never leak into it.
"""
import os

DEFAULTS = {"XDG_DATA_HOME": os.path.join(".local", "share"), "XDG_CONFIG_HOME": ".config"}


def base(var, home=None):
    home = os.path.expanduser(home or "~")
    value = os.environ.get(var, "")
    # The spec says relative values are invalid and to be ignored.
    if os.path.isabs(value) and os.path.realpath(home) == os.path.realpath(os.path.expanduser("~")):
        return value
    return os.path.join(home, DEFAULTS[var])


def data(home=None):
    return base("XDG_DATA_HOME", home)


def config(home=None):
    return base("XDG_CONFIG_HOME", home)
