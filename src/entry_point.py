import json
import os
from datetime import date, datetime
from typing import Any

import garth
import lxml.etree as mod_etree
import requests
from garminconnect import (
    Garmin,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
    GarminConnectAuthenticationError,
)
from garth.exc import GarthHTTPError
from gpxpy.gpx import GPXTrack, GPX, GPXTrackPoint

from src.tcx_to_gpx import convert_tcx_to_gpx
from .Extension import Extension
from .gpx_track_analyzer import TrackAnalyzer

cycling_ids = [2,5,10,19,20,21,22,25,89,143]

def init_api(user_name: str, password: str, output_file: str) -> Garmin | str:
    """Initialize Garmin API with your credentials."""
    garth.http.USER_AGENT = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.6778.81 Mobile Safari/537.36"
    }
    token_store = output_file + "/.garminconnect"
    try:
        print(
            f"Trying to login to Garmin Connect using token data from '{token_store}'...\n"
        )
        garmin = Garmin()
        garmin.login(token_store)
        return garmin
    except (FileNotFoundError, GarthHTTPError, GarminConnectAuthenticationError):
        # Session is expired. You'll need to log in again
        print(
            "Login tokens not present, login with your Garmin Connect credentials to generate them.\n"
            f"They will be stored in '{token_store}' for future use.\n"
        )
        try:
            garmin = Garmin(email=user_name, password=password, return_on_mfa=True)
            result1, result2 = garmin.login()
            if result1 == "needs_mfa":  # MFA is required
                mfa_code = get_mfa()
                garmin.resume_login(result2, mfa_code)

            # Save Oauth1 and Oauth2 token files to directory for next login
            garmin.garth.dump(token_store)
            print(
                f"Oauth tokens stored in '{token_store}' directory for future use. (first method)\n"
            )
            garmin.login(token_store)
            return garmin
        except (
                FileNotFoundError,
                GarthHTTPError,
                GarminConnectAuthenticationError,
                requests.exceptions.HTTPError,
        ) as err:
            return (
                f"return code: 1Error occurred during Garmin Connect Client init: {err}"
            )
        except Exception as err:
            return f"return code: 1Unknown error occurred during Garmin Connect Client init {err}"


def get_mfa() -> str:
    """Get MFA."""

    return input("MFA one-time code: ")


def get_activities_by_date(api: Garmin, start_date: datetime, end_date: datetime, activity_type: str | None) -> list[dict[str, Any]]:
    """Return available activities."""
    url = api.garmin_connect_activities
    activities = []
    start = 0
    limit = 20
    return_data = True

    while return_data:
        params = {
            "startDate": str(start_date),
            "endDate": str(end_date),
            "start": str(start),
            "limit": str(limit),
        }
        if activity_type:
            params["activityType"] = str(activity_type)
        additional_activities = api.connectapi(url, params=params)
        if additional_activities:
            activities.extend(additional_activities)
            start = start + limit
        else:
            return_data = False
    return activities


def get_exercise_sets(api: Garmin, activity_id: str) -> dict[str, Any]:
    activity_id = str(activity_id)
    return api.connectapi(f"{api.garmin_connect_activity}/{activity_id}")


def get_daily_events(api: Garmin, selected_date: str) -> dict[str, Any] | str:
    try:
        url = f"/wellness-service/wellness/dailyEvents/{api.display_name}"
        params = {
            "calendarDate": str(selected_date),
        }
        print(f"Fetching daily events with url {url} for date {selected_date}")
        return api.connectapi(url, params=params)
    except (
            GarminConnectConnectionError,
            GarminConnectAuthenticationError,
            GarminConnectTooManyRequestsError,
    ) as err:
        return (
            f"return code: 1: Error occurred during Garmin Connect Client get daily events for date "
            f"{selected_date}: {err}"
        )
    except Exception as err:
        return (
            f"return code: 1Unknown error occurred during Garmin Connect Client get daily events for date "
            f"{selected_date}: {err}"
        )


