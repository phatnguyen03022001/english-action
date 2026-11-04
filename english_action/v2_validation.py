"""Compatibility import for :mod:`english_action.content.v2_validation`."""
from .content import v2_validation as _impl
from .content.v2_validation import *  # noqa: F401,F403

def __getattr__(name):
    return getattr(_impl, name)
