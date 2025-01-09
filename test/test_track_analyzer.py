import tempfile

import gpxpy
import yaml

from src.Extension import Extension
from src.gpx_track_analyzer import TrackAnalyzer
from src.utils import prefix_filename, reduce_track_to_relevant_elevation_points, \
    remove_elevation_differences_smaller_as

def test_simplifying_track_gpx():
    file = "../resources/track.gpx"
    output_file = tempfile.NamedTemporaryFile(suffix=".gpx")
    gpx_file_simplified = prefix_filename(output_file.name)
    analyzer = TrackAnalyzer(file)
    analyzer.write_simplified_track_to_file(gpx_file_simplified)
    gpx = gpxpy.parse(open(gpx_file_simplified, "r"))
    assert len(get_points(gpx)) == 106

def test_analyzing_track_gpx():
    file = "../resources/track.gpx"

    analyzer = TrackAnalyzer(file)
    gpx = gpxpy.parse(open(file, 'r'))
    assert len(gpx.tracks[0].segments[0].points[0].extensions) == 0
    analyzer.analyze()
    output_file = tempfile.NamedTemporaryFile(suffix=".gpx")
    output_file_yaml = tempfile.NamedTemporaryFile(suffix=".yaml")
    gpx_file_gpxpy = output_file.name.replace(".gpx", "_gpxpy.json")
    analyzer.write_data_and_extension_to_file(gpx_file_gpxpy, output_file_yaml.name)
    extensions = yaml.load(open(output_file_yaml.name, "r"))
    extension_points = [Extension.parse_from_yaml(e) for e in extensions["extensions"]]
    assert len(get_points(gpx)) == 5683
    assert extension_points[66].distance > 0.0
    assert extension_points[66].verticalVelocity > 0.0
    assert extension_points[66].slope > 0.0

    gpx_file_gpxpy_content = """{
    "duration": 7503.0,
    "min_elevation": 619.6,
    "max_elevation": 1211.4,
    "number_points": 5683,
    "elevation_gain": 637.9,
    "elevation_loss": 639.5,
    "moving_time": 4701.0,
    "moving_distance": 10292.59,
    "max_speed": 3.3,
    "vertical_velocity_60s_+": 0.256,
    "vertical_velocity_600s_+": 0.191,
    "vertical_velocity_3600s_+": 0.16,
    "vertical_velocity_60s_-": 1.313,
    "vertical_velocity_600s_-": 0.817,
    "vertical_velocity_3600s_-": 0.164,
    "slope_100": 32.68,
    "avg_velocity_1km": 23.076923076923077,
    "avg_velocity_5km": 5.054759898904802,
    "avg_velocity_10km": 4.8826800488268
}"""
    assert open(gpx_file_gpxpy, "r").read() == gpx_file_gpxpy_content


def test_analyzing_track2_gpx():
    file = "../resources/track2.gpx"

    analyzer = TrackAnalyzer(file)
    analyzer.analyze()
    assert abs(analyzer.data["slope_100"] - 13.32) < 0.01
    assert abs(analyzer.data["vertical_velocity_60s_+"] - 0.3) < 0.01
    assert abs(analyzer.data["vertical_velocity_600s_+"] - 0.25) < 0.01
    assert abs(analyzer.data["vertical_velocity_3600s_+"] - 0.185) < 0.01
    assert analyzer.duration < 2


def test_analyzing_track6_gpx():
    file = "../resources/track6.gpx"

    analyzer = TrackAnalyzer(file)
    analyzer.analyze()
    assert abs(analyzer.data["slope_100"] - 17.16) < 0.01
    assert abs(analyzer.data["vertical_velocity_60s_+"] - 0.31) < 0.01
    assert abs(analyzer.data["vertical_velocity_600s_+"] - 0.234) < 0.01
    assert abs(analyzer.data["vertical_velocity_3600s_+"] - 0.186) < 0.01
    assert analyzer.duration < 3

