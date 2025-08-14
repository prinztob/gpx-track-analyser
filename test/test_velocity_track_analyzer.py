from src.gpx_track_analyzer import TrackAnalyzer
from src.velocity_track_analyzer import VelocityTrackAnalyzer, VelocityEntry


def test_reading_split_data():
    file = "resources/splits_1.json"
    analyzer = VelocityTrackAnalyzer([], [file])
    assert len(analyzer.velocity_entries_from_garmin) == 34
    assert VelocityEntry(3359.49, 494.0) == analyzer.velocity_entries_from_garmin[0]


def test_max_velocity_in_kilometer_interval_splits_1():
    file = "resources/splits_1.json"
    analyzer = VelocityTrackAnalyzer([], [file])
    assert round(analyzer.get_average_velocity_for_kilometers(5), 2) == 31.37
    assert round(analyzer.get_average_velocity_for_kilometers(10), 2) == 30.17
    assert round(analyzer.get_average_velocity_for_kilometers(15), 2) == 29.34
    assert round(analyzer.get_average_velocity_for_kilometers(20), 2) == 0.0


def test_max_velocity_in_kilometer_interval_merge_splits():
    analyzer = VelocityTrackAnalyzer(
        [],
        [
            "resources/activity_splits_4.json",
            "resources/activity_splits_5.json",
        ],
    )
    assert round(analyzer.get_average_velocity_for_kilometers(5), 2) == 25.08
    assert round(analyzer.get_average_velocity_for_kilometers(10), 2) == 22.28
    assert round(analyzer.get_average_velocity_for_kilometers(15), 2) == 20.60
    assert round(analyzer.get_average_velocity_for_kilometers(20), 2) == 20.34
    assert round(analyzer.get_average_velocity_for_kilometers(30), 2) == 17.97
    assert round(analyzer.get_average_velocity_for_kilometers(40), 2) == 17.41
    assert round(analyzer.get_average_velocity_for_kilometers(50), 2) == 17.66
    assert round(analyzer.get_average_velocity_for_kilometers(75), 2) == 0.0


def test_max_velocity_in_kilometer_interval_gpx_and_splits():
    track_analyzer = TrackAnalyzer("resources/activity_with_splits.gpx")
    track_analyzer.analyze()
    assert round(track_analyzer.data["avg_velocity_1km"], 2) == 31.3
    assert round(track_analyzer.data["avg_velocity_50km"], 2) == 5.17
    track_analyzer_with_splits = TrackAnalyzer(
        "resources/activity_with_splits.gpx",
        split_files=["resources/activity_with_splits.json"],
    )
    track_analyzer_with_splits.analyze()
    assert round(track_analyzer_with_splits.data["avg_velocity_1km"], 2) == round(
        track_analyzer.data["avg_velocity_1km"], 2
    )
    assert round(track_analyzer_with_splits.data["avg_velocity_50km"], 2) == 23.6
