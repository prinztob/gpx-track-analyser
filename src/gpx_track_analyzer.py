import datetime
import json
import math
import re
from typing import List, Dict

import geopy.distance
import gpxpy.gpx
import lxml.etree as mod_etree
import yaml
from pandas import DataFrame, to_datetime

from src.Extension import Extension


class TrackAnalyzer(object):
    NAMESPACE_NAME = 'http://www.garmin.com/xmlschemas/TrackPointExtension/v1'
    NAMESPACE = '{' + NAMESPACE_NAME + '}'
    TRACK_EXTENSIONS = 'TrackPointExtension'
    SUFFIX = "_simplified"

    def __init__(self, file, yaml_file=None, remove_extensions=False):
        self.file = file
        self.yaml_file = yaml_file if yaml_file else file.replace(".gpx", "_extensions.yaml")
        with open(file, 'r') as f:
            search_result = re.search(r'<\?xml(.|\n)*?(\<\/gpx\>)', f.read())
            if search_result:
                self.gpx_file = search_result.group(0)
        self.data = {}
        self.all_points = []
        self.points_with_time = []
        self.gpx = None
        self.slopes = []
        self.time_entries = []
        self.distance_entries = []
        self.distance_entries_time = []
        self.power_entries = []
        self.vertical_velocities = {}
        self.slope_100 = 0
        self.vertical_velocities_60s = 0
        self.vertical_velocities_600s = 0
        self.vertical_velocities_3600s = 0
        self.duration = 0
        self.remove_extensions = remove_extensions

    def write_file(self, file=None, to_yaml=False):
        if not file:
            file = self.file
        extensions = []
        for track in self.gpx.tracks:
            for segment in track.segments:
                for point in segment.points:
                    extensions.append(Extension.parse(point.extensions))
                    if self.remove_extensions:
                        point.extensions = None
        if self.remove_extensions:
            with open(file, "w") as f:
                f.write(self.gpx.to_xml())
        gpx_file_simplified = prefix_filename(file)
        gpx_file_gpxpy = file.replace(".gpx", "_gpxpy.json")
        self.set_gpx_data()
        with open(gpx_file_gpxpy, 'w') as fp:
            json.dump(self.data, fp, indent=4)
        print(f"Written data of track to {gpx_file_gpxpy}")
        self.gpx.simplify()
        if self.yaml_file:
            self.write_extensions_to_yaml(extensions)
        with open(gpx_file_simplified, 'w') as f:
            f.write(self.gpx.to_xml())
        print(f"Written simplified track to {gpx_file_simplified}")

    def write_extensions_to_yaml(self, extensions: List["Extension"]):
        with open(self.yaml_file, 'w', encoding='utf-8') as file:
            yaml.dump({"extensions": [e.to_dict() for e in extensions]}, file, default_flow_style=False)
        print(f"Written extensions to {self.yaml_file}")

    def analyze(self):
        start_time = datetime.datetime.now()
        self.set_all_points_with_distance()
        self.set_vertical_velocity(60, update_points=True)
        self.set_vertical_velocity(600)
        self.set_vertical_velocity(3600)
        self.set_slope(100)
        self.duration = (datetime.datetime.now() - start_time).total_seconds()

    def get_maximal_values(self):
        self.slope_100 = max(self.slopes) if len(self.slopes) > 0 else 0
        self.vertical_velocities_60s = max(self.vertical_velocities["60"]) if len(
            self.vertical_velocities["60"]) > 0 else 0
        self.vertical_velocities_600s = max(self.vertical_velocities["600"]) if len(
            self.vertical_velocities["600"]) > 0 else 0
        self.vertical_velocities_3600s = max(self.vertical_velocities["3600"]) if len(
            self.vertical_velocities["3600"]) > 0 else 0

    def parse_track(self):
        with open(self.file, 'r') as f:
            search_result = re.search(r'<\?xml(.|\n)*?(\<\/gpx\>)', f.read())
            if search_result:
                self.gpx = gpxpy.parse(search_result.group(0))
            else:
                self.gpx = gpxpy.parse(f)

    def set_all_points_with_distance(self):
        print(f"Read and add distance to track file {self.file}")
        if self.gpx_file:
            if self.gpx is None:
                self.parse_track()
            distance = 0.0
            last_point = None
            last_time = None
            for track in self.gpx.tracks:
                for segment in track.segments:
                    points = []
                    for point in segment.points:
                        if point.latitude != 0 and point.longitude != 0:
                            if last_point:
                                distance += geopy.distance.distance((last_point.latitude, last_point.longitude),
                                                                    (point.latitude, point.longitude)).km
                            self.set_tag_in_extensions(distance * 1000, point, "distance")
                            point.distance = distance * 1000
                            self.distance_entries.append(int(point.distance))
                            if last_point and point.time and last_point.time:
                                self.distance_entries_time.append((point.time - last_point.time).total_seconds())
                            else:
                                self.distance_entries_time.append(0)
                            self.all_points.append(point)
                            points.append(point)
                            if point.time:
                                self.points_with_time.append(point)
                            all_power_entries = [el.text for el in point.extensions[0] if 'power' in el.tag]
                            if len(all_power_entries) > 0:
                                if last_time and point.time:
                                    diff = abs((point.time - last_time).seconds)
                                    if 5 < diff < 300:
                                        for seconds in range(1, abs((point.time - last_time).seconds)):
                                            self.time_entries.append(last_time + datetime.timedelta(seconds=seconds))
                                            self.power_entries.append(0)
                                    if diff < 300:
                                        self.time_entries.append(point.time)
                                        self.power_entries.append(all_power_entries[0])
                                        last_time = point.time
                            last_point = point
                    segment.points = points

    def set_tag_in_extensions(self, value, point, tag_name):
        tag = f"{self.NAMESPACE}{self.TRACK_EXTENSIONS}"
        if len([e for e in point.extensions if e.tag == tag]) == 0:
            self.gpx.nsmap["n3"] = self.NAMESPACE_NAME
            point.extensions.append(mod_etree.Element(tag))
        root = mod_etree.Element(self.NAMESPACE + tag_name)
        root.text = f"{value}"
        elements = [e for e in point.extensions if e.tag == tag][0]
        extensions = [e for e in elements if e.tag.endswith("}" + tag_name)]
        if len(extensions) == 0:
            elements.append(root)

    def set_slope(self, max_meter_interval, use_regression=True):
        sum_meters = 0.0
        i = 0
        track_points_for_interval = []
        middle_entry = None
        while i < len(self.all_points) - 1:
            if middle_entry and sum_meters >= max_meter_interval and len(track_points_for_interval) > 2:
                if use_regression:
                    x_array = [entry.distance for entry in track_points_for_interval]
                    y_array = [entry.elevation for entry in track_points_for_interval]
                    if len(set(y_array)) > 1:
                        linear_regression = estimate_coefficients(x_array, y_array)
                        slope = linear_regression[1] * 100 if linear_regression[2] > 0.9 else 0.0
                    else:
                        slope = 0
                else:
                    elevation = track_points_for_interval[-1].elevation - track_points_for_interval[0].elevation
                    slope = 0.0 if sum_meters == 0.0 else (elevation / sum_meters) * 100
                self.slopes.append(slope)
                track_points_for_interval = track_points_for_interval[1: -1]
                middle_entry = track_points_for_interval[0]
            else:
                slope = 0
            self.set_tag_in_extensions(slope * 100, self.all_points[i], "slope")
            if (not self.all_points[i].distance is None and self.all_points[i].distance >= 0.0
                    and self.all_points[i].elevation):
                track_points_for_interval.append(self.all_points[i])
                sum_meters = track_points_for_interval[-1].distance - track_points_for_interval[0].distance
                if sum_meters > max_meter_interval / 2 and not middle_entry:
                    middle_entry = self.all_points[i]
            i += 1

    def set_vertical_velocity(self, max_time_interval, update_points=False, use_regression=False):
        diff_times = 0.0
        self.vertical_velocities[str(max_time_interval)] = []
        i = 0
        track_points_for_interval = []
        middle_entry = None
        while i < len(self.all_points) - 1:
            vertical_velocity = 0
            if middle_entry is not None and diff_times >= max_time_interval and len(track_points_for_interval) > 2:
                reduced_track_points_for_interval = reduce_track_to_relevant_elevation_points(track_points_for_interval)
                relevant_track_points_for_interval, gain, loss = remove_elevation_differences_smaller_as(
                    reduced_track_points_for_interval, 10)
                if len(relevant_track_points_for_interval) > 1:
                    if use_regression:
                        x_array = [(entry.time - self.all_points[0].time).total_seconds() for entry in
                                   track_points_for_interval]
                        y_array = [entry.elevation for entry in track_points_for_interval]
                        if len(set(y_array)) > 1:
                            linear_regression = estimate_coefficients(x_array, y_array)
                            vertical_velocity = linear_regression[1] if linear_regression[2] > 0.9 else 0.0
                    else:
                        vertical_velocity = 0.0 if diff_times == 0.0 else (gain / diff_times)

                    self.vertical_velocities[str(max_time_interval)].append(vertical_velocity)
                track_points_for_interval = track_points_for_interval[1: -1]
                middle_entry = None
            if update_points:
                self.set_tag_in_extensions(vertical_velocity * max_time_interval, self.all_points[i], "vvelocity")
            if not self.all_points[i].time is None and self.all_points[i].elevation:
                track_points_for_interval.append(self.all_points[i])
                diff_times = (track_points_for_interval[-1].time - track_points_for_interval[0].time).total_seconds()
                if diff_times > max_time_interval / 2 and not middle_entry:
                    middle_entry = self.all_points[i]
            i += 1

    def set_gpx_data(self):
        extremes = self.gpx.get_elevation_extremes()
        self.gpx.smooth()
        moving_data = self.gpx.get_moving_data()
        uphill_downhill = self.gpx.get_uphill_downhill()
        self.data = {
            "duration": self.gpx.get_duration(),
            "min_elevation": round(extremes.minimum, 1) if extremes else 0,
            "max_elevation": round(extremes.maximum, 1) if extremes else 0,
            "number_points": self.gpx.get_points_no(),
            "elevation_gain": round(uphill_downhill.uphill, 1) if uphill_downhill else 0,
            "elevation_loss": round(uphill_downhill.downhill, 1) if uphill_downhill else 0,
            "moving_time": moving_data.moving_time,
            "moving_distance": round(moving_data.moving_distance, 2) if moving_data else 0,
            "max_speed": round(moving_data.max_speed, 2) if moving_data else 0,
            "slope_100": round(self.slope_100, 3) if self.slope_100 else 0,
            "vertical_velocities_60s": round(self.vertical_velocities_60s, 3) if self.vertical_velocities_60s else 0,
            "vertical_velocities_600s": round(self.vertical_velocities_600s, 3) if self.vertical_velocities_60s else 0,
            "vertical_velocities_3600s": round(self.vertical_velocities_3600s, 3) if self.vertical_velocities_60s else 0
        }
        self.set_power_data()
        self.set_max_average_velocity_data()

    def set_power_data(self):
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
        if len(self.power_entries) > 0 and len(self.power_entries) == len(self.time_entries):
            duration = (self.time_entries[-1] - self.time_entries[0]).seconds
            df = DataFrame({'power': self.power_entries})
            df.index = self.time_entries

            self.data["power_avg"] = int(max(df.rolling(f"{duration}s", min_periods=max_period).mean().dropna().values))
            for entry in power_per_time_entries:
                if duration > entry.time_interval:
                    values = df.rolling(entry.window, min_periods=entry.min_period).mean().dropna().values
                    if len(values) > 0:
                        self.data[entry.json_key_interval] = int(max(values))

    def set_max_average_velocity_data(self):
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
        if len(self.distance_entries_time) == len(self.distance_entries):
            df = DataFrame({'time': self.distance_entries_time})
            df.index = to_datetime(self.distance_entries, unit="s")

            for entry in entries:
                if max(self.distance_entries) > entry.window_in_m:
                    sums = df.rolling(f"{entry.window_in_m}s").sum().dropna()
                    times = sums.loc[(df.index >= to_datetime(entry.window_in_m, unit="s"))].values
                    if len(times > 0):
                        self.data[entry.json_key_interval] = entry.window_in_km * 3600 / times.min()


