"""Pytest fixtures for the vibe_quant unit suite."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _restore_aux_data_paths() -> Iterator[None]:
    """Undo ``aux_data.configure`` leaks: its archive/catalog paths are module globals.

    ``NTScreeningRunner`` re-configures them on every run -- with the run's tmp
    catalog in tests -- and never resets, so the last test's tmp path leaks into
    the next test on the same xdist worker (vibe-quant-ca3v8).
    """
    aux = sys.modules.get("vibe_quant.dsl.aux_data")
    saved = (aux._archive_path, aux._catalog_path) if aux is not None else None

    yield

    aux = sys.modules.get("vibe_quant.dsl.aux_data")
    if aux is None:
        return
    archive, catalog = saved if saved is not None else (None, None)
    aux.configure(archive, catalog)
