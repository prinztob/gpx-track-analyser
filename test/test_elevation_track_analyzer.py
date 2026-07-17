import datetime
from pathlib import Path

import pytest
from gpxpy.gpx import GPXTrackPoint

from src.Extension import Extension
from src.elevation_track_analyzer import ElevationTrackAnalyzer
from src.entry_point import analyze_elevation_window


def _make_point(
    elevation: float,
    distance: float,
    time: datetime.datetime | None = None,
) -> tuple[GPXTrackPoint, Extension]:
    """Helper to create a (GPXTrackPoint, Extension) tuple for testing."""
    pt = GPXTrackPoint(latitude=47.0, longitude=11.0, elevation=elevation, time=time)
    ext = Extension(distance=distance)
    return (pt, ext)


def _make_track(
    elevations: list[float], distances: list[float]
) -> list[tuple[GPXTrackPoint, Extension]]:
    """Build a list of (GPXTrackPoint, Extension) from elevation and distance arrays."""
    base_time = datetime.datetime(2024, 1, 1, 10, 0, 0)
    return [
        _make_point(e, d, time=base_time + datetime.timedelta(seconds=i * 5))
        for i, (e, d) in enumerate(zip(elevations, distances))
    ]


class TestAnalyzeWindowBasic:
    """Tests for the analyze_window method with basic elevation profiles."""

    def test_flat_track(self) -> None:
        """A flat track should have zero gain, loss, and gradients."""
        points = _make_track(
            elevations=[500.0, 500.0, 500.0, 500.0, 500.0],
            distances=[0.0, 100.0, 200.0, 300.0, 400.0],
        )
        analyzer = ElevationTrackAnalyzer(points)
        result = analyzer.analyze_window(0, 4)

        assert result["elevation_gain"] == 0.0
        assert result["elevation_loss"] == 0.0
        assert result["avg_gradient"] == 0.0
        assert result["max_gradient"] == 0.0
        assert result["window_length"] == 400.0

    def test_pure_ascent(self) -> None:
        """A monotonically ascending track should have elevation gain and positive gradients."""
        points = _make_track(
            elevations=[500.0, 510.0, 520.0, 530.0, 540.0],
            distances=[0.0, 100.0, 200.0, 300.0, 400.0],
        )
        analyzer = ElevationTrackAnalyzer(points)
        result = analyzer.analyze_window(0, 4)

        # Elevation gain should be sum of positive deltas
        assert result["elevation_gain"] > 0.0
        # No loss on a pure ascent
        assert result["elevation_loss"] == 0.0
        # Average gradient: (540 - 500) / 400 * 100 = 10%
        assert result["avg_gradient"] == 10.0
        # Max gradient between consecutive points: 10/100 * 100 = 10%
        assert result["max_gradient"] == 10.0
        assert result["window_length"] == 400.0

    def test_pure_descent(self) -> None:
        """A monotonically descending track should have elevation loss and negative gradients."""
        points = _make_track(
            elevations=[540.0, 530.0, 520.0, 510.0, 500.0],
            distances=[0.0, 100.0, 200.0, 300.0, 400.0],
        )
        analyzer = ElevationTrackAnalyzer(points)
        result = analyzer.analyze_window(0, 4)

        assert result["elevation_gain"] == 0.0
        assert result["elevation_loss"] > 0.0
        # Average gradient: (500 - 540) / 400 * 100 = -10%
        assert result["avg_gradient"] == -10.0
        # Max gradient (steepest, sign preserved): -10/100 * 100 = -10%
        assert result["max_gradient"] == -10.0
        assert result["window_length"] == 400.0

    def test_mixed_ascent_descent(self) -> None:
        """A track with both ascent and descent should report both gain and loss."""
        points = _make_track(
            elevations=[500.0, 550.0, 500.0],
            distances=[0.0, 500.0, 1000.0],
        )
        analyzer = ElevationTrackAnalyzer(points)
        result = analyzer.analyze_window(0, 2)

        assert result["elevation_gain"] > 0.0
        assert result["elevation_loss"] > 0.0
        # Net elevation change is 0, so avg gradient is 0
        assert result["avg_gradient"] == 0.0
        # Max gradient: 50/500 * 100 = 10% (ascent) vs -50/500 * 100 = -10% (descent)
        # Both have same absolute value, first one encountered is +10%
        assert result["max_gradient"] == 10.0
        assert result["window_length"] == 1000.0

    def test_steeper_section_has_larger_max_gradient(self) -> None:
        """The max gradient should reflect the steepest individual segment."""
        points = _make_track(
            elevations=[500.0, 520.0, 530.0, 530.0],
            distances=[0.0, 100.0, 200.0, 300.0],
        )
        analyzer = ElevationTrackAnalyzer(points)
        result = analyzer.analyze_window(0, 3)

        # Segment 1: 20m rise over 100m = 20%
        # Segment 2: 10m rise over 100m = 10%
        # Segment 3: 0m rise over 100m = 0%
        assert result["max_gradient"] == 20.0


