"""Two independent feature modules with legacy root API compatibility."""
from importlib import import_module

__all__ = ("SchemaDirectory", "digest", "evaluate_publication", "validate_dataset",
           "bind_evidence", "prepare_batch", "promote_candidates", "revise_candidates")


def __getattr__(name):
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = "contracts" if name in __all__[:4] else "ledger"
    value = getattr(import_module(f".content.{module}", __name__), name)
    globals()[name] = value
    return value