def reduce_track_to_relevant_elevation_points(points):
    reduced_points = []
    points_with_doubles = []
    i = 0
    for point in points:
        current_elevation = round(point.elevation)
        if i == 0 or i == len(points) - 1:
            points_with_doubles.append(point)
        else:
            last_elevation = round(points[i - 1].elevation) if (i != 0) else current_elevation
            if current_elevation != last_elevation:
                points_with_doubles.append(point)
        i += 1
    j = 0
    for point in points_with_doubles:
        current_elevation = round(point.elevation)
        last_elevation = round(points_with_doubles[j - 1].elevation) if (j != 0) else current_elevation
        next_elevation = round(points_with_doubles[j + 1].elevation) if (
                j != len(points_with_doubles) - 1) else current_elevation
        if j == 0 or j == len(points_with_doubles) - 1:
            reduced_points.append(point)
        elif current_elevation != last_elevation and current_elevation != next_elevation:
            if (math.copysign(1, current_elevation - last_elevation) !=
                    math.copysign(1, next_elevation - current_elevation)):
                reduced_points.append(point)
        j += 1
    return reduced_points


def remove_elevation_differences_smaller_as(points, minimal_delta):
    filtered_points = []
    elevation_gain = 0.0
    elevation_loss = 0.0
    i = 0
    for point in points:
        if i == 0:
            filtered_points.append(point)
        else:
            delta = point.elevation - filtered_points[-1].elevation
            delta_to_second_last = point.elevation - filtered_points[-2].elevation if len(filtered_points) > 1 else 0
            delta_from_last = filtered_points[-1].elevation - filtered_points[-2].elevation if len(
                filtered_points) > 1 else 0
            if abs(delta) >= minimal_delta:
                filtered_points.append(point)
                if delta > 0:
                    elevation_gain += delta
                else:
                    elevation_loss += delta
            elif abs(delta_to_second_last) > abs(delta_from_last):
                filtered_points.pop(-1)
                filtered_points.append(point)
                if delta > 0:
                    elevation_gain += delta
                else:
                    elevation_loss += delta
        i += 1
    return filtered_points, elevation_gain, elevation_loss


