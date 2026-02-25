import unittest
from pathlib import Path

from numpy.ma.testutils import assert_equal

from src.gpx_track_analyzer import TrackAnalyzer
from src.power_track_analyzer import PowerTrackAnalyzer


class TestPowerTrackAnalyzer:
    def test_5h_power(self) -> None:
        track_analyzer = TrackAnalyzer(Path("./resources/track_cleaned.gpx"))
        track_analyzer.set_all_points_with_distance()
        # Create PowerTrackAnalyzer instance
        analyzer = PowerTrackAnalyzer(
            [e for e in track_analyzer.all_points_with_extension if e[0].time]
        )

        # Analyze the track
        result = analyzer.analyze()

        # Verify that 5h power is 60W
        assert_equal(result.get("power_5h", 0), 63, "5h power should be 63 W")


if __name__ == "__main__":
    unittest.main()
