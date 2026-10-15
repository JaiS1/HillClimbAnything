"""Shared helpers: errors, JSON I/O and hashing. Standard library only."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


class HcaError(ValueError):
    """A user-facing harness error: the message says what to fix."""


class Refusal(HcaError):
    """The referee refuses to continue because its own records disagree (exit 2, like a refused confirm), not because of a usage error."""


HarnessError = HcaError  # name used by code ported from Dream Loop


def object_hash(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, allow_nan=False).encode()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    """Atomic write: a reader never sees a half-written file."""
    path = Path(path)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temp, path)


CHUNK_BYTES = 1024 * 1024


def digest(path):
    """sha256 of a file, streamed in 1 MiB chunks so a multi-GB dataset never has to fit in memory."""
    sha = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(CHUNK_BYTES)
            if not chunk:
                break
            sha.update(chunk)
    return sha.hexdigest()


def read_tail(path, limit=4096):
    """The last limit bytes of a file as text (undecodable bytes replaced); whole file if shorter."""
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        handle.seek(max(0, handle.tell() - limit))
        return handle.read().decode("utf-8", errors="replace")
