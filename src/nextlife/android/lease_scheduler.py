"""Lease scheduler that honours PhoneBroker cooldowns (TZ Connectors 5.3.3).

Connectors do not store their own intervals. This scheduler records the
cooldown the broker reported and refuses another lease of that platform
until it expires. Other platforms stay eligible.
"""

from __future__ import annotations

from datetime import datetime, timedelta


class LeaseScheduler:
    def __init__(self) -> None:
        self._until: dict[str, datetime] = {}

    def note_cooldown(self, platform: str, seconds: int, now: datetime) -> None:
        self._until[platform] = now + timedelta(seconds=max(0, seconds))

    def can_lease(self, platform: str, now: datetime) -> bool:
        until = self._until.get(platform)
        return until is None or now >= until

    def ready_platforms(self, platforms: list[str], now: datetime) -> list[str]:
        return [name for name in platforms if self.can_lease(name, now)]