def get_user_summary(api: Garmin, selected_date: str) -> dict[str, Any] | str:
    try:
        return api.get_user_summary(selected_date)
    except (
            GarminConnectConnectionError,
            GarminConnectAuthenticationError,
            GarminConnectTooManyRequestsError,
    ) as err:
        return (
            f"return code: 1: Error occurred during Garmin Connect Client get user summary for date "
            f"{selected_date}: {err}"
        )
    except Exception as err:
        return (
            f"return code: 1Unknown error occurred during Garmin Connect Client get user summary for date "
            f"{selected_date}: {err}"
        )


def get_hrv(api: Garmin, selected_date: str) -> dict[str, Any] | str:
    try:
        print(f"Requesting daily hrv data for date {selected_date}")
        return api.get_hrv_data(selected_date)
    except (
            GarminConnectConnectionError,
            GarminConnectAuthenticationError,
            GarminConnectTooManyRequestsError,
    ) as err:
        return (
            f"return code: 1: Error occurred during Garmin Connect Client get daily hrv for for "
            f"{selected_date}: {err}"
        )
    except Exception as err:
        return (
            f"return code: 1Unknown error occurred during Garmin Connect Client get daily hrv for date "
            f"{selected_date}: {err}"
        )


def get_activity_json_for_date(client: Garmin, selected_date: str) -> str:
    try:
        activities = get_activities_by_date(client, selected_date, selected_date, None)
        for activity in activities:
            if "vo2MaxPreciseValue" not in activity:
                activity["vo2MaxPreciseValue"] = get_precise_vo2max(
                    client, selected_date, activity
                )
        return json.dumps(activities)
    except (
            GarminConnectConnectionError,
            GarminConnectAuthenticationError,
            GarminConnectTooManyRequestsError,
    ) as err:
        return (
            f"return code: 1Error occurred during Garmin Connect Client get activity json for date "
            f"{selected_date}: {err}"
        )
    except Exception as err:
        return (
            f"return code: 1Unknown error occurred during Garmin Connect Client get activity json for date "
            f"{selected_date}: "
            f"{err}"
        )


def is_cycling(activity):
    return activity["activityType"]["typeId"] in cycling_ids if "activityType" in activity and "typeId" in activity[
        "eventType"] else False


def download_tcx(api, activity_id, output_file_tcx, output_file_gpx):
    try:
        gpx_data = api.download_activity(
            activity_id, dl_fmt=Garmin.ActivityDownloadFormat.TCX
        )
        with open(output_file_tcx, "wb") as fb:
            fb.write(gpx_data)
        convert_tcx_to_gpx(output_file_tcx, output_file_gpx)
        return "return code: 0"
    except (
            GarminConnectConnectionError,
            GarminConnectAuthenticationError,
            GarminConnectTooManyRequestsError,
    ) as err:
        return f"return code: 1Error occurred during Garmin Connect Client download tcx for id {activity_id}: {err}"
    except Exception as err:
        return (
            f"return code: 1Unknown error occurred during Garmin Connect Client download tcx for id {activity_id}: "
            f"{err}"
        )


def download_gpx(api: Garmin, activity_id: str, output_file: str) -> str:
    try:
        gpx_data = api.download_activity(
            activity_id, dl_fmt=Garmin.ActivityDownloadFormat.GPX
        )
        with open(output_file, "wb") as fb:
            fb.write(gpx_data)
        return "return code: 0"
    except (
            GarminConnectConnectionError,
            GarminConnectAuthenticationError,
            GarminConnectTooManyRequestsError,
    ) as err:
        return f"return code: 1Error occurred during Garmin Connect Client download gpx for id {activity_id}: {err}"
    except Exception as err:
        return (
            f"return code: 1Unknown error occurred during Garmin Connect Client download gpx for id {activity_id}: "
            f"{err}"
        )


