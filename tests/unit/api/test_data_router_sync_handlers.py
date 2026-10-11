"""Regression guard: GET handlers in the data router stay plain ``def``.

A GET handler declared ``async def`` runs its body on the event loop. The data
router's GET endpoints do blocking sqlite/os.walk work, so an ``async def``
there froze the whole API (up to 22 s on a 2 GB archive). FastAPI runs plain
``def`` handlers in its threadpool instead. POST /ingest, /update and /rebuild
only enqueue a job and may stay async.
"""

from __future__ import annotations

import inspect

from fastapi.routing import APIRoute

from vibe_quant.api.routers.data import router


def test_get_handlers_are_not_async() -> None:
    """Every GET route on the data router must use a sync endpoint."""
    get_routes = [
        route
        for route in router.routes
        if isinstance(route, APIRoute) and "GET" in (route.methods or set())
    ]
    assert get_routes, "data router exposes no GET routes; guard would be vacuous"

    for route in get_routes:
        assert not inspect.iscoroutinefunction(route.endpoint), (
            f"GET {route.path} is async def; blocking work would run on the event loop. "
            "Declare it plain def so FastAPI runs it in the threadpool."
        )