class TestAnalyzeWindowSubwindow:
    """Tests for analyze_window using sub-windows of a larger track."""

    def test_subwindow_ascent_only(self) -> None:
        """Analyzing only the ascending portion of a track."""
        points = _make_track(
            elevations=[500.0, 550.0, 600.0, 550.0, 500.0],
            distances=[0.0, 250.0, 500.0, 750.0, 1000.0],
        )
        analyzer = ElevationTrackAnalyzer(points)
        result = analyzer.analyze_window(0, 2)

        assert result["elevation_gain"] > 0.0
        assert result["elevation_loss"] == 0.0
        # Average gradient: (600 - 500) / 500 * 100 = 20%
        assert result["avg_gradient"] == 20.0

    def test_subwindow_descent_only(self) -> None:
        """Analyzing only the descending portion of a track."""
        points = _make_track(
            elevations=[500.0, 550.0, 600.0, 550.0, 500.0],
            distances=[0.0, 250.0, 500.0, 750.0, 1000.0],
        )
        analyzer = ElevationTrackAnalyzer(points)
        result = analyzer.analyze_window(2, 4)

        assert result["elevation_gain"] == 0.0
        assert result["elevation_loss"] > 0.0
        # Average gradient: (500 - 600) / 500 * 100 = -20%
        assert result["avg_gradient"] == -20.0

    def test_single_point_window_raises(self) -> None:
        """A window of a single point (start == end) should raise ValueError."""
        points = _make_track(
            elevations=[500.0, 550.0, 600.0],
            distances=[0.0, 250.0, 500.0],
        )
        analyzer = ElevationTrackAnalyzer(points)
        with pytest.raises(ValueError, match="start_idx must be less than end_idx"):
            analyzer.analyze_window(1, 1)


class TestAnalyzeWindowEdgeCases:
    """Edge case tests for analyze_window."""

    def test_start_idx_out_of_range(self) -> None:
        """A start_idx beyond the track should raise ValueError."""
        points = _make_track(
            elevations=[500.0, 550.0],
            distances=[0.0, 100.0],
        )
        analyzer = ElevationTrackAnalyzer(points)
        with pytest.raises(ValueError, match="Index out of range"):
            analyzer.analyze_window(5, 6)

    def test_end_idx_out_of_range(self) -> None:
        """An end_idx beyond the track should raise ValueError."""
        points = _make_track(
            elevations=[500.0, 550.0],
            distances=[0.0, 100.0],
        )
        analyzer = ElevationTrackAnalyzer(points)
        with pytest.raises(ValueError, match="Index out of range"):
            analyzer.analyze_window(0, 5)

    def test_negative_start_idx(self) -> None:
        """A negative start_idx should raise ValueError."""
        points = _make_track(
            elevations=[500.0, 550.0],
            distances=[0.0, 100.0],
        )
        analyzer = ElevationTrackAnalyzer(points)
        with pytest.raises(ValueError, match="Index out of range"):
            analyzer.analyze_window(-1, 1)

    def test_start_greater_than_end(self) -> None:
        """start_idx > end_idx should raise ValueError."""
        points = _make_track(
            elevations=[500.0, 550.0, 600.0],
            distances=[0.0, 100.0, 200.0],
        )
        analyzer = ElevationTrackAnalyzer(points)
        with pytest.raises(ValueError, match="start_idx must be less than end_idx"):
            analyzer.analyze_window(2, 0)

    def test_zero_distance_between_points(self) -> None:
        """When consecutive points have zero distance, gradient for that segment is skipped."""
        points = _make_track(
            elevations=[500.0, 510.0, 510.0],
            distances=[0.0, 100.0, 100.0],
        )
        analyzer = ElevationTrackAnalyzer(points)
        result = analyzer.analyze_window(0, 2)

        # Only the first segment has distance > 0, so max_gradient = 10/100 * 100 = 10%
        assert result["max_gradient"] == 10.0

    def test_two_point_window(self) -> None:
        """A minimal window of two points should work correctly."""
        points = _make_track(
            elevations=[500.0, 600.0],
            distances=[0.0, 1000.0],
        )
        analyzer = ElevationTrackAnalyzer(points)
        result = analyzer.analyze_window(0, 1)

        assert result["elevation_gain"] > 0.0
        assert result["elevation_loss"] == 0.0
        assert result["avg_gradient"] == 10.0
        assert result["max_gradient"] == 10.0

    def test_result_keys(self) -> None:
        """The result dictionary should contain exactly the expected keys."""
        points = _make_track(
            elevations=[500.0, 550.0, 600.0],
            distances=[0.0, 250.0, 500.0],
        )
        analyzer = ElevationTrackAnalyzer(points)
        result = analyzer.analyze_window(0, 2)

        expected_keys = {"elevation_gain", "elevation_loss", "avg_gradient", "max_gradient", "window_length"}
        assert set(result.keys()) == expected_keys

    def test_rounding_to_three_decimals(self) -> None:
        """Results should be rounded to 3 decimal places."""
        points = _make_track(
            elevations=[500.0, 501.0],
            distances=[0.0, 333.0],
        )
        analyzer = ElevationTrackAnalyzer(points)
        result = analyzer.analyze_window(0, 1)

        # avg_gradient = 1/333 * 100 = 0.3003003... -> rounded to 0.3
        assert result["avg_gradient"] == round(1.0 / 333.0 * 100.0, 3)
        # max_gradient = same as avg_gradient for 2 points
        assert result["max_gradient"] == round(1.0 / 333.0 * 100.0, 3)


