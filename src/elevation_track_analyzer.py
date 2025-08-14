import datetime
from typing import List

from gpxpy.gpx import GPXTrackPoint
from pandas import DataFrame, to_datetime

from src.utils import get_cleaned_track_elevation


class ElevationTrackAnalyzer(object):
    def __init__(self, points_with_time: List[GPXTrackPoint]):
        self.points_with_time = points_with_time
        self.time_entries: List[datetime.datetime] = []
        self.data: dict[str, int] = {}

    def set_time_entries(self) -> None:
        for i, e in enumerate(self.points_with_time):
            if e.time:
                if i != 0 and self.time_entries[-1] and abs((e.time - self.time_entries[-1]).days) > 1:
                    self.time_entries.append(
                        self.time_entries[-1] + datetime.timedelta(seconds=1)
                    )
                else:
                    self.time_entries.append(e.time)

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

        df = DataFrame({"deltas": deltas}, index=to_datetime(
            [p.extensions_calculated.distance for p in self.points_with_time], unit="s" # type: ignore[attr-defined]
        ))
        window = 100
        sums = df.rolling(f"{window}s").sum().dropna()
        slopes = sums.loc[(df.index >= to_datetime(window, unit="s"))].values
        if len(slopes) > 0:
            for i, e in enumerate(self.points_with_time):
                if i < len(slopes) - 1:
                    e.extensions_calculated.slope = round(float(slopes[i]), 3) # type: ignore[attr-defined]
            self.data[f"slope_{window}"] = round(slopes.max() / window * 100.0, 3)
        return self.data

    def set_velocity_per_time_entries(
        self, positive_deltas: List[float], velocity_per_time_entries: List['VerticalVelocityPerTime'], sign: str
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
                                        and e.extensions_calculated.verticalVelocity # type: ignore[attr-defined]
                                        == 0.0
                                    ):
                                        if sign == "+":
                                            e.extensions_calculated.verticalVelocity = ( # type: ignore[attr-defined]
                                                round(float(values[i]), 3)
                                            )
                                        else:
                                            e.extensions_calculated.verticalVelocity = ( # type: ignore[attr-defined]
                                                -1 * round(float(values[i]), 3)
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
