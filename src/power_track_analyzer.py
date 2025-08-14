import datetime
from typing import List

from gpxpy.gpx import GPXTrackPoint
from pandas import DataFrame


class PowerTrackAnalyzer(object):
    def __init__(self, points: List[GPXTrackPoint]):
        self.points_with_time = points
        self.time_entries = []
        self.power_entries = []
        self.data = {}
        self.duration = 0
        self.max_period = 0

    def set_time_entries(self):
        for i, point in enumerate(self.points_with_time):
            if i != 0:
                diff = abs((point.time - self.points_with_time[i - 1].time).seconds)
                if 5 < diff < 300:
                    for seconds in range(
                        1, abs((point.time - self.points_with_time[i - 1].time).seconds)
                    ):
                        self.time_entries.append(
                            self.points_with_time[i - 1].time
                            + datetime.timedelta(seconds=seconds)
                        )
                        self.power_entries.append(0)
                if diff < 300:
                    self.time_entries.append(point.time)
                    self.power_entries.append(point.extensions_calculated.power)
            else:
                self.time_entries.append(point.time)
                self.power_entries.append(point.extensions_calculated.power)
        self.duration = (self.time_entries[-1] - self.time_entries[0]).seconds
        self.max_period = len(self.time_entries) - 1
        duration_to_add = 18500 - (self.time_entries[-1] - self.time_entries[0]).seconds
        if duration_to_add > 0:
            for seconds in range(1, duration_to_add):
                self.time_entries.append(
                    self.points_with_time[-1].time + datetime.timedelta(seconds=seconds)
                )
                self.power_entries.append(0)

    def analyze(self) -> dict:
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
                                e.extensions_calculated.power60s = int(values[i])
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
