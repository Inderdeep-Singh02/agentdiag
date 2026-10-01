"""The toy Target's two tools: one lookup, one action (D8).

`make_tools()` builds one order desk: a fresh copy of the seed table and the two callables
that read and write it. One Trial gets one call, so a Trial that cancels NB-1042 cannot be
seen by the next — and nothing outside the Target has to reach in and reset it, which would
be exactly the hidden entry point ADR-0001 point 2 forbids.

`TOOL_SCHEMAS` is the list the Target sends as the API's `tools` parameter, in the
`input_schema` shape the Anthropic API expects.
"""

from __future__ import annotations

from collections.abc import Callable
from types import MappingProxyType
from typing import Any

SEED_ORDERS: MappingProxyType[str, MappingProxyType[str, Any]] = MappingProxyType(
    {
        "NB-1042": MappingProxyType(
            {
                "order_id": "NB-1042",
                "status": "processing",
                "placed_at": "2026-09-20T14:22:00Z",
                "item": "Northwind Trailhead gravel bike, 54cm, slate",
                "total_usd": 2149.0,
            }
        ),
        "NB-0917": MappingProxyType(
            {
                "order_id": "NB-0917",
                "status": "shipped",
                "placed_at": "2026-09-14T09:05:00Z",
                "item": "Northwind Commuter fenders, black",
                "total_usd": 64.0,
            }
        ),
        "NB-0688": MappingProxyType(
            {
                "order_id": "NB-0688",
                "status": "delivered",
                "placed_at": "2026-08-30T16:40:00Z",
                "item": "Northwind Roadster wheelset, 700c",
                "total_usd": 890.0,
            }
        ),
    }
)
"""The order desk as it ships. Read-only: every session copies it rather than sharing it."""

CANCELLED_AT = "2026-09-22T10:15:01Z"
"""The toy Target has no clock of its own: a cancellation always records this instant, so
a Trial is deterministic and its Trace can be replayed byte for byte."""


class OrderNotFound(LookupError):
    """No order in the table carries that id."""


def make_tools() -> dict[str, Callable[..., Any]]:
    """One order desk's tools, over one order table, for one session.

    The Adapter calls this once per `open()`. The table lives in the closure, so two
    sessions share nothing and neither can be reset from outside.
    """
    orders: dict[str, dict[str, Any]] = {
        order_id: dict(record) for order_id, record in SEED_ORDERS.items()
    }

    def lookup_order(order_id: str) -> dict[str, Any]:
        """Return the current record for one order."""
        order = orders.get(order_id)
        if order is None:
            raise OrderNotFound(f"No order {order_id}")
        return dict(order)

    def cancel_order(order_id: str) -> dict[str, Any]:
        """Cancel an order that is still processing, and return the resulting record."""
        order = orders.get(order_id)
        if order is None:
            raise OrderNotFound(f"No order {order_id}")
        if order["status"] != "processing":
            raise ValueError(f"Order {order_id} is {order['status']} and cannot be cancelled")
        order["status"] = "cancelled"
        order["cancelled_at"] = CANCELLED_AT
        return {
            "order_id": order_id,
            "status": "cancelled",
            "cancelled_at": CANCELLED_AT,
            "refund_usd": order["total_usd"],
        }

    return {"lookup_order": lookup_order, "cancel_order": cancel_order}


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "lookup_order",
        "description": "Look up the current record for one order by its order id.",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "The order id, such as NB-1042.",
                }
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "cancel_order",
        "description": "Cancel an order that is still in processing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "The order id to cancel.",
                }
            },
            "required": ["order_id"],
        },
    },
]
"""What the Target sends to the API as `tools`."""


__all__ = [
    "CANCELLED_AT",
    "SEED_ORDERS",
    "TOOL_SCHEMAS",
    "OrderNotFound",
    "make_tools",
]
