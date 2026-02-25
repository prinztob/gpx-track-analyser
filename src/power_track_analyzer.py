import datetime
from typing import List, Tuple

from gpxpy.gpx import GPXTrackPoint
from pandas import DataFrame

from src.Extension import Extension


class PowerTrackAnalyzer(object):
    def __init__(self, points: List[Tuple[GPXTrackPoint, Extension]]):
        self.points_with_time = points
        self.time_entries: list[datetime.datetime] = []
        self.power_entries: list[int] = []
        self.data: dict[str, int] = {}
        self.duration = 0
        self.max_period = 0

    def set_time_entries(self) -> None:
        for i, point in enumerate(self.points_with_time):
            if point[0].time:
                if i != 0:
                    last_point = self.points_with_time[i - 1]
                    if last_point[0].time:
                        diff = abs((point[0].time - last_point[0].time).seconds)
                        # Only handle gaps larger than 5 seconds by adding intermediate time entries with zero power
                        if diff > 5:
                            for seconds in range(1, diff):
                                self.time_entries.append(
                                    last_point[0].time
                                    + datetime.timedelta(seconds=seconds)
                                )
                                self.power_entries.append(0)
                        # Always add the current point
                        self.time_entries.append(point[0].time)
                        self.power_entries.append(point[1].power)
                else:
                    self.time_entries.append(point[0].time)
                    self.power_entries.append(point[1].power)
        self.duration = (self.time_entries[-1] - self.time_entries[0]).seconds
        self.max_period = len(self.time_entries) - 1
        # Removed padding at the end as it's not necessary and might cause issues

    def analyze(self) -> dict[str, int]:
        self.set_time_entries()
        power_per_time_entries = [
            PowerPerTime(10, "10s"),
            PowerPerTime(30, "30s"),
            PowerPerTime(60, "1min"),
            PowerPerTime(300, "5min"),
            PowerPerTime(600, "10min"),
            PowerPerTime(1200, "20min"),
            PowerPerTime(1800, "30min"),
            PowerPerTime(3600, "1h"),
            PowerPerTime(7200, "2h"),
            PowerPerTime(10800, "3h"),
            PowerPerTime(14400, "4h"),
            PowerPerTime(18000, "5h"),
        ]
        if len(set(self.power_entries)) > 1 and len(self.power_entries) == len(
            self.time_entries
        ):
            df = DataFrame({"power": self.power_entries}, index=self.time_entries)

            self.data["power_avg"] = int(
                max(
                    df.rolling(f"{self.duration}s", min_periods=self.max_period)
                    .mean()
                    .dropna()
                    .values
                )
            )
            for entry in power_per_time_entries:
                if self.duration > entry.time_interval * 0.8:
                    values = (
                        df.rolling(entry.window, min_periods=entry.time_interval)
                        .mean()
                        .dropna()
                        .values
                    )
                    if entry.window == "1min":
                        for i, e in enumerate(self.points_with_time):
                            if i < len(values) - 1:
                                e[1].power60s = int(values[i])
                    if len(values) > 0:
                        self.data[entry.json_key_interval] = int(max(values))
                    else:
                        means = df.rolling(entry.window).mean().dropna()
                        values_2nd_try = means.loc[
                            (
                                df.index
                                >= df.index[0]
                                + datetime.timedelta(seconds=entry.time_interval)
                            )
                        ].values
                        if len(values_2nd_try) > 0:
                            self.data[entry.json_key_interval] = int(
                                max(values_2nd_try)
                            )
        return self.data


class PowerPerTime(object):
    def __init__(self, time_interval: int, window: str):
        self.time_interval = time_interval
        self.json_key_interval = f"power_{window}"
        self.window = window
