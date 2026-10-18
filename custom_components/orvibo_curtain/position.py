"""Mapping between motor positions and Home Assistant positions.

HA's 0-100% spans the motor's closed position to its open position, so a curtain
at the configured open position shows as 100% and one at (or below) the closed
position shows as 0%. Invert swaps the motor's ends before scaling.
"""

from __future__ import annotations


def to_ha_position(device_pos: int, closed: int, opened: int, invert: bool) -> int:
    """Convert a motor position (0-100) to an HA position (0-100)."""
    pos = 100 - device_pos if invert else device_pos
    scaled = (pos - closed) * 100 / (opened - closed)
    return max(0, min(100, round(scaled)))


def to_device_position(ha_pos: int, closed: int, opened: int, invert: bool) -> int:
    """Convert an HA position (0-100) to a motor position (inverse of to_ha_position)."""
    pos = round(closed + ha_pos * (opened - closed) / 100)
    return 100 - pos if invert else pos
