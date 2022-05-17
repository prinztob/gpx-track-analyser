import tempfile

import gpxpy

from src.track_analyzer import TrackAnalyzer, reduce_track_to_relevant_elevation_points, remove_elevation_differences_smaller_as, \
    prefix_filename


def test_analyzing_track_gpx():
    file = "resources/track.gpx"

    analyzer = TrackAnalyzer(file, True)
    gpx = gpxpy.parse(open(file, 'r'))
    assert len(gpx.tracks[0].segments[0].points[0].extensions) == 0
    analyzer.analyze()
    analyzer.get_maximal_values()
    assert abs(analyzer.slope_100 - 36.139) < 0.1
    assert abs(analyzer.vertical_velocities_60s - 0.25) < 0.01
    assert abs(analyzer.vertical_velocities_600s - 0.20) < 0.01
    assert abs(analyzer.vertical_velocities_3600s - 0.15) < 0.01

    output_file = tempfile.NamedTemporaryFile(suffix=".gpx")
    gpx_file_simplified = prefix_filename(output_file.name)
    gpx_file_gpxpy = output_file.name.replace(".gpx", "_gpxpy.json")
    analyzer.write_file(output_file.name)
    gpx = gpxpy.parse(open(output_file.name, "r"))
    assert len(gpx.tracks[0].segments[0].points[0].extensions[0]) == 3
    assert len(get_points(gpx)) == 5683

    gpx = gpxpy.parse(open(gpx_file_simplified, "r"))
    assert len(get_points(gpx)) == 106

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
    "slope_100": 36.139,
    "vertical_velocities_60s": 0.257,
    "vertical_velocities_600s": 0.191,
    "vertical_velocities_3600s": 0.16
}"""
    assert open(gpx_file_gpxpy, "r").read() == gpx_file_gpxpy_content


def test_analyzing_track2_gpx():
    file = "resources/track2.gpx"

    analyzer = TrackAnalyzer(file)
    analyzer.analyze()
    analyzer.get_maximal_values()
    assert abs(analyzer.slope_100 - 12.913) < 0.01
    assert abs(analyzer.vertical_velocities_60s - 0.29) < 0.01
    assert abs(analyzer.vertical_velocities_600s - 0.25) < 0.01
    assert abs(analyzer.vertical_velocities_3600s - 0.185) < 0.01
    assert analyzer.duration < 5


def test_analyzing_track3_gpx():
    file = "resources/track3.gpx"

    analyzer = TrackAnalyzer(file)
    analyzer.analyze()
    analyzer.get_maximal_values()
    assert abs(analyzer.slope_100 - 27.945) < 0.01
    assert abs(analyzer.vertical_velocities_60s - 0.30) < 0.01
    assert abs(analyzer.vertical_velocities_600s - 0.22) < 0.01
    assert abs(analyzer.vertical_velocities_3600s - 0.17) < 0.01
    assert analyzer.duration < 30


def test_analyzing_track4_gpx():
    file = "resources/track4.gpx"

    analyzer = TrackAnalyzer(file)
    analyzer.analyze()
    analyzer.get_maximal_values()
    assert abs(analyzer.slope_100 - 16.3565) < 0.01
    assert abs(analyzer.vertical_velocities_60s - 0.20) < 0.01
    assert abs(analyzer.vertical_velocities_600s - 0.04) < 0.01
    assert abs(analyzer.vertical_velocities_3600s - 0) < 0.01


def test_analyzing_track5_gpx():
    file = "resources/track5.gpx"

    analyzer = TrackAnalyzer(file)
    analyzer.set_all_points_with_distance()

    analyzer.set_gpx_data()
    assert analyzer.data["moving_distance"] == 30825.58


def test_reduce_track_to_relevant_elevation_points():
    file = "resources/track3.gpx"
    gpx_file = open(file, 'r')
    gpx = gpxpy.parse(gpx_file)
    reduced_track_points_for_interval = reduce_track_to_relevant_elevation_points(gpx.tracks[0].segments[0].points[0: 1000])
    assert len(reduced_track_points_for_interval) == 18
    relevant_track_points_for_interval, gain, loss = remove_elevation_differences_smaller_as(
        reduced_track_points_for_interval, 10)
    assert len(relevant_track_points_for_interval) == 7
    assert abs(gain - 98.2) < 0.1
    assert abs(loss + 97.2) < 0.1


def get_points(gpx):
    points = []
    for track in gpx.tracks:
        for segment in track.segments:
            points = points + segment.points
    return points