class TestAnalyzeWindowWithRealGpx:
    """Integration test using a real GPX file from the resources folder."""

    def test_window_on_real_track(self) -> None:
        """analyze_window should return valid results on a real GPX track."""
        import gpxpy
        from pathlib import Path

        file = Path("./resources/track.gpx")
        gpx = gpxpy.parse(open(file, "r"))
        gpx_points = gpx.tracks[0].segments[0].points

        # Build points_with_time with distance set from gpxpy's 2D distance
        points_with_time: list[tuple[GPXTrackPoint, Extension]] = []
        distance = 0.0
        for i, pt in enumerate(gpx_points):
            if i > 0:
                distance += pt.distance_2d(gpx_points[i - 1])
            ext = Extension(distance=distance)
            if pt.time and pt.elevation:
                points_with_time.append((pt, ext))

        analyzer = ElevationTrackAnalyzer(points_with_time)
        result = analyzer.analyze_window(0, min(100, len(points_with_time) - 1))

        assert result["elevation_gain"] >= 0.0
        assert result["elevation_loss"] >= 0.0
        assert isinstance(result["avg_gradient"], float)
        assert isinstance(result["max_gradient"], float)


class TestAnalyzeElevationWindowEntryPoint:
    """Tests for the analyze_elevation_window entry point function."""

    def test_valid_window(self) -> None:
        """analyze_elevation_window should return the 4 elevation values for a valid window."""
        result = analyze_elevation_window(
            gpx_path="./resources/track.gpx",
            start_idx=0,
            end_idx=50,
        )
        assert isinstance(result, dict)
        assert "elevation_gain" in result
        assert "elevation_loss" in result
        assert "avg_gradient" in result
        assert "max_gradient" in result
        assert "window_length" in result
        assert result["elevation_gain"] >= 0.0
        assert result["elevation_loss"] >= 0.0

    def test_invalid_indices_returns_error_string(self) -> None:
        """analyze_elevation_window should return an error string for invalid indices."""
        result = analyze_elevation_window(
            gpx_path="./resources/track.gpx",
            start_idx=99999,
            end_idx=100000,
        )
        assert isinstance(result, str)
        assert "Error" in result

    def test_start_greater_than_end_returns_error_string(self) -> None:
        """analyze_elevation_window should return an error string when start > end."""
        result = analyze_elevation_window(
            gpx_path="./resources/track.gpx",
            start_idx=10,
            end_idx=5,
        )
        assert isinstance(result, str)
        assert "Error" in result

    def test_with_yaml_extensions(self) -> None:
        """analyze_elevation_window should work with a yaml extensions file."""
        result = analyze_elevation_window(
            gpx_path="./resources/track8.gpx",
            start_idx=0,
            end_idx=50,
            yaml_extensions_path="./resources/track8_extensions.yaml",
        )
        assert isinstance(result, dict)
        assert "elevation_gain" in result
        assert "elevation_loss" in result
        assert "avg_gradient" in result
        assert "max_gradient" in result
        assert "window_length" in result
