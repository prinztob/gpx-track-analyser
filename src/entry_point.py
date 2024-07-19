import json
import os
from datetime import date

from garminconnect import (
    Garmin,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
    GarminConnectAuthenticationError,
)

from .gpx_track_analyzer import TrackAnalyzer
from src.tcx_to_gpx import convert_tcx_to_gpx

BASE_URL = 'https://connect.garmin.com'


def init_api(user_name, password, output_file):
    """Initialize Garmin API with your credentials."""
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
            garmin = Garmin(user_name, password)
            garmin.login()
            garmin.garth.dump(token_store)
            return garmin
        except (FileNotFoundError, GarthHTTPError, GarminConnectAuthenticationError, requests.exceptions.HTTPError) \
                as err:
            return "return code: 1Error occurred during Garmin Connect Client init: %s" % err
    except Exception as err:
        return "return code: 1Unknown error occurred during Garmin Connect Client init %s" % err


def get_activities_by_date(api, startdate, enddate, activitytype):
    """Return available activities."""
    url = api.garmin_connect_activities
    activities = []
    start = 0
    limit = 20
    return_data = True

    while return_data:
        params = {
            "startDate": str(startdate),
            "endDate": str(enddate),
            "start": str(start),
            "limit": str(limit)
        }
        if activitytype:
            params["activityType"] = str(activitytype)
        additional_activities = api.modern_rest_client.get(url, params=params).json()
        if additional_activities:
            activities.extend(additional_activities)
            start = start + limit
        else:
            return_data = False
    return activities


def get_excercise_sets(api, activity_id):
    activity_id = str(activity_id)
    url = f"proxy/activity-service/activity/{activity_id}"
    return api.modern_rest_client.get(url).json()


def get_activity_json_for_date(client, date):
    try:
        activities = get_activities_by_date(client, date, date, None)
        return json.dumps(activities)
    except (
            GarminConnectConnectionError,
            GarminConnectAuthenticationError,
            GarminConnectTooManyRequestsError,
    ) as err:
        return f"return code: 1Error occurred during Garmin Connect Client get activity json for date {date}: {err}"
    except Exception as err:
        return f"return code: 1Unknown error occurred during Garmin Connect Client get activity json for date {date}: {err}"


def download_tcx(api, activity_id, output_file_tcx, output_file_gpx):
    try:
        gpx_data = api.download_activity(activity_id, dl_fmt=Garmin.ActivityDownloadFormat.TCX)
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
        return (f"return code: 1Unknown error occurred during Garmin Connect Client download tcx for id {activity_id}: "
                f"{err}")


def download_gpx(api, activity_id, output_file):
    try:
        gpx_data = api.download_activity(activity_id, dl_fmt=Garmin.ActivityDownloadFormat.GPX)
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
        return f"return code: 1Unknown error occurred during Garmin Connect Client download gpx for id {activity_id}: {err}"


def get_multi_sport_data(api, activity_id):
    try:
        return get_excercise_sets(api, activity_id)
    except (
            GarminConnectConnectionError,
            GarminConnectAuthenticationError,
            GarminConnectTooManyRequestsError,
    ) as err:
        return "return code: 1Error occurred during Garmin Connect Client get multi sport data: %s" % err
    except Exception as err:
        return "return code: 1Unknown error occurred during Garmin Connect Client get multi sport data %s" % err


def get_split_data(api, activity_id, folder):
    try:
        return download_splits(api, activity_id, folder)
    except (
            GarminConnectConnectionError,
            GarminConnectAuthenticationError,
            GarminConnectTooManyRequestsError,
    ) as err:
        return "return code: 1Error occurred during Garmin Connect Client get split data: %s" % err
    except Exception as err:
        return "return code: 1Unknown error occurred during Garmin Connect Client get split data %s" % err


def get_power_data(api, date):
    """
    Get power data
    """
    try:
        url = f"proxy/fitnessstats-service/powerCurve/?startDate={date}&endDate={date}"
        print(f"Fetching power data with url {url}")
        return api.modern_rest_client.get(url).json()
    except (
            GarminConnectConnectionError,
            GarminConnectAuthenticationError,
            GarminConnectTooManyRequestsError,
    ) as err:
        return "return code: 1Error occurred during Garmin Connect Client get power data: %s" % err
    except Exception as err:  # pylint: disable=broad-except
        return "return code: 1Unknown error occurred during Garmin Connect Client get power data %s" % err


