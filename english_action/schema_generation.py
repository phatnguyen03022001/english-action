"""Compatibility import for :mod:`english_action.content.schema_generation`."""
from .content import schema_generation as _impl
from .content.schema_generation import *  # noqa: F401,F403

def __getattr__(name):
    return getattr(_impl, name)