def get_exercise_set(api: Garmin, activity_id: str, folder: str) -> dict[str, Any] | str:
    try:
        sets = get_exercise_sets(api, activity_id)
        output_file = f"{folder}/activity_{str(activity_id)}_exercise_set.json"
        if not os.path.exists(output_file):
            with open(output_file, "w+") as fb:
                json.dump(sets, fb)
        return sets
    except (
            GarminConnectConnectionError,
            GarminConnectAuthenticationError,
            GarminConnectTooManyRequestsError,
    ) as err:
        return f"return code: 1Error occurred during Garmin Connect Client get multi sport data: {err}"
    except Exception as err:
        return f"return code: 1Unknown error occurred during Garmin Connect Client get multi sport data {err}"


def get_split_data(api: Garmin, activity_id: str, folder: str) -> dict[str, Any] | str:
    try:
        return download_splits(api, activity_id, folder)
    except Exception as err:
        return f"return code: 1Unknown error occurred during Garmin Connect Client get split data {err}"


def get_power_data(api: Garmin, selected_date: str) -> dict[str, Any] | str:
    """
    Get power data
    """
    try:
        url = f"/fitnessstats-service/powerCurve/?startDate={selected_date}&endDate={selected_date}"
        print(f"Fetching power data with url {url}")
        return api.connectapi(url)
    except (
            GarminConnectConnectionError,
            GarminConnectAuthenticationError,
            GarminConnectTooManyRequestsError,
    ) as err:
        return f"return code: 1Error occurred during Garmin Connect Client get power data: {err}"
    except Exception as err:  # pylint: disable=broad-except
        return f"return code: 1Unknown error occurred during Garmin Connect Client get power data {err}"


def get_vo2max(api, selected_date):
    """
    Get vo2Max
    """
    try:
        return get_precise_vo2max(api, selected_date, get_activities_by_date(api, selected_date, selected_date, None)[0])
    except (
            GarminConnectConnectionError,
            GarminConnectAuthenticationError,
            GarminConnectTooManyRequestsError,
    ) as err:
        return f"return code: 1Error occurred during Garmin Connect Client get vo2max: {err}"
    except Exception as err:  # pylint: disable=broad-except
        return f"return code: 1Unknown error occurred during Garmin Connect Client get vo2max {err}"


def download_activities_by_date(api: Garmin, folder: str, start_date: datetime, end_date: datetime=date.today()) -> str:
    try:
        print(f"Download activities between {start_date} and {end_date}.")
        activities = get_activities_by_date(api, start_date, end_date, None)
        write_index = 0
        print(f"Downloading {len(activities)} activities.")
        for activity in activities:
            activity_id = activity["activityId"]
            if activity["activityType"]["typeId"] == 89:
                multi_sport_data = get_exercise_sets(api, activity_id)
                child_ids = multi_sport_data["metadataDTO"]["childIds"]
                activity["childIds"] = child_ids
                for child_id in child_ids:
                    details = get_exercise_sets(api, child_id)
                    output_file = f"{folder}/child_{str(child_id)}.json"
                    if not os.path.exists(output_file):
                        with open(output_file, "w+") as fb:
                            json.dump(details, fb)
                start_time_local = activity["startTimeLocal"].split(" ")
                if start_time_local and len(start_time_local) == 2:
                    update_power_data(activity, api, start_time_local[0])
            else:
                activity["childIds"] = []
            start_time_local = activity["startTimeLocal"].split(" ")
            if (
                    start_time_local
                    and len(start_time_local) == 2
                    and "vo2MaxPreciseValue" not in activity
            ):
                activity["vo2MaxPreciseValue"] = get_precise_vo2max(
                    api, start_time_local[0], activity
                )
            output_file = f"{folder}/activity_{str(activity_id)}.json"
            with open(output_file, "w+") as fb:
                json.dump(activity, fb)
                write_index += 1
            download_splits(api, activity_id, folder)
            get_exercise_set(api, activity_id, folder)
        return "return code: 0\nDownloaded {} activities, wrote {} to file".format(
            len(activities), write_index
        )
    except (
            GarminConnectConnectionError,
            GarminConnectAuthenticationError,
            GarminConnectTooManyRequestsError,
    ) as err:
        return f"return code: 1Error occurred during Garmin Connect Client download activities by date: {err}"
    except Exception as err:  # pylint: disable=broad-except
        return f"return code: 1Unknown error occurred during Garmin Connect Client download activities by date {err}"