def download_activities_by_date(api, folder, start_date, end_date=date.today()):
    try:
        print(f"Download activities between {start_date} and {end_date}.")
        activities = get_activities_by_date(api, start_date, end_date, None)
        write_index = 0
        print(f"Downloading {len(activities)} activities.")
        for activity in activities:
            activity_id = activity["activityId"]
            if activity["activityType"]["typeId"] == 89:
                multi_sport_data = get_excercise_sets(api, activity_id)
                child_ids = multi_sport_data["metadataDTO"]["childIds"]
                activity["childIds"] = child_ids
                for id in child_ids:
                    details = get_excercise_sets(api, id)
                    output_file = f"{folder}/child_{str(id)}.json"
                    if not os.path.exists(output_file):
                        with open(output_file, "w+") as fb:
                            json.dump(details, fb)
                date = activity["startTimeLocal"].split(" ")
                if date and len(date) == 2:
                    update_power_data(activity, api, date[0])
            else:
                activity["childIds"] = []
            output_file = f"{folder}/activity_{str(activity_id)}.json"
            if not os.path.exists(output_file):
                with open(output_file, "w+") as fb:
                    json.dump(activity, fb)
                    write_index += 1
            download_splits(api, activity_id, folder)
        return "return code: 0\nDownloaded {} activities, wrote {} to file".format(len(activities),
                                                                                   write_index)
    except (
            GarminConnectConnectionError,
            GarminConnectAuthenticationError,
            GarminConnectTooManyRequestsError,
    ) as err:
        return "return code: 1Error occurred during Garmin Connect Client download activities by date: %s" % err
    except Exception as err:  # pylint: disable=broad-except
        return "return code: 1Unknown error occurred during Garmin Connect Client download activities by date %s" % err


def download_splits(api, activity_id, folder):
    splits = api.get_activity_splits(activity_id)
    output_file = f"{folder}/activity_{str(activity_id)}_splits.json"
    if not os.path.exists(output_file):
        with open(output_file, "w+") as fb:
            json.dump(splits, fb)
    return splits


def update_power_data(activity, api, date):
    power_data = get_power_data(api, date)
    if "entries" in power_data and len(power_data["entries"]) == 15:
        activity['maxAvgPower_1'] = power_data['entries'][0]['power']
        activity['maxAvgPower_2'] = power_data['entries'][1]['power']
        activity['maxAvgPower_5'] = power_data['entries'][2]['power']
        activity['maxAvgPower_10'] = power_data['entries'][3]['power']
        activity['maxAvgPower_20'] = power_data['entries'][4]['power']
        activity['maxAvgPower_30'] = power_data['entries'][5]['power']
        activity['maxAvgPower_60'] = power_data['entries'][6]['power']
        activity['maxAvgPower_120'] = power_data['entries'][7]['power']
        activity['maxAvgPower_300'] = power_data['entries'][8]['power']
        activity['maxAvgPower_600'] = power_data['entries'][9]['power']
        activity['maxAvgPower_1200'] = power_data['entries'][10]['power']
        activity['maxAvgPower_1800'] = power_data['entries'][11]['power']
        activity['maxAvgPower_3600'] = power_data['entries'][12]['power']
        activity['maxAvgPower_7200'] = power_data['entries'][13]['power']
        activity['maxAvgPower_18000'] = power_data['entries'][14]['power']


def analyze_gpx_track(path):
    try:
        analyzer = TrackAnalyzer(path)
        analyzer.analyze()
        analyzer.get_maximal_values()
        analyzer.write_file()
        return "return code: 0"
    except Exception as err:  # pylint: disable=broad-except
        return "return code: 1Unknown error occurred %s" % err


