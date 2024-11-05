from typing import List

from gpxpy.gpx import GPXTrackPoint
from pandas import DataFrame, to_datetime


class VelocityTrackAnalyzer(object):
    def __init__(self, points: List[GPXTrackPoint]):
        self.points_with_time = points
        self.time_deltas = []
        self.distance_entries = [p.extensions_calculated.distance for p in self.points_with_time]

        self.data = {}

    def get_time_entries(self):
        for i, e in enumerate(self.points_with_time):
            if i != 0 and abs((e.time - self.points_with_time[i - 1].time).days) > 1:
                time = 1
            else:
                time = (e.time - self.points_with_time[i - 1].time).seconds
            self.time_deltas.append(time)

    def analyze(self) -> dict:
        self.get_time_entries()
        entries = [
            AverageVelocityPerDistance(1),
            AverageVelocityPerDistance(5),
            AverageVelocityPerDistance(10),
            AverageVelocityPerDistance(15),
            AverageVelocityPerDistance(20),
            AverageVelocityPerDistance(30),
            AverageVelocityPerDistance(40),
            AverageVelocityPerDistance(50),
            AverageVelocityPerDistance(75),
            AverageVelocityPerDistance(100),
        ]
        if len(self.distance_entries) == len(self.time_deltas):
            df = DataFrame({'time': self.time_deltas})
            df.index = to_datetime(self.distance_entries, unit="s")

            for entry in entries:
                if max(self.distance_entries) > entry.window_in_m:
                    sums = df.rolling(f"{entry.window_in_m}s").sum().dropna()
                    times = sums.loc[(df.index >= to_datetime(entry.window_in_m, unit="s"))].values
                    if len(times) > 0:
                        self.data[entry.json_key_interval] = entry.window_in_km * 3600 / times.min()
        return self.data


class AverageVelocityPerDistance(object):
    def __init__(self, window_in_km: int):
        self.json_key_interval = f"avg_velocity_{window_in_km}km"
        self.window_in_km = window_in_km
        self.window_in_m = window_in_km * 1000
