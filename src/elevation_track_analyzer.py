import datetime
from typing import List, Tuple

from gpxpy.gpx import GPXTrackPoint
from pandas import DataFrame, to_datetime

from src.Extension import Extension
from src.utils import get_cleaned_track_elevation


class ElevationTrackAnalyzer(object):
    def __init__(self, points_with_time: List[Tuple[GPXTrackPoint, Extension]]):
        self.points_with_time = points_with_time
        self.time_entries: List[datetime.datetime] = []
        self.data: dict[str, int] = {}

    def analyze_window(self, start_idx: int, end_idx: int) -> dict[str, float]:
        """Analyze elevation data for a window of the track between start_idx and end_idx (inclusive).

        Reuses get_cleaned_track_elevation for cleaned elevation deltas and the same
        positive/negative delta pattern as analyze() for elevation gain/loss.

        Args:
            start_idx: Start index of the window (inclusive, 0-based).
            end_idx: End index of the window (inclusive, 0-based).

        Returns:
            Dictionary with elevation_gain, elevation_loss, avg_gradient, max_gradient,
            window_length.

        Raises:
            ValueError: If indices are out of range or start_idx >= end_idx.
        """
        if start_idx < 0 or end_idx >= len(self.points_with_time):
            raise ValueError(
                f"Index out of range: start_idx={start_idx}, end_idx={end_idx}, "
                f"track length={len(self.points_with_time)}"
            )
        if start_idx >= end_idx:
            raise ValueError(
                f"start_idx must be less than end_idx: start_idx={start_idx}, end_idx={end_idx}"
            )

        window_points = self.points_with_time[start_idx : end_idx + 1]

        # Reuse get_cleaned_track_elevation for cleaned elevation deltas
        deltas = get_cleaned_track_elevation(window_points)

        # Elevation gain and loss (same pattern as analyze())
        positive_deltas = [(d if d > 0 else 0) for d in deltas]
        negative_deltas = [(abs(d) if d < 0 else 0) for d in deltas]
        elevation_gain = sum(positive_deltas)
        elevation_loss = sum(negative_deltas)

        # Average gradient: net elevation change / total distance * 100
        total_distance = window_points[-1][1].distance - window_points[0][1].distance
        start_elevation = (
            window_points[0][0].elevation
            if window_points[0][0].elevation
            else 0
        )
        end_elevation = (
            window_points[-1][0].elevation
            if window_points[-1][0].elevation
            else 0
        )

        if total_distance > 0:
            avg_gradient = (end_elevation - start_elevation) / total_distance * 100.0
        else:
            avg_gradient = 0.0

        # Max gradient: steepest gradient between consecutive points (max absolute value, sign preserved)
        max_gradient = 0.0
        for i in range(1, len(window_points)):
            delta_elevation = (
                (window_points[i][0].elevation - window_points[i - 1][0].elevation)
                if window_points[i][0].elevation and window_points[i - 1][0].elevation
                else 0
            )
            delta_distance = (
                window_points[i][1].distance - window_points[i - 1][1].distance
            )
            if delta_distance > 0:
                gradient = delta_elevation / delta_distance * 100.0
                if abs(gradient) > abs(max_gradient):
                    max_gradient = gradient

        return {
            "elevation_gain": round(elevation_gain, 3),
            "elevation_loss": round(elevation_loss, 3),
            "avg_gradient": round(avg_gradient, 3),
            "max_gradient": round(max_gradient, 3),
            "window_length": round(total_distance, 3),
        }

    def set_time_entries(self) -> None:
        for i, e in enumerate(self.points_with_time):
            if e[0].time:
                if (
                    i != 0
                    and self.time_entries[-1]
                    and abs((e[0].time - self.time_entries[-1]).days) > 1
                ):
                    self.time_entries.append(
                        self.time_entries[-1] + datetime.timedelta(seconds=1)
                    )
                else:
                    self.time_entries.append(e[0].time)

    def analyze(self) -> dict[str, int]:
        self.set_time_entries()
        deltas = get_cleaned_track_elevation(self.points_with_time)
        positive_deltas = [(e if e > 0 else 0) for e in deltas]
        negative_deltas = [(abs(e) if e < 0 else 0) for e in deltas]
        max_period = len(self.time_entries) - 1
        velocity_per_time_entries = [
            VerticalVelocityPerTime(60, "60s", max_period),
            VerticalVelocityPerTime(600, "600s", max_period),
            VerticalVelocityPerTime(3600, "3600s", max_period),
        ]
        self.set_velocity_per_time_entries(
            positive_deltas, velocity_per_time_entries, "+"
        )
        self.set_velocity_per_time_entries(
            negative_deltas, velocity_per_time_entries, "-"
        )

        df = DataFrame(
            {"deltas": deltas},
            index=to_datetime(
                [p[1].distance for p in self.points_with_time],
                unit="s",
            ),
        )
        window = 100
        sums = df.rolling(f"{window}s").sum().dropna()
        slopes = sums.loc[(df.index >= to_datetime(window, unit="s"))].values
        if len(slopes) > 0:
            for i, e in enumerate(self.points_with_time):
                if i < len(slopes) - 1:
                    e[1].slope = round(float(slopes[i]), 3)
            self.data[f"slope_{window}"] = round(slopes.max() / window * 100.0, 3)
        return self.data

    def set_velocity_per_time_entries(
        self,
        positive_deltas: List[float],
        velocity_per_time_entries: List["VerticalVelocityPerTime"],
        sign: str,
    ) -> None:
        if len(positive_deltas) > 0 and len(positive_deltas) == len(self.time_entries):
            duration = (self.time_entries[-1] - self.time_entries[0]).seconds
            df = DataFrame({"deltas": positive_deltas}, index=self.time_entries)
            for entry in velocity_per_time_entries:
                if duration > entry.time_interval:
                    try:
                        values = df.rolling(entry.window).sum().dropna().values
                        if len(values) > 0:
                            if entry.window == "60s":
                                for i, e in enumerate(self.points_with_time):
                                    if (
                                        i < len(values) - 1
                                        and e[1].vertical_velocity == 0.0
                                    ):
                                        if sign == "+":
                                            e[1].vertical_velocity = round(
                                                float(values[i]), 3
                                            )
                                        else:
                                            e[1].vertical_velocity = -1 * round(
                                                float(values[i]), 3
                                            )
                            self.data[f"{entry.json_key_interval}_{sign}"] = round(
                                (max(values / entry.time_interval))[0], 3
                            )
                    except ValueError as ex:
                        print(f"Failed {ex}")
        else:
            print(
                "Could not set_velocity_per_time_entries because array length does not match."
            )


class VerticalVelocityPerTime(object):
    def __init__(self, time_interval: int, window: str, max_period: int):
        self.time_interval = time_interval
        self.json_key_interval = f"vertical_velocity_{window}"
        self.window = window
        self.min_period = max_period if max_period < time_interval else time_interval
