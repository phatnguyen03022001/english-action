"""Compatibility import for :mod:`english_action.content.contracts`."""
from .content import contracts as _impl
from .content.contracts import *  # noqa: F401,F403

def __getattr__(name):
    return getattr(_impl, name)