def get_precise_vo2max(api: Garmin, selected_date: str, activity: dict[str, Any]) -> float:
    url = f"/metrics-service/metrics/maxmet/latest/{selected_date}"
    data = api.connectapi(url)
    if len(data) > 0:
        if is_cycling(activity) and data["cycling"] and "vo2MaxPreciseValue" in data["cycling"]:
            vo2_max_precise_value = data["cycling"]["vo2MaxPreciseValue"]
            print(f"Found vo2MaxPreciseValue {vo2_max_precise_value}.")
            return vo2_max_precise_value
        elif data["generic"] and "vo2MaxPreciseValue" in data["generic"]:
            vo2_max_precise_value = data["generic"]["vo2MaxPreciseValue"]
            print(f"Found vo2MaxPreciseValue {vo2_max_precise_value}.")
            return vo2_max_precise_value
    return 0.0


def download_splits(api: Garmin, activity_id: str, folder: str) -> dict[str, Any]:
    output_file = f"{folder}/activity_{str(activity_id)}_splits.json"
    splits = {}
    if not os.path.exists(output_file):
        print(f"Download splits for activity_id {activity_id}")
        splits = api.get_activity_splits(activity_id)
        with open(output_file, "w+") as fb:
            json.dump(splits, fb)
    else:
        with open(output_file) as fb:
            splits = json.load(fb)
    return splits


def update_power_data(activity: dict[str, Any], api: Garmin, selected_date: str) -> None:
    power_data = get_power_data(api, selected_date)
    if "entries" in power_data and len(power_data["entries"]) == 15:
        activity["maxAvgPower_1"] = power_data["entries"][0]["power"]
        activity["maxAvgPower_2"] = power_data["entries"][1]["power"]
        activity["maxAvgPower_5"] = power_data["entries"][2]["power"]
        activity["maxAvgPower_10"] = power_data["entries"][3]["power"]
        activity["maxAvgPower_20"] = power_data["entries"][4]["power"]
        activity["maxAvgPower_30"] = power_data["entries"][5]["power"]
        activity["maxAvgPower_60"] = power_data["entries"][6]["power"]
        activity["maxAvgPower_120"] = power_data["entries"][7]["power"]
        activity["maxAvgPower_300"] = power_data["entries"][8]["power"]
        activity["maxAvgPower_600"] = power_data["entries"][9]["power"]
        activity["maxAvgPower_1200"] = power_data["entries"][10]["power"]
        activity["maxAvgPower_1800"] = power_data["entries"][11]["power"]
        activity["maxAvgPower_3600"] = power_data["entries"][12]["power"]
        activity["maxAvgPower_7200"] = power_data["entries"][13]["power"]
        activity["maxAvgPower_18000"] = power_data["entries"][14]["power"]


def analyze_gpx_track(gpx_path: str, additional_data_folder: str, split_files: list[str]) -> str:
    try:
        start_time = datetime.now()
        analyzer = TrackAnalyzer(gpx_path, additional_data_folder, split_files)
        if not analyzer.analyze():
            analyzer = TrackAnalyzer(gpx_path, additional_data_folder, split_files)
            analyzer.analyze(True)
        analyzer.write_data_and_extension_to_file()
        print(
            f"Analyzing of {gpx_path} took {(datetime.now() - start_time).total_seconds()}"
        )
        return "return code: 0"
    except Exception as err:  # pylint: disable=broad-except
        return f"return code: 1Unknown error occurred {err}"


