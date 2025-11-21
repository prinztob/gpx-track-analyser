import datetime
import json
import os.path
import re
from typing import Any, Tuple

import geopy.distance  # type: ignore[import-untyped]
import gpxpy.gpx
import numpy as np
import yaml
from gpxpy.gpx import GPXTrackPoint, GPX

from src import utils
from src.Extension import Extension
from src.elevation_track_analyzer import ElevationTrackAnalyzer
from src.power_track_analyzer import PowerTrackAnalyzer
from src.utils import prefix_filename, write_extensions_to_yaml
from src.velocity_track_analyzer import VelocityTrackAnalyzer

GPXTrackPoint.extensions_calculated = Extension()  # type: ignore[attr-defined]


class TrackAnalyzer(object):
    NAMESPACE_NAME = "http://www.garmin.com/xmlschemas/TrackPointExtension/v1"
    NAMESPACE = "{" + NAMESPACE_NAME + "}"
    TRACK_EXTENSIONS = "TrackPointExtension"

    def __init__(
            self,
            file: str,
            additional_data_folder: str | None = None,
            split_files: list[str] | None = None,
            yaml_file_folder: str | None = None,
    ) -> None:
        self.file = file
        if not additional_data_folder:
            additional_data_folder = os.path.dirname(file)
        self.yaml_file = os.path.join(
            yaml_file_folder if yaml_file_folder else additional_data_folder,
            os.path.basename(file.replace(".gpx", "_extensions.yaml")),
        )
        self.gpx_file_simplified = os.path.join(
            additional_data_folder, prefix_filename(os.path.basename(file))
        )
        self.gpx_file_gpxpy = os.path.join(
            additional_data_folder,
            os.path.basename(file).replace(".gpx", "_gpxpy.json"),
        )
        with open(file, "r") as f:
            search_result = re.search(r"<\?xml(.|\n)*?(\<\/gpx\>)", f.read())
            if search_result:
                self.gpx_file = search_result.group(0)
        self.data: dict[str, Any] = {}
        self.all_points: list[GPXTrackPoint] = []
        self.all_points_with_extension: list[Tuple[GPXTrackPoint, Extension]] = []
        self.gpx: GPX | None = None
        self.extension_points: list[Extension] = []
        self.duration: float = 0.0
        self.split_files = split_files

    def write_simplified_track_to_file(
            self, gpx_file_simplified: str | None = None
    ) -> None:
        if self.gpx_file:
            if self.gpx is None:
                self.parse_track_and_extension()
        if self.gpx:
            if not gpx_file_simplified:
                gpx_file_simplified = self.gpx_file_simplified
            self.gpx.simplify()
            with open(gpx_file_simplified, "w") as f:
                f.write(self.gpx.to_xml())
            print(f"Written simplified track to {gpx_file_simplified}")

    def write_data_and_extension_to_file(
            self, gpx_file_gpxpy: str | None = None, yaml_file: str | None = None
    ) -> None:
        if not yaml_file:
            yaml_file = self.yaml_file
        if not gpx_file_gpxpy:
            gpx_file_gpxpy = self.gpx_file_gpxpy
        if yaml_file:
            write_extensions_to_yaml(
                self.extension_points,
                yaml_file,
            )
        with open(gpx_file_gpxpy, "w") as fp:
            json.dump(self.data, fp, indent=4)
        print(f"Written data of track to {gpx_file_gpxpy}")

    def analyze(self, track_is_non_monotonic: bool = False) -> bool:
        start_time = datetime.datetime.now()
        self.set_all_points_with_distance(track_is_non_monotonic)
        self.calculate_data_with_gpxpy()
        points_with_time = [e for e in self.all_points_with_extension if e[0].time]
        points_with_time_and_elevation = [e for e in points_with_time if e[0].elevation]
        # try:
        self.data.update(
            ElevationTrackAnalyzer(points_with_time_and_elevation).analyze()
        )
        # except Exception as err:
        #     print(f"ElevationTrackAnalyzer failed with {err}")
        # try:
        self.data.update(PowerTrackAnalyzer(points_with_time).analyze())
        # except Exception as err:
        #     if err.args[0] == "index values must be monotonic":
        #         return False
        #     print(f"PowerTrackAnalyzer failed with {err}")
        # try:
        self.data.update(
            VelocityTrackAnalyzer(points_with_time, self.split_files).analyze()
        )
        # except Exception as err:
        #     if err.args[0] == "index values must be monotonic":
        #         return False
        #     print(f"VelocityTrackAnalyzer failed with {err}")
        self.duration = (datetime.datetime.now() - start_time).total_seconds()
        return True

    def calculate_data_with_gpxpy(self) -> None:
        if self.gpx:
            extremes = self.gpx.get_elevation_extremes()
            self.gpx.smooth()
            moving_data = self.gpx.get_moving_data()
            uphill_downhill = self.gpx.get_uphill_downhill()
            self.data.update(
                {
                    "duration": self.gpx.get_duration(),
                    "min_elevation": round(extremes.minimum, 1)
                    if extremes and extremes.minimum
                    else 0,
                    "max_elevation": round(extremes.maximum, 1)
                    if extremes and extremes.maximum
                    else 0,
                    "number_points": self.gpx.get_points_no(),
                    "elevation_gain": round(uphill_downhill.uphill, 1)
                    if uphill_downhill
                    else 0,
                    "elevation_loss": round(uphill_downhill.downhill, 1)
                    if uphill_downhill
                    else 0,
                    "moving_time": moving_data.moving_time,
                    "moving_distance": round(moving_data.moving_distance, 2)
                    if moving_data
                    else 0,
                    "max_speed": round(moving_data.max_speed, 2) if moving_data else 0,
                }
            )

    def parse_track_and_extension(self) -> None:
        with open(self.file, "r") as f:
            search_result = re.search(r"<\?xml(.|\n)*?(\<\/gpx\>)", f.read())
            if search_result:
                self.gpx = gpxpy.parse(search_result.group(0))
            else:
                self.gpx = gpxpy.parse(f)
        if os.path.exists(self.yaml_file):
            extensions = yaml.safe_load(open(self.yaml_file, "r"))
            self.extension_points = [
                Extension.parse_from_yaml(e) for e in extensions["extensions"]
            ]
        else:
            self.extension_points = [Extension.parse(p.extensions) for p in utils.get_points(self.gpx)]
        number_track_points = utils.get_number_of_track_points(self.gpx)
        if number_track_points != len(self.extension_points):
            print(
                f"# track pints {number_track_points} do not match extension point number "
                f"{len(self.extension_points)} -> set all extensions to empty Extension"
            )
            self.extension_points = [Extension() for _ in range(number_track_points)]

    def set_all_points_with_distance(self, track_is_non_monotonic: bool) -> None:
        print(f"Read and add distance to track file {self.file}")
        if self.gpx_file:
            if self.gpx is None:
                self.parse_track_and_extension()
            distance = 0.0
            if not self.track_points_monotonic():
                self.recalculate_distances(distance)

    def recalculate_distances(self, distance: float) -> None:
        print("Distances are not set or not monotonic -> recalculate distance")
        for i, p in enumerate(self.all_points):
            distance += geopy.distance.distance(
                (self.all_points[i - 1].latitude, self.all_points[i - 1].longitude),
                (p.latitude, p.longitude),
            ).km
            self.extension_points[i].distance = distance

    def track_points_monotonic(self) -> bool:
        distances = (
            [p.distance for p in self.extension_points] if self.extension_points else []
        )
        all_points = (
            [p for t in self.gpx.tracks for s in t.segments for p in s.points]
            if self.gpx
            else []
        )
        dx = np.diff(distances)
        monotonic = len(set(distances)) > 1 and (
                bool(np.all(dx <= 0)) or bool(np.all(dx >= 0))
        )
        self.all_points = all_points
        self.all_points_with_extension = list(zip(all_points, self.extension_points))
        return monotonic
