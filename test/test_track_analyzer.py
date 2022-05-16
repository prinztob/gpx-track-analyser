from src.track_analyzer import TrackAnalyzer


def test_analyzing_track_gpx():
    file = "resources/track.gpx"

    analyzer = TrackAnalyzer(file)
    analyzer.analyze()
    analyzer.get_maximal_values()
    assert abs(analyzer.slope_100 - 0.361) < 0.01
    assert abs(analyzer.vertical_velocities_60s - 0.25) < 0.01
    assert abs(analyzer.vertical_velocities_600s - 0.20) < 0.01
    assert abs(analyzer.vertical_velocities_3600s - 0.15) < 0.01
    print(analyzer.duration)
    assert analyzer.duration < 8


def test_analyzing_track2_gpx():
    file = "resources/track2.gpx"

    analyzer = TrackAnalyzer(file)
    analyzer.analyze()
    analyzer.get_maximal_values()
    assert abs(analyzer.slope_100 - 0.129) < 0.01
    assert abs(analyzer.vertical_velocities_60s - 0.29) < 0.01
    assert abs(analyzer.vertical_velocities_600s - 0.25) < 0.01
    assert abs(analyzer.vertical_velocities_3600s - 0.185) < 0.01
    print(analyzer.duration)
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
    print(analyzer.duration)
    assert analyzer.duration < 30
