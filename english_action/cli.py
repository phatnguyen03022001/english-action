"""Compatibility import for :mod:`english_action.content.cli`."""
from .content import cli as _impl
from .content.cli import *  # noqa: F401,F403

def __getattr__(name):
    return getattr(_impl, name)
