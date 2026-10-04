"""Where roll PRs are opened. Each backend is a module here with a `Backend` class; `github` now,
`launchpad` (quirq's own cloud) later. Code outside this package never calls a forge directly."""
from __future__ import annotations

import importlib


class BackendError(Exception):
    """A forge call failed. The message says which call and why."""


def load(name: str, **kwargs):
    try:
        module = importlib.import_module(f"qqroll.backends.{name}")
    except ModuleNotFoundError as e:
        if e.name == f"qqroll.backends.{name}":
            raise ValueError(f"unknown backend {name!r}") from None
        raise
    return module.Backend(**kwargs)