def simplify_gpx_track(gpx_path: str, additional_data_folder: str) -> str:
    try:
        start_time = datetime.now()
        analyzer = TrackAnalyzer(gpx_path, additional_data_folder)
        analyzer.write_simplified_track_to_file()
        print(
            f"Simplifying of {gpx_path} took {(datetime.now() - start_time).total_seconds()}"
        )
        return "return code: 0"
    except Exception as err:  # pylint: disable=broad-except
        return f"return code: 1Unknown error occurred {err}"



def merge_tracks(gpx_track_files_to_merge: GPXTrack, output_file: str, name: str) -> str:
    try:
        print(f"Trying to merge the following tracks: {gpx_track_files_to_merge}")
        files = list(gpx_track_files_to_merge)
        analyzer_for_all_tracks = None
        gpx_track_analyzers = []
        for file in files:
            analyzer = TrackAnalyzer(file)
            analyzer.parse_track()
            gpx_track_analyzers.append(analyzer)

        for analyzer in sorted(gpx_track_analyzers, key=lambda a: get_time(a.gpx)):
            if analyzer_for_all_tracks is None:
                analyzer_for_all_tracks = analyzer
            else:
                update_distance(analyzer_for_all_tracks.gpx, analyzer.gpx)
                analyzer_for_all_tracks.gpx.tracks.extend(analyzer.gpx.tracks)
        analyzer_for_all_tracks.gpx.name = name
        with open(output_file, "w") as f:
            f.write(analyzer_for_all_tracks.gpx.to_xml())
        print(f"Wrote file {output_file}")
        return "return code: 0Merging of tracks successful"
    except Exception as err:
        return "return code: 1Unknown error occurred during merging of tracks: %s" % err


def update_distance(gpx_with_correct_distances: GPX, gpx_track_to_be_updated: GPX) ->None:
    last_point_first_track = Extension.parse(
        gpx_with_correct_distances.tracks[-1].segments[-1].points[-1].extensions
    )
    last_point_last_track = Extension.parse(
        gpx_track_to_be_updated.tracks[0].segments[0].points[0].extensions
    )
    delta_last_track = last_point_last_track.distance
    delta_first_track = last_point_first_track.distance - delta_last_track
    for track in gpx_track_to_be_updated.tracks:
        for segment in track.segments:
            points = []
            for point in segment.points:
                point.extensions_calculated = Extension.parse(point.extensions)
                set_tag_in_extensions(
                    gpx_track_to_be_updated,
                    delta_first_track + point.extensions_calculated.distance,
                    point,
                    "distance",
                    )
                points.append(point)
            segment.points = points


def set_tag_in_extensions(gpx: GPX, value: float, point: GPXTrackPoint, tag_name: str) -> None:
    namespace_name = "http://www.garmin.com/xmlschemas/TrackPointExtension/v1"
    namespace = "{" + namespace_name + "}"
    track_extensions = "TrackPointExtension"
    tag = f"{namespace}{track_extensions}"
    if len([e for e in point.extensions if e.tag == tag]) == 0:
        gpx.nsmap["n3"] = namespace_name
        point.extensions.append(mod_etree.Element(tag))
    root = mod_etree.Element(namespace + tag_name)
    root.text = f"{value}"
    elements = [e for e in point.extensions if e.tag == tag][0]
    extensions = [e for e in elements if e.tag.endswith("}" + tag_name)]
    if len(extensions) == 0:
        elements.append(root)
    else:
        elements.remove(extensions[0])
        elements.append(root)


def get_time(gpx: GPX) -> datetime:
    if gpx.time:
        return gpx.time
    elif (
            gpx
            and len(gpx.tracks) > 0
            and len(gpx.tracks[0].segments) > 0
            and len(gpx.tracks[0].segments[0].points) > 0
    ):
        return gpx.tracks[0].segments[0].points[0].time
    else:
        return datetime.now()
