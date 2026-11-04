"""Compatibility import for :mod:`english_action.content.review`."""
from .content import review as _impl
from .content.review import *  # noqa: F401,F403

def __getattr__(name):
    return getattr(_impl, name)