def test_analyzing_track7_gpx():
    file = "../resources/track7.gpx"

    analyzer = TrackAnalyzer(file)
    if not analyzer.analyze():
        analyzer = TrackAnalyzer(file)
        analyzer.analyze(True)
    assert abs(analyzer.data["slope_100"] - 17.88) < 0.01
    assert abs(analyzer.data["vertical_velocity_60s_+"] - 0.4) < 0.01
    assert abs(analyzer.data["avg_velocity_10km"] - 26.99) < 0.01
    assert analyzer.data["power_2h"] == 192
    assert analyzer.duration < 3

def test_analyzing_track3_gpx():
    file = "../resources/track3.gpx"

    analyzer = TrackAnalyzer(file)
    analyzer.analyze()
    assert abs(analyzer.data["slope_100"] - 25.0) < 0.01
    assert abs(analyzer.data["vertical_velocity_60s_+"] - 0.32) < 0.01
    assert abs(analyzer.data["vertical_velocity_600s_+"] - 0.22) < 0.01
    assert abs(analyzer.data["vertical_velocity_3600s_+"] - 0.17) < 0.01
    assert abs(analyzer.data["vertical_velocity_60s_-"] - 1.82) < 0.01
    assert abs(analyzer.data["vertical_velocity_600s_-"] - 0.60) < 0.01
    assert abs(analyzer.data["vertical_velocity_3600s_-"] - 0.26) < 0.01
    assert analyzer.data["power_avg"] == 118
    assert analyzer.data["power_10s"] == 493
    assert analyzer.data["power_30s"] == 421
    assert analyzer.data["power_1min"] == 373
    assert analyzer.data["power_5min"] == 256
    assert analyzer.data["power_10min"] == 248
    assert analyzer.data["power_20min"] == 243
    assert analyzer.data["power_30min"] == 235
    assert analyzer.data["power_1h"] == 197
    assert analyzer.all_points[55].extensions_calculated.power60s == 206
    assert analyzer.all_points[55].extensions_calculated.power == 236
    assert analyzer.all_points[55].extensions_calculated.hr == 112
    assert analyzer.all_points[-1].extensions_calculated.distance == 64797.8203125
    print(analyzer.duration)
    assert analyzer.duration < 10


def test_analyzing_track4_gpx():
    file = "../resources/track4.gpx"

    analyzer = TrackAnalyzer(file)
    analyzer.analyze()
    assert abs(analyzer.data["slope_100"] - 18.652) < 0.011
    assert abs(analyzer.data["vertical_velocity_60s_+"] - 0.189) < 0.012
    assert abs(analyzer.data["vertical_velocity_600s_+"] - 0.05) < 0.013
    assert "vertical_velocity_3600s_+" not in analyzer.data


def test_analyzing_track5_gpx():
    file = "../resources/track5.gpx"

    analyzer = TrackAnalyzer(file)
    analyzer.set_all_points_with_distance(False)
    analyzer.calculate_data_with_gpxpy()
    assert analyzer.data["moving_distance"] == 30765.68


def test_reduce_track_to_relevant_elevation_points():
    file = "../resources/track3.gpx"
    gpx_file = open(file, 'r')
    gpx = gpxpy.parse(gpx_file)
    reduced_track_points_for_interval = reduce_track_to_relevant_elevation_points(
        gpx.tracks[0].segments[0].points[0: 1000])
    assert len(reduced_track_points_for_interval) == 18
    relevant_track_points_for_interval, gain, loss = remove_elevation_differences_smaller_as(
        reduced_track_points_for_interval, 10)
    assert len(relevant_track_points_for_interval) == 7
    assert abs(gain - 99.2) < 0.1
    assert abs(loss + 98.2) < 0.1


def get_points(gpx):
    points = []
    for track in gpx.tracks:
        for segment in track.segments:
            points = points + segment.points
    return points
