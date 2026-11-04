"""Compatibility import for :mod:`english_action.content.spawn`."""
from .content import spawn as _impl
from .content.spawn import *  # noqa: F401,F403

def __getattr__(name):
    return getattr(_impl, name)


if __name__ == "__main__":
    _impl.main()
