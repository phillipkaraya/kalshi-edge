"""Pluggable notifier for fills, kill-switch trips, and gate changes.

The default is silent. ``LogNotifier`` writes to the logger. An iMessage notifier
is left as an integration point: Kalshi Edge runs as a Python process while
Phil's iMessage send lives behind an MCP / Shortcuts boundary, so wire it from
the orchestrating layer rather than importing it here.
"""

from __future__ import annotations

import logging
from typing import Protocol

logger = logging.getLogger("kalshi_edge.alerts")


class Notifier(Protocol):
    def notify(self, event: str, message: str) -> None: ...


class NullNotifier:
    """Discards notifications (default)."""

    def notify(self, event: str, message: str) -> None:  # noqa: D102
        return None


class LogNotifier:
    """Writes notifications to the logger."""

    def notify(self, event: str, message: str) -> None:  # noqa: D102
        logger.info("[%s] %s", event, message)