def get_device_id(api):
    """
    Get device id
    """
    try:
        url = "proxy/device-service/deviceregistration/devices"
        print(f"Fetching device id with {url}")
        response_json = api.modern_rest_client.get(url).json()
        if len(response_json) > 0:
            for device in response_json:
                if "deviceId" in device:
                    return device["deviceId"]
            return ""
        else:
            return ""
    except (
            GarminConnectConnectionError,
            GarminConnectAuthenticationError,
            GarminConnectTooManyRequestsError,
    ) as err:
        return "return code: 1Error occurred during Garmin Connect Client get power data: %s" % err
    except Exception as err:  # pylint: disable=broad-except
        return "return code: 1Unknown error occurred during Garmin Connect Client get device id %s" % err


def get_solar_intensity_for_date(api, date, device_id):
    """
    Get solar intensity for date
    """
    try:
        url = f"proxy/web-gateway/solar/{device_id}/{date}/{date}"
        print(f"Fetching solar intensity with url {url}")
        return api.modern_rest_client.get(url).json()
    except (
            GarminConnectConnectionError,
            GarminConnectAuthenticationError,
            GarminConnectTooManyRequestsError,
    ) as err:
        return f"return code: 1Error occurred during Garmin Connect Client get solar intensity for date {date}: {err}"
    except Exception as err:  # pylint: disable=broad-except
        return f"return code: 1Unknown error occurred during Garmin Connect Client get solar intensity for date {date}: {err}"


def get_battery_charged_in_percent(solar):
    if "deviceSolarInput" in solar and "solarDailyDataDTOs" in solar["deviceSolarInput"]:
        data_for_dates = solar["deviceSolarInput"]["solarDailyDataDTOs"]
        for data in data_for_dates:
            if "solarInputReadings" in data:
                solar_reading_for_date = data["solarInputReadings"]
                solar_utilization = [intensity["solarUtilization"] for intensity in solar_reading_for_date if
                                     intensity["solarUtilization"] > 0]

                solar_exposition = [intensity["solarUtilization"] for intensity in solar_reading_for_date if
                                    intensity["solarUtilization"] > 5]
                start_date = datetime.strptime(solar_reading_for_date[0]["readingTimestampLocal"],
                                               '%Y-%m-%dT%H:%M:%S.%f')
                end_date = datetime.strptime(solar_reading_for_date[-1]["readingTimestampLocal"],
                                             '%Y-%m-%dT%H:%M:%S.%f')
                seconds = (end_date - start_date).seconds
                multiplicand = 0.2 / (60 * 100)  # 0.2 % per 60 minutes 100% solar intensity (Fenix 6)
                return sum(solar_utilization) * multiplicand, \
                       len(solar_exposition) / 60, \
                       86400 - seconds < 999


def merge_tracks(gpx_track_files_to_merge, output_file, name):
    try:
        gpx_track_files_to_merge = list(gpx_track_files_to_merge)
        analyzer_for_all_tracks = None
        for file in gpx_track_files_to_merge:
            analyzer_for_single_track = TrackAnalyzer(file, True)
            analyzer_for_single_track.parse_track()
            if analyzer_for_all_tracks is None:
                analyzer_for_all_tracks = analyzer_for_single_track
            else:
                time1 = get_time(analyzer_for_all_tracks.gpx)
                time2 = get_time(analyzer_for_single_track.gpx)
                if  time1 is None or time2 is None or time1 < time2:
                    analyzer_for_all_tracks.gpx.tracks.extend(analyzer_for_single_track.gpx.tracks)
                else:
                    analyzer_for_single_track.gpx.tracks.extend(analyzer_for_all_tracks.gpx.tracks)
                    analyzer_for_all_tracks = analyzer_for_single_track
        analyzer_for_all_tracks.gpx.name = name
        analyzer_for_all_tracks.set_all_points_with_distance()
        with open(output_file, "w") as f:
            f.write(analyzer_for_all_tracks.gpx.to_xml())
        print(f"Wrote file {output_file}")
    except Exception as err:
        print( "return code: 1Unknown error occurred during Garmin Connect Client get device id %s" % err)


def get_time(gpx):
    if gpx.time:
        return gpx.time
    if gpx and len(gpx.tracks) > 0 and len(gpx.tracks[0].segments) > 0 and len(gpx.tracks[0].segments[0].points) > 0:
        return gpx.tracks[0].segments[0].points[0].time
