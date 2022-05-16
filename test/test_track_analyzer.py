import tempfile

import gpxpy

from src.track_analyzer import TrackAnalyzer, reduce_track_to_relevant_elevation_points, remove_elevation_differences_smaller_as


def test_analyzing_track_gpx():
    file = "resources/track.gpx"

    analyzer = TrackAnalyzer(file)
    gpx = gpxpy.parse(open(file, 'r'))
    assert len(gpx.tracks[0].segments[0].points[0].extensions) == 0
    analyzer.analyze()
    analyzer.get_maximal_values()
    assert abs(analyzer.slope_100 - 0.361) < 0.01
    assert abs(analyzer.vertical_velocities_60s - 0.25) < 0.01
    assert abs(analyzer.vertical_velocities_600s - 0.20) < 0.01
    assert abs(analyzer.vertical_velocities_3600s - 0.15) < 0.01

    output_file = tempfile.NamedTemporaryFile(suffix=".gpx")
    analyzer.update_file(output_file.name)
    gpx = gpxpy.parse(open(output_file.name, "r"))
    assert len(gpx.tracks[0].segments[0].points[0].extensions[0]) == 3


def test_analyzing_track2_gpx():
    file = "resources/track2.gpx"

    analyzer = TrackAnalyzer(file)
    analyzer.analyze()
    analyzer.get_maximal_values()
    assert abs(analyzer.slope_100 - 0.129) < 0.01
    assert abs(analyzer.vertical_velocities_60s - 0.29) < 0.01
    assert abs(analyzer.vertical_velocities_600s - 0.25) < 0.01
    assert abs(analyzer.vertical_velocities_3600s - 0.185) < 0.01
    assert analyzer.duration < 5


def test_analyzing_track3_gpx():
    file = "resources/track3.gpx"

    analyzer = TrackAnalyzer(file)
    analyzer.analyze()
    analyzer.get_maximal_values()
    assert abs(analyzer.slope_100 - 0.28) < 0.01
    assert abs(analyzer.vertical_velocities_60s - 0.30) < 0.01
    assert abs(analyzer.vertical_velocities_600s - 0.22) < 0.01
    assert abs(analyzer.vertical_velocities_3600s - 0.17) < 0.01
    assert analyzer.duration < 30


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
