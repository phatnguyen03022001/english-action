"""Compatibility import for :mod:`english_action.content.ledger`."""
from .content import ledger as _impl
from .content.ledger import *  # noqa: F401,F403

def __getattr__(name):
    return getattr(_impl, name)
