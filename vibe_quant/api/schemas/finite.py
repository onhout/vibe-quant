"""Reject non-finite floats anywhere in a launch request body (bead vibe-quant-z9qzi).

Starlette's ``request.json()`` is ``json.loads``, which accepts the bare literals
``NaN``, ``Infinity`` and ``-Infinity``. Pydantic then either accepts one into an
untyped field (``parameters: dict[str, object]``) or fails on it, and FastAPI cannot
serialise the resulting ``RequestValidationError`` whose ``input`` holds a NaN
(``json.dumps(..., allow_nan=False)``) -- a 500. This walker turns any non-finite
float anywhere in the raw body into a 422 naming its dotted path.

Every other type (int, bool, None, str, and finite floats) is left to the model's own
validators, so a numeric string, a bool or an out-of-range finite number keeps today's
outcome -- only ``NaN``/``Infinity``/``-Infinity`` are this module's business.
"""

from __future__ import annotations

import math


def _join(path: str, key: object) -> str:
    """Append a dict key or list index to a dotted path (top-level keys stand alone)."""
    return f"{path}.{key}" if path else str(key)


def reject_non_finite(value: object, path: str = "") -> None:
    """Raise ``ValueError`` naming the first non-finite float in ``value``.

    Walks dicts, lists and tuples depth-first in insertion order, so the reported path
    is deterministic; a non-finite float is ``f"{path}: must be a finite number"``.
    A value that is not a float, dict or sequence is not inspected.
    """
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path or 'body'}: must be a finite number")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            reject_non_finite(item, _join(path, key))
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            reject_non_finite(item, _join(path, index))
