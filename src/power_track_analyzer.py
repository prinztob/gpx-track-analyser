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

    def set_time_entries(self):
        for i, point in enumerate(self.points_with_time):
            if i != 0:
                diff = abs((point.time - self.points_with_time[i - 1].time).seconds)
                if 5 < diff < 300:
                    for seconds in range(1, abs((point.time - self.points_with_time[i - 1].time).seconds)):
                        self.time_entries.append(
                            self.points_with_time[i - 1].time + datetime.timedelta(seconds=seconds))
                        self.power_entries.append(0)
                if diff < 300:
                    self.time_entries.append(point.time)
                    self.power_entries.append(point.extensions_calculated.power)
            else:
                self.time_entries.append(point.time)
                self.power_entries.append(point.extensions_calculated.power)

    def analyze(self) -> dict:
        self.set_time_entries()
        max_period = len(self.time_entries) - 1
        power_per_time_entries = [
            PowerPerTime(10, "10s", max_period),
            PowerPerTime(30, "30s", max_period),
            PowerPerTime(60, "1min", max_period),
            PowerPerTime(300, "5min", max_period),
            PowerPerTime(600, "10min", max_period),
            PowerPerTime(1200, "20min", max_period),
            PowerPerTime(1800, "30min", max_period),
            PowerPerTime(3600, "1h", max_period),
            PowerPerTime(7200, "2h", max_period),
            PowerPerTime(18000, "5h", max_period)
        ]
        if len(set(self.power_entries)) > 1 and len(self.power_entries) == len(self.time_entries):
            duration = (self.time_entries[-1] - self.time_entries[0]).seconds
            df = DataFrame({'power': self.power_entries})
            df.index = self.time_entries

            self.data["power_avg"] = int(
                max(df.rolling(f"{duration}s", min_periods=max_period).mean().dropna().values))
            for entry in power_per_time_entries:
                if duration > entry.time_interval:
                    values = df.rolling(entry.window, min_periods=entry.min_period).mean().dropna().values
                    if entry.window == "1min":
                        for i, e in enumerate(self.points_with_time):
                            if i < len(values) - 1:
                                e.extensions_calculated.power60s = int(values[i])
                    if len(values) > 0:
                        self.data[entry.json_key_interval] = int(max(values))
        return self.data


class PowerPerTime(object):
    def __init__(self, time_interval: int, window: str, max_period: int):
        self.time_interval = time_interval
        self.json_key_interval = f"power_{window}"
        self.window = window
        self.min_period = max_period if max_period < time_interval else time_interval
