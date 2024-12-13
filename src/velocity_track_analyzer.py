import json
from dataclasses import dataclass
from typing import List

from gpxpy.gpx import GPXTrackPoint
from pandas import DataFrame, to_datetime


def get_velocity_entries_from_garmin(split_files: List[str]) -> List["VelocityEntry"]:
    if split_files:
        velocity_entries: List["VelocityEntry"] = []
        for file in split_files:
            data = json.load(open(file))
            if "lapDTOs" in data:
                for lap in data["lapDTOs"]:
                    velocity_entries.append(
                        VelocityEntry(
                            float(lap["distance"]),
                            float(lap["movingDuration"])
                        )
                    )
        return velocity_entries
    else:
        return []


class VelocityTrackAnalyzer(object):
    def __init__(self, points: List[GPXTrackPoint], split_files: List[str] = None):
        self.points_with_time = points
        self.time_deltas = []
        self.distance_entries = [p.extensions_calculated.distance for p in self.points_with_time]
        self.velocity_entries_from_garmin = get_velocity_entries_from_garmin(split_files)
        self.data = {}

    def get_time_entries(self):
        for i, e in enumerate(self.points_with_time):
            if i != 0 and abs((e.time - self.points_with_time[i - 1].time).days) > 1:
                time = 1
            else:
                time = (e.time - self.points_with_time[i - 1].time).seconds
            self.time_deltas.append(time)

    def get_average_velocity_for_kilometers(self, kilometer: int) -> float:
        velocities_in_kilometer_interval: List[float] = []
        for i, e in enumerate(self.velocity_entries_from_garmin):
            sum_kilometers = 0.0
            sum_duration_hours = 0.0
            j = i
            while sum_kilometers < kilometer and j < len(self.velocity_entries_from_garmin):
                sum_kilometers += self.velocity_entries_from_garmin[j].meter / 1000
                sum_duration_hours += self.velocity_entries_from_garmin[j].seconds / 3600
                j += 1
            if sum_kilometers >= kilometer:
                velocities_in_kilometer_interval.append(sum_kilometers / sum_duration_hours)
        return max(velocities_in_kilometer_interval) if len(velocities_in_kilometer_interval) else 0.0

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
                        velocity_from_track = entry.window_in_km * 3600 / times.min()
                        velocity_from_garmin = self.get_average_velocity_for_kilometers(entry.window_in_km)
                        self.data[
                            entry.json_key_interval] = velocity_from_track if velocity_from_track > velocity_from_garmin else velocity_from_garmin
        return self.data


class AverageVelocityPerDistance(object):
    def __init__(self, window_in_km: int):
        self.json_key_interval = f"avg_velocity_{window_in_km}km"
        self.window_in_km = window_in_km
        self.window_in_m = window_in_km * 1000


@dataclass
class VelocityEntry:
    meter: float
    seconds: float
