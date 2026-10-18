"""Tests for motor <-> Home Assistant position scaling."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.orvibo_curtain.position import to_device_position, to_ha_position


class TestDefaults(unittest.TestCase):
    """With closed=0 and open=100 positions pass through unchanged."""

    def test_identity(self):
        for pos in (0, 37, 100):
            self.assertEqual(to_ha_position(pos, 0, 100, False), pos)
            self.assertEqual(to_device_position(pos, 0, 100, False), pos)


class TestOpenAndClosedPosition(unittest.TestCase):
    """closed=6, open=90: HA 0-100% spans the motor's 6-90%."""

    def test_commands(self):
        self.assertEqual(to_device_position(0, 6, 90, False), 6)
        self.assertEqual(to_device_position(50, 6, 90, False), 48)
        self.assertEqual(to_device_position(100, 6, 90, False), 90)

    def test_reports(self):
        self.assertEqual(to_ha_position(6, 6, 90, False), 0)
        self.assertEqual(to_ha_position(90, 6, 90, False), 100)
        self.assertEqual(to_ha_position(48, 6, 90, False), 50)

    def test_reports_beyond_the_range_are_clamped(self):
        # The motor bounces back to 4-6% after closing, and may report above 90%.
        self.assertEqual(to_ha_position(4, 6, 90, False), 0)
        self.assertEqual(to_ha_position(100, 6, 90, False), 100)

    def test_round_trip(self):
        for ha in range(0, 101):
            self.assertAlmostEqual(
                to_ha_position(to_device_position(ha, 6, 90, False), 6, 90, False), ha, delta=1
            )


class TestInvert(unittest.TestCase):
    """Invert swaps the motor's ends before scaling."""

    def test_inverted_commands_and_reports(self):
        self.assertEqual(to_device_position(0, 6, 90, True), 94)
        self.assertEqual(to_device_position(100, 6, 90, True), 10)
        self.assertEqual(to_ha_position(94, 6, 90, True), 0)
        self.assertEqual(to_ha_position(10, 6, 90, True), 100)


if __name__ == "__main__":
    unittest.main()
