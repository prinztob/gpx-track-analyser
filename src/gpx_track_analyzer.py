import datetime
import json
import logging
import math

import geopy.distance
import gpxpy.gpx
import lxml.etree as mod_etree

logging.basicConfig(format="%(asctime)s %(levelname)8s %(pathname)s: %(message)s", level=logging.INFO,
                    datefmt="%y-%m-%dT%H:%M:%S")
_LOGGER = logging.getLogger(__name__)


class TrackAnalyzer(object):
    NAMESPACE_NAME = 'http://www.garmin.com/xmlschemas/TrackPointExtension/v1'
    NAMESPACE = '{' + NAMESPACE_NAME + '}'
    TRACK_EXTENSIONS = 'TrackPointExtension'
    SUFFIX = "_simplified"

    def __init__(self, file, update_track_with_calculated_values=False):
        self.file = file
        self.data = {}
        self.all_points = []
        self.points_with_time = []
        self.gpx = None
        self.slopes = []
        self.vertical_velocities = {}
        self.slope_100 = 0
        self.vertical_velocities_60s = 0
        self.vertical_velocities_600s = 0
        self.vertical_velocities_3600s = 0
        self.duration = 0
        self.update_track_with_calculated_values = update_track_with_calculated_values

    def write_file(self, file=None):
        if not file:
            file = self.file
        if self.update_track_with_calculated_values:
            with open(file, "w") as f:
                f.write(self.gpx.to_xml())
        gpx_file_simplified = prefix_filename(file)
        gpx_file_gpxpy = file.replace(".gpx", "_gpxpy.json")
        self.set_gpx_data()
        with open(gpx_file_gpxpy, 'w') as fp:
            json.dump(self.data, fp, indent=4)
        self.gpx.simplify()
        with open(gpx_file_simplified, 'w') as f:
            f.write(self.gpx.to_xml())

    def analyze(self):
        start_time = datetime.datetime.now()
        self.set_all_points_with_distance()
        current_date_time = datetime.datetime.now()
        self.set_vertical_velocity(60, True)
        self.set_vertical_velocity(600)
        self.set_vertical_velocity(3600)
        print(f"Time {(datetime.datetime.now() - current_date_time).total_seconds()}")
        self.set_slope(100)
        self.duration = (datetime.datetime.now() - start_time).total_seconds()
        _LOGGER.info(f"Took {self.duration}")

    def get_maximal_values(self):
        self.slope_100 = max(self.slopes)
        self.vertical_velocities_60s = max(self.vertical_velocities["60"]) if len(
            self.vertical_velocities["60"]) > 0 else 0
        self.vertical_velocities_600s = max(self.vertical_velocities["600"]) if len(
            self.vertical_velocities["600"]) > 0 else 0
        self.vertical_velocities_3600s = max(self.vertical_velocities["3600"]) if len(
            self.vertical_velocities["3600"]) > 0 else 0

    def set_all_points_with_distance(self):
        _LOGGER.info(f"Read and add distance to track file {self.file}")
        gpx_file = open(self.file, 'r')
        self.gpx = gpxpy.parse(gpx_file)
        distance = 0.0
        for track in self.gpx.tracks:
            for segment in track.segments:
                last_point = None
                points = []
                for point in segment.points:
                    if point.latitude != 0 and point.longitude != 0:
                        if last_point:
                            distance += geopy.distance.distance((last_point.latitude, last_point.longitude),
                                                                (point.latitude, point.longitude)).km
                        self.set_tag_in_extensions(distance * 1000, point, "distance")
                        point.distance = distance * 1000
                        last_point = point
                        self.all_points.append(point)
                        if point.time:
                            self.points_with_time.append(point)
                        points.append(point)
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
            if not self.all_points[i].distance is None and self.all_points[i].distance >= 0.0 and self.all_points[
                i].elevation:
                track_points_for_interval.append(self.all_points[i])
                sum_meters = track_points_for_interval[-1].distance - track_points_for_interval[0].distance
                if sum_meters > max_meter_interval / 2 and not middle_entry:
                    middle_entry = self.all_points[i]
            i += 1

    def set_vertical_velocity(self, max_time_interval, update_points=False):
        self.vertical_velocities[str(max_time_interval)] = []
        if len(self.points_with_time) > 0:
            vertical_velocity = 0
            i = 0
            while i < len(self.points_with_time):
                j = i + 10
                if j >= len(self.points_with_time) - 1:
                    break
                diff_times = 0.0
                while diff_times < max_time_interval:
                    if j >= len(self.points_with_time) - 1:
                        break
                    diff_times = (self.points_with_time[j].time - self.points_with_time[i].time).total_seconds()
                    j += 1
                track_points_for_interval = self.points_with_time[i:j]
                reduced_track_points_for_interval = reduce_track_to_relevant_elevation_points(track_points_for_interval)
                relevant_track_points_for_interval, gain, loss = remove_elevation_differences_smaller_as(
                    reduced_track_points_for_interval, 10)
                current_velocity = 0.0 if diff_times == 0.0 else (gain / diff_times)
                if current_velocity > vertical_velocity:
                    vertical_velocity = current_velocity
                    i += 1
                else:
                    i += 25
                if update_points and i < len(self.all_points):
                    self.set_tag_in_extensions(vertical_velocity * max_time_interval, self.all_points[i],
                                               "vvelocity")

                self.vertical_velocities[str(max_time_interval)].append(vertical_velocity)

    def set_gpx_data(self):
        extremes = self.gpx.get_elevation_extremes()
        self.gpx.smooth()
        moving_data = self.gpx.get_moving_data()
        uphill_downhill = self.gpx.get_uphill_downhill()
        self.data = {
            "duration": self.gpx.get_duration(),
            "min_elevation": round(extremes.minimum, 1),
            "max_elevation": round(extremes.maximum, 1),
            "number_points": self.gpx.get_points_no(),
            "elevation_gain": round(uphill_downhill.uphill, 1),
            "elevation_loss": round(uphill_downhill.downhill, 1),
            "moving_time": moving_data.moving_time,
            "moving_distance": round(moving_data.moving_distance, 2),
            "max_speed": round(moving_data.max_speed, 2),
            "slope_100": round(self.slope_100, 3),
            "vertical_velocities_60s": round(self.vertical_velocities_60s, 3),
            "vertical_velocities_600s": round(self.vertical_velocities_600s, 3),
            "vertical_velocities_3600s": round(self.vertical_velocities_3600s, 3)
        }


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
            if math.copysign(1, current_elevation - last_elevation) != math.copysign(1,
                                                                                     next_elevation - current_elevation):
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
    return fn.replace(".gpx", TrackAnalyzer.SUFFIX + ".gpx")
