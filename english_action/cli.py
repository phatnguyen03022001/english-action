"""Compatible command surface; private schemas are an explicit CLI dependency."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import stat
import sys

from .contracts import SchemaDirectory, validate_dataset
from .ledger import (bind_evidence, blank_state, prepare_batch, promote_candidates,
                     revise_candidates, state_valid)


def _path_safe(path: str, *, create_parent: bool = False) -> Path:
    if not path:
        raise ValueError("--state must be a non-empty file path")
    given = Path(path)
    absolute = Path(os.path.abspath(given))
    if not given.is_absolute() and not absolute.is_relative_to(Path.cwd()):
        raise ValueError("Unsafe state path: relative path escapes working directory")
    current = Path(absolute.anchor)
    components = absolute.parts[1:-1]
    for part in components:
        current /= part
        if create_parent and not current.exists() and not current.is_symlink():
            current.mkdir(mode=0o700)
        try:
            info = current.lstat()
        except FileNotFoundError:
            # A non-existing parent may only be created during a write.
            if create_parent:
                raise
            continue
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise ValueError("Unsafe state path: directory component is not a regular directory")
        if info.st_uid not in (os.getuid(), 0):
            raise ValueError("Unsafe state path: untrusted directory owner")
        mode = stat.S_IMODE(info.st_mode)
        sticky_tmp = str(current) == "/tmp" and info.st_uid == 0 and mode & stat.S_ISVTX
        if mode & 0o022 and not sticky_tmp:
            raise ValueError("Unsafe state path: untrusted directory write permissions")
        if create_parent and part == ".draft" and (info.st_uid != os.getuid() or mode & 0o077):
            raise ValueError("Unsafe state path: .draft state root must be private 0700")
    if create_parent and absolute.parent == Path("/tmp"):
        raise ValueError("Unsafe state path: create a private directory below /tmp")
    try:
        info = absolute.lstat()
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise ValueError("Unsafe state path: state file must be regular, never a symlink")
    return absolute


def _read_state(path: Path) -> dict | None:
    _path_safe(str(path))
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("Safe state reads require O_NOFOLLOW")
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "r", encoding="utf-8") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise ValueError("State is not a regular file")
        value = json.load(f)
    _path_safe(str(path))
    return value


@contextmanager
def _state_lock(path: Path):
    _path_safe(str(path), create_parent=True)
    lock = Path(str(path) + ".lock")
    if lock.is_symlink():
        raise ValueError("Unsafe state path: lock symlink")
    descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    identity = os.fstat(descriptor)
    try:
        yield
    finally:
        os.close(descriptor)
        try:
            info = lock.lstat()
            if info.st_dev == identity.st_dev and info.st_ino == identity.st_ino and stat.S_ISREG(info.st_mode):
                lock.unlink()
        except FileNotFoundError:
            pass


def _atomic_save(path: Path, state: dict) -> None:
    _path_safe(str(path))
    temp = Path(str(path) + "." + secrets.token_hex(6) + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    identity = os.fstat(fd)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        _path_safe(str(path))
        check = temp.lstat()
        if check.st_ino != identity.st_ino or check.st_dev != identity.st_dev or not stat.S_ISREG(check.st_mode):
            raise ValueError("Temporary state file changed")
        os.replace(temp, path)
        _path_safe(str(path))
        dfd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        try:
            if temp.is_file() and not temp.is_symlink():
                check = temp.lstat()
                if (check.st_dev, check.st_ino) == (identity.st_dev, identity.st_ino):
                    temp.unlink()
        except FileNotFoundError:
            pass


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="english-quality")
    parser.add_argument("operation", choices=("validate", "prepare", "bind-evidence", "revise", "promote", "check"))
    parser.add_argument("--input", help="Path to candidate request JSON")
    parser.add_argument("--state", default=".draft/candidates-state.json")
    parser.add_argument("--schemas", default="schemas", help="Consumer-owned schema root with v1/ and v2/")
    parser.add_argument("--version", choices=("v1", "v2"), default="v2")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--full-publication-diagnostics", action="store_true")
    opts = parser.parse_args(argv)
    try:
        schema = SchemaDirectory(opts.schemas, opts.version)
        path = _path_safe(opts.state)
        writing = opts.operation in ("prepare", "bind-evidence", "revise", "promote") and not opts.dry_run
        @contextmanager
        def no_lock():
            yield
        with _state_lock(path) if writing else no_lock():
            state = _read_state(path)
            if opts.operation == "check":
                if not state_valid(state):
                    missing = state is None
                    out = {"status": "invalid", "storageValid": False, "issues": [{"code": "STATE_NOT_FOUND" if missing else "STATE_INVALID", "message": "Persisted candidate ledger does not exist" if missing else "Malformed candidate ledger structure"}], "publication": {"eligible": False, "issues": []}}
                else:
                    validated = validate_dataset(state["records"], schema, version=opts.version, publication_issue_limit=None if opts.full_publication_diagnostics else 1000)
                    out = {"status": "valid" if validated["valid"] else "invalid", "storageValid": validated["valid"], "issues": validated["issues"], "publication": validated["publication"]}
            else:
                if not opts.input:
                    raise ValueError("--input path required")
                with open(opts.input, "r", encoding="utf-8") as f:
                    request = json.load(f)
                ledger = state if state is not None else {}
                if opts.operation in ("validate", "prepare"):
                    next_state, out = prepare_batch(request, ledger, schema, dry_run=opts.operation == "validate" or opts.dry_run)
                elif opts.operation == "bind-evidence":
                    next_state, out = bind_evidence(ledger, request, schema, dry_run=opts.dry_run)
                elif opts.operation == "revise":
                    next_state, out = revise_candidates(ledger, request, schema, dry_run=opts.dry_run)
                else:
                    # CLI deliberately has no mechanism to manufacture a trusted reviewer callback.
                    next_state, out = promote_candidates(ledger, request, schema, dry_run=opts.dry_run)
                if writing and out["status"] in ("prepared", "bound", "revised", "promoted"):
                    _atomic_save(path, next_state)
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 1 if out["status"] in ("conflict", "invalid", "blocked") else 0
    except Exception as exc:
        detail = {"status": "error", "message": str(exc)}
        if opts.operation == "check" and isinstance(exc, json.JSONDecodeError):
            detail["code"] = "STATE_PARSE_ERROR"
        print(json.dumps(detail, ensure_ascii=False), file=sys.stderr)
        return 1


def main():
    sys.exit(run())
