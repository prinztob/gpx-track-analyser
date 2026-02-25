from pathlib import Path
from xml.etree import ElementTree

from gpxpy import gpx
from tcxreader.tcxreader import TCXReader  # type: ignore[import-untyped]

from src.Extension import Extension
from src.utils import write_extensions_to_yaml


def convert_tcx_to_gpx(
    tcx_file_path: Path,
    gpx_file_path: Path | None = None,
    name: str = "",
    yaml_extension_file_path: Path | None = None,
) -> None:
    tcx_reader = TCXReader()
    extensions: list[Extension] = []
    data = tcx_reader.read(str(tcx_file_path))
    has_hr = data.hr_avg is not None and data.hr_avg > 0
    has_cadence = data.cadence_avg is not None and data.cadence_avg > 0
    print(f"Extracting track points from tcx file {tcx_file_path}")
    gpx_from_tcx = gpx.GPX()
    gpx_from_tcx.nsmap["gpxtpx"] = (
        "http://www.garmin.com/xmlschemas/TrackPointExtension/v1"
    )
    gpx_from_tcx.name = name
    gpx_from_tcx.description = ""
    gpx_track = gpx.GPXTrack(
        name=name,
        description="",
    )
    gpx_track.type = data.activity_type
    gpx_from_tcx.tracks.append(gpx_track)
    gpx_segment = gpx.GPXTrackSegment()
    gpx_track.segments.append(gpx_segment)
    for point in data.trackpoints:
        gpx_track_point = gpx.GPXTrackPoint(
            latitude=point.latitude,
            longitude=point.longitude,
            elevation=point.elevation,
            time=point.time,
        )
        if yaml_extension_file_path:
            extensions.append(
                Extension(
                    distance=point.distance if point.distance else 0.0,
                    cadence=point.cadence if has_cadence and point.cadence else 0,
                    power=point.tpx_ext["Watts"]
                    if "Watts" in point.tpx_ext and point.tpx_ext["Watts"]
                    else 0,
                    hr=point.hr_value if has_hr and point.hr_value else 0,
                    speed=point.tpx_ext["Speed"]
                    if "Speed" in point.tpx_ext and point.tpx_ext["Speed"]
                    else 0.0,
                )
            )
        if not yaml_extension_file_path and (has_hr or has_cadence):
            gpx_track_point.extensions.append(
                ElementTree.fromstring(f"""<gpxtpx:TrackPointExtension xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">
                    {f"<gpxtpx:hr>{point.hr_value}</gpxtpx:hr>" if has_hr else ""}
                    {f"<gpxtpx:cadence>{point.cadence}</gpxtpx:cadence>" if has_cadence else ""}
                    {f"<gpxtpx:power>{point.tpx_ext['Watts']}</gpxtpx:power>" if "Watts" in point.tpx_ext else ""}
                    {f"<gpxtpx:distance>{point.distance}</gpxtpx:distance>"}
                    </gpxtpx:TrackPointExtension>
                """)
            )
        gpx_segment.points.append(gpx_track_point)
    if yaml_extension_file_path:
        write_extensions_to_yaml(extensions, Path(yaml_extension_file_path))
    if not gpx_file_path:
        gpx_file_path = Path(str(tcx_file_path).replace(".tcx", ".gpx"))
    gpx_file_path.write_text(gpx_from_tcx.to_xml())
    print(f"Successfully converted tcx file to gpx and written to {gpx_file_path}")