def estimate_coefficients(x_array, y_array):
    n = len(x_array)
    s_x = sum(x_array)
    s_y = sum(y_array)

    # calculating cross-deviation and deviation about x
    ss_xy = sum([x_array[i] * y_array[i] for i in range(0, n)])
    ss_xx = sum([x_array[i] * x_array[i] for i in range(0, n)])
    ss_yy = sum([y_array[i] * y_array[i] for i in range(0, n)])

    # calculating regression coefficients
    b_1 = (n * ss_xy - s_x * s_y) / (n * ss_xx - s_x * s_x)
    b_0 = s_y / n - b_1 * s_x / n
    divisor = math.sqrt((n * ss_xx - s_x * s_x) * (n * ss_yy - s_y * s_y))
    if divisor > 0:
        r = (n * ss_xy - s_x * s_y) / divisor
    else:
        r = 0
    return b_0, b_1, r


def prefix_filename(fn: str) -> str:
    if fn.endswith(".yaml"):
        return fn.replace(".yaml", TrackAnalyzer.SUFFIX + ".yaml")
    else:
        return fn.replace(".gpx", TrackAnalyzer.SUFFIX + ".gpx")


class PowerPerTime(object):
    def __init__(self, time_interval: int, window: str, max_period: int):
        self.time_interval = time_interval
        self.json_key_interval = f"power_{window}"
        self.window = window
        self.min_period = max_period if max_period < time_interval else time_interval


class AverageVelocityPerDistance(object):
    def __init__(self, window_in_km: int):
        self.json_key_interval = f"avg_velocity_{window_in_km}km"
        self.window_in_km = window_in_km
        self.window_in_m = window_in_km * 1000
