from xml.etree import ElementTree

from gpxpy import gpx
from tcxreader.tcxreader import TCXReader


def convert_tcx_to_gpx(in_file_path, out_file_path=None, name=""):
    tcx_reader = TCXReader()
    data = tcx_reader.read(in_file_path)
    has_hr = data.hr_avg is not None and data.hr_avg > 0
    has_cadence = data.cadence_avg is not None and data.cadence_avg > 0
    print(f"Extracting track points from tcx file {in_file_path}")
    gpx_from_tcx = gpx.GPX()
    gpx_from_tcx.nsmap["gpxtpx"] = "http://www.garmin.com/xmlschemas/TrackPointExtension/v1"
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

        if has_hr or has_cadence:
            gpx_track_point.extensions.append(
                ElementTree.fromstring(f"""<gpxtpx:TrackPointExtension xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">
                    {f"<gpxtpx:hr>{point.hr_value}</gpxtpx:hr>" if has_hr else ""}
                    {f"<gpxtpx:cadence>{point.cadence}</gpxtpx:cadence>" if has_cadence else ""}
                    {f"<gpxtpx:power>{point.tpx_ext['Watts']}</gpxtpx:power>" if 'Watts' in point.tpx_ext else ""}
                    {f"<gpxtpx:distance>{point.distance}</gpxtpx:distance>"}
                    </gpxtpx:TrackPointExtension>
                """)
            )
        gpx_segment.points.append(gpx_track_point)
    if not out_file_path:
        out_file_path = in_file_path.replace(".tcx", ".gpx")
    with open(out_file_path, "w", encoding="utf8") as output:
        output.write(gpx_from_tcx.to_xml())
    print(f"Successfully converted tcx file to gpx and written to {out_file_path}")
