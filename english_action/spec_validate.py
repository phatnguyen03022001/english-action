"""Compatibility import for :mod:`english_action.english.spec_validate`."""
from .english import spec_validate as _impl
from .english.spec_validate import *  # noqa: F401,F403

def __getattr__(name):
    return getattr(_impl, name)


if __name__ == "__main__":
    raise SystemExit(_impl.main())
