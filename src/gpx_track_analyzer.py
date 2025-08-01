import datetime
import json
import os.path
import re
from typing import List

import geopy.distance
import gpxpy.gpx
import numpy as np
from gpxpy.gpx import GPXTrackPoint
from numpy import bool_

from src.Extension import Extension
from src.elevation_track_analyzer import ElevationTrackAnalyzer
from src.power_track_analyzer import PowerTrackAnalyzer
from src.utils import prefix_filename, write_extensions_to_yaml
from src.velocity_track_analyzer import VelocityTrackAnalyzer

GPXTrackPoint.extensions_calculated = Extension()


class TrackAnalyzer(object):
    NAMESPACE_NAME = 'http://www.garmin.com/xmlschemas/TrackPointExtension/v1'
    NAMESPACE = '{' + NAMESPACE_NAME + '}'
    TRACK_EXTENSIONS = 'TrackPointExtension'

    def __init__(self, file, additional_data_folder=None, split_files: List[str] = None):
        self.file = file
        if not additional_data_folder:
            additional_data_folder = os.path.dirname(file)
        self.yaml_file = os.path.join(additional_data_folder,
                                      os.path.basename(file.replace(".gpx", "_extensions.yaml")))
        self.gpx_file_simplified = os.path.join(additional_data_folder, prefix_filename(os.path.basename(file)))
        self.gpx_file_gpxpy = os.path.join(additional_data_folder,
                                           os.path.basename(file).replace(".gpx", "_gpxpy.json"))
        with open(file, 'r') as f:
            search_result = re.search(r'<\?xml(.|\n)*?(\<\/gpx\>)', f.read())
            if search_result:
                self.gpx_file = search_result.group(0)
        self.data = {}
        self.all_points: List[GPXTrackPoint] = []
        self.gpx = None
        self.duration = 0
        self.split_files = split_files

    def write_simplified_track_to_file(self, gpx_file_simplified=None):
        if self.gpx_file:
            if self.gpx is None:
                self.parse_track()
        if not gpx_file_simplified:
            gpx_file_simplified = self.gpx_file_simplified
        self.gpx.simplify()
        with open(gpx_file_simplified, 'w') as f:
            f.write(self.gpx.to_xml())
        print(f"Written simplified track to {gpx_file_simplified}")

    def write_data_and_extension_to_file(self, gpx_file_gpxpy=None, yaml_file=None):
        if not yaml_file:
            yaml_file = self.yaml_file
        if not gpx_file_gpxpy:
            gpx_file_gpxpy = self.gpx_file_gpxpy
        if yaml_file:
            write_extensions_to_yaml([e.extensions_calculated for e in self.all_points], yaml_file)
        with open(gpx_file_gpxpy, 'w') as fp:
            json.dump(self.data, fp, indent=4)
        print(f"Written data of track to {gpx_file_gpxpy}")

    def analyze(self, track_is_non_monotonic=False) -> bool:
        start_time = datetime.datetime.now()
        self.set_all_points_with_distance(track_is_non_monotonic)
        self.calculate_data_with_gpxpy()
        points = [e for e in self.all_points if e.time]
        self.data.update(ElevationTrackAnalyzer([point for point in points if point.elevation]).analyze())
        try:
            self.data.update(PowerTrackAnalyzer(points).analyze())
        except Exception as err:
            if err.args[0] == 'index values must be monotonic':
                return False
            print(f"PowerTrackAnalyzer failed with {err}")
        try:
            self.data.update(VelocityTrackAnalyzer(points, self.split_files).analyze())
        except Exception as err:
            if err.args[0] == 'index values must be monotonic':
                return False
            print(f"VelocityTrackAnalyzer failed with {err}")
        self.duration = (datetime.datetime.now() - start_time).total_seconds()
        return True

    def calculate_data_with_gpxpy(self):
        extremes = self.gpx.get_elevation_extremes()
        self.gpx.smooth()
        moving_data = self.gpx.get_moving_data()
        uphill_downhill = self.gpx.get_uphill_downhill()
        self.data.update({
            "duration": self.gpx.get_duration(),
            "min_elevation": round(extremes.minimum, 1) if extremes else 0,
            "max_elevation": round(extremes.maximum, 1) if extremes else 0,
            "number_points": self.gpx.get_points_no(),
            "elevation_gain": round(uphill_downhill.uphill, 1) if uphill_downhill else 0,
            "elevation_loss": round(uphill_downhill.downhill, 1) if uphill_downhill else 0,
            "moving_time": moving_data.moving_time,
            "moving_distance": round(moving_data.moving_distance, 2) if moving_data else 0,
            "max_speed": round(moving_data.max_speed, 2) if moving_data else 0,
        })

    def parse_track(self):
        with open(self.file, 'r') as f:
            search_result = re.search(r'<\?xml(.|\n)*?(\<\/gpx\>)', f.read())
            if search_result:
                self.gpx = gpxpy.parse(search_result.group(0))
            else:
                self.gpx = gpxpy.parse(f)

    def set_all_points_with_distance(self, track_is_non_monotonic):
        print(f"Read and add distance to track file {self.file}")
        if self.gpx_file:
            if self.gpx is None:
                self.parse_track()
            distance = 0.0
            if not self.track_points_monotonic():
                self.recalculate_distances(distance, track_is_non_monotonic)

    def recalculate_distances(self, distance, track_is_non_monotonic):
        print("Distances are not set or not monotonic -> recalculate distance")
        for track in self.gpx.tracks:
            for segment in track.segments:
                points = []
                delta = 0.0
                for i, point in enumerate(segment.points):
                    point.extensions_calculated = Extension.parse(point.extensions)
                    point_distance = point.extensions_calculated.distance
                    if point.latitude != 0 and point.longitude != 0:
                        if (i == 0
                                and len(self.all_points) > 0
                                and point_distance == 0
                                and point_distance < self.all_points[-1].extensions_calculated.distance
                        ):
                            delta = self.all_points[-1].extensions_calculated.distance
                        if point_distance == 0.0:
                            if i != 0:
                                distance += geopy.distance.distance(
                                    (points[-1].latitude, points[-1].longitude),
                                    (point.latitude, point.longitude)
                                ).km
                            point.extensions_calculated.distance = distance * 1000 + delta
                        elif delta > 0:
                            point.extensions_calculated.distance += delta
                        if track_is_non_monotonic:
                            if i != 0 and point_distance < segment.points[i - 1].extensions_calculated.distance:
                                point.extensions_calculated.distance = segment.points[
                                    i - 1].extensions_calculated.distance

                        self.all_points.append(point)
                        points.append(point)
                segment.points = points

    def track_points_monotonic(self) -> bool_:
        distances = []
        all_points = []
        for track in self.gpx.tracks:
            for segment in track.segments:
                segment_points = []
                for i, point in enumerate(segment.points):
                    point.extensions_calculated = Extension.parse(point.extensions)
                    if point.latitude != 0 and point.longitude != 0:
                        distances.append(point.extensions_calculated.distance)
                        segment_points.append(point)
                segment.points = segment_points
                all_points.extend(segment_points)
        dx = np.diff(distances)
        monotonic = len(set(distances)) > 1 and (np.all(dx <= 0) or np.all(dx >= 0))
        if monotonic:
            self.all_points = all_points
        return monotonic
