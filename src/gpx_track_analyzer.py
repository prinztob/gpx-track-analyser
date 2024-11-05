import datetime
import json
import os.path
import re
from typing import List

import geopy.distance
import gpxpy.gpx
from gpxpy.gpx import GPXTrackPoint

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

    def __init__(self, file, additional_data_folder=None):
        self.file = file
        if not additional_data_folder:
            additional_data_folder = os.path.dirname(file)
        self.yaml_file = os.path.join(additional_data_folder, os.path.basename(file.replace(".gpx", "_extensions.yaml")))
        self.gpx_file_simplified = os.path.join(additional_data_folder, prefix_filename(os.path.basename(file)))
        self.gpx_file_gpxpy = os.path.join(additional_data_folder, os.path.basename(file).replace(".gpx", "_gpxpy.json"))
        with open(file, 'r') as f:
            search_result = re.search(r'<\?xml(.|\n)*?(\<\/gpx\>)', f.read())
            if search_result:
                self.gpx_file = search_result.group(0)
        self.data = {}
        self.all_points: List[GPXTrackPoint] = []
        self.gpx = None
        self.distance_entries = []
        self.duration = 0

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


    def analyze(self):
        start_time = datetime.datetime.now()
        self.set_all_points_with_distance()
        self.calculate_data_with_gpxpy()
        points = [e for e in self.all_points if e.time]
        self.data.update(ElevationTrackAnalyzer(points).analyze())
        self.data.update(PowerTrackAnalyzer(points).analyze())
        self.data.update(VelocityTrackAnalyzer(points).analyze())
        self.duration = (datetime.datetime.now() - start_time).total_seconds()

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

    def set_all_points_with_distance(self):
        print(f"Read and add distance to track file {self.file}")
        if self.gpx_file:
            if self.gpx is None:
                self.parse_track()
            distance = 0.0
            for track in self.gpx.tracks:
                for segment in track.segments:
                    points = []
                    delta = 0.0
                    for i, point in enumerate(segment.points):
                        point.extensions_calculated = Extension.parse(point.extensions)
                        if point.latitude != 0 and point.longitude != 0:
                            if (i == 0
                                    and len(self.all_points) > 0
                                    and point.extensions_calculated.distance == 0
                                    and point.extensions_calculated.distance < self.all_points[-1].extensions_calculated.distance):
                                delta = self.all_points[-1].extensions_calculated.distance
                            if point.extensions_calculated.distance == 0.0:
                                if i != 0:
                                    distance += geopy.distance.distance(
                                        (points[-1].latitude, points[-1].longitude),
                                        (point.latitude, point.longitude)
                                    ).km
                                point.extensions_calculated.distance = round(distance * 1000 + delta, 1)
                            elif delta > 0:
                                point.extensions_calculated.distance += delta
                            self.all_points.append(point)
                            points.append(point)
                    segment.points = points
