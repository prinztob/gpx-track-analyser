import datetime
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

    def __init__(self, file):
        self.file = file
        self.all_points = []
        self.gpx = None
        self.slopes = []
        self.vertical_velocities = {}
        self.slope_100 = 0
        self.vertical_velocities_60s = 0
        self.vertical_velocities_600s = 0
        self.vertical_velocities_3600s = 0
        self.duration = 0

    def update_file(self, file=None):
        if not file:
            file = self.file
        with open(file, "w") as f:
            f.write(self.gpx.to_xml())

    def analyze(self):
        start_time = datetime.datetime.now()
        self.set_all_points_with_distance()
        self.set_vertical_velocity(60, update_points=True)
        self.set_vertical_velocity(600)
        self.set_vertical_velocity(3600)
        self.set_slope(100)
        self.duration = (datetime.datetime.now() - start_time).total_seconds()
        _LOGGER.info(f"Took {self.duration}")

    def get_maximal_values(self):
        self.slope_100 = max(self.slopes)
        self.vertical_velocities_60s = max(self.vertical_velocities["60"])
        self.vertical_velocities_600s = max(self.vertical_velocities["600"])
        self.vertical_velocities_3600s = max(self.vertical_velocities["3600"])

    def set_all_points_with_distance(self):
        _LOGGER.info(f"Read and add distance to track file {self.file}")
        gpx_file = open(self.file, 'r')
        self.gpx = gpxpy.parse(gpx_file)
        distance = 0.0
        for track in self.gpx.tracks:
            for segment in track.segments:
                last_point = None
                for point in segment.points:
                    if last_point:
                        distance += geopy.distance.distance((last_point.latitude, last_point.longitude),
                                                            (point.latitude, point.longitude)).km
                    self.set_tag_in_extensions(distance * 1000, point, "distance")
                    point.distance = distance * 1000
                    last_point = point
                    self.all_points.append(point)

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
                        slope = linear_regression[1] if linear_regression[2] > 0.9 else 0.0
                    else:
                        slope = 0
                else:
                    elevation = track_points_for_interval[-1].elevation - track_points_for_interval[0].elevation
                    slope = 0.0 if sum_meters == 0.0 else (elevation / sum_meters)
                self.slopes.append(slope)
                track_points_for_interval = track_points_for_interval[1: -1]
                middle_entry = track_points_for_interval[0]
            else:
                slope = 0
            self.set_tag_in_extensions(slope * 100, self.all_points[i], "slope")
            if not self.all_points[i].distance is None and self.all_points[i].distance >= 0.0 and self.all_points[i].elevation:
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
            if middle_entry and diff_times >= max_time_interval and len(track_points_for_interval) > 2:
                reduced_track_points_for_interval = reduce_track_to_relevant_elevation_points(track_points_for_interval)
                relevant_track_points_for_interval, gain, loss = remove_elevation_differences_smaller_as(
                    reduced_track_points_for_interval, 10)
                if use_regression:
                    x_array = [(entry.time - self.all_points[0].time).total_seconds() for entry in track_points_for_interval]
                    y_array = [entry.elevation for entry in track_points_for_interval]
                    if len(set(y_array)) > 1:
                        linear_regression = estimate_coefficients(x_array, y_array)
                        vertical_velocity = linear_regression[1] if linear_regression[2] > 0.9 else 0.0
                    else:
                        vertical_velocity = 0
                else:
                    vertical_velocity = 0.0 if diff_times == 0.0 else (gain / diff_times)

                self.vertical_velocities[str(max_time_interval)].append(vertical_velocity)
                track_points_for_interval = track_points_for_interval[1: -1]
                middle_entry = track_points_for_interval[0]
            else:
                vertical_velocity = 0
            if update_points:
                self.set_tag_in_extensions(vertical_velocity * max_time_interval, self.all_points[i], "vvelocity")
            if not self.all_points[i].time is None and self.all_points[i].elevation:
                track_points_for_interval.append(self.all_points[i])
                diff_times = (track_points_for_interval[-1].time - track_points_for_interval[0].time).total_seconds()
                if diff_times > max_time_interval / 2 and not middle_entry:
                    middle_entry = self.all_points[i]
            i += 1


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
        next_elevation = round(points_with_doubles[j + 1].elevation) if (j != len(points_with_doubles) - 1) else current_elevation
        if j == 0 or j == len(points_with_doubles) - 1:
            reduced_points.append(point)
        elif current_elevation != last_elevation and current_elevation != next_elevation:
            if math.copysign(1, current_elevation - last_elevation) != math.copysign(1, next_elevation - current_elevation):
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
            if abs(delta) >= minimal_delta:
                filtered_points.append(point)
                if delta > 0:
                    elevation_gain += delta
                else:
                    elevation_loss += delta
        i += 1
    return filtered_points, elevation_gain, elevation_loss


def estimate_coefficients(x_array, y_array):
    n = len(x_array)
    S_x = sum(x_array)
    S_y = sum(y_array)

    # calculating cross-deviation and deviation about x
    SS_xy = sum([x_array[i] * y_array[i] for i in range(0, n)])
    SS_xx = sum([x_array[i] * x_array[i] for i in range(0, n)])
    SS_yy = sum([y_array[i] * y_array[i] for i in range(0, n)])

    # calculating regression coefficients
    b_1 = (n * SS_xy - S_x * S_y) / (n * SS_xx - S_x * S_x)
    b_0 = S_y / n - b_1 * S_x / n
    divisor = math.sqrt((n * SS_xx - S_x * S_x) * (n * SS_yy - S_y * S_y))
    if divisor > 0:
        r = (n * SS_xy - S_x * S_y) / divisor
    else:
        r = 0
    return (b_0, b_1, r)

# with open("/tmp/output.gpx", "w") as f:
#    f.write(gpx.to_xml())
