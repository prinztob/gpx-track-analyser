import math
from typing import Tuple, List

import yaml
from gpxpy.gpx import GPXTrackPoint

from src.Extension import Extension

SUFFIX = "_simplified"


def reduce_track_to_relevant_elevation_points(
        points: List[GPXTrackPoint],
) -> List[Tuple[int, GPXTrackPoint]]:
    reduced_points: List[Tuple[int, GPXTrackPoint]] = []
    points_with_doubles: List[Tuple[int, GPXTrackPoint]] = []
    i = 0
    for point in points:
        point.index = i # type: ignore[attr-defined]
        current_elevation = round(point.elevation if point.elevation else 0)
        if i == 0 or i == len(points) - 1:
            points_with_doubles.append((i, point))
        else:
            last_elevation = (
                round(points[i - 1].elevation) if i != 0 and points[i - 1].elevation else current_elevation # type: ignore[arg-type]
            )
            if current_elevation != last_elevation:
                points_with_doubles.append((i, point))
        i += 1
    j = 0
    for point_with_doubles in points_with_doubles:
        current_elevation = round(point_with_doubles[1].elevation) if point_with_doubles[1].elevation else 0
        last_elevation = (
            round(points_with_doubles[j - 1][1].elevation) # type: ignore[arg-type]
            if j != 0 and points_with_doubles[j - 1][1].elevation
            else current_elevation
        )
        next_elevation = (
            round(points_with_doubles[j + 1][1].elevation) # type: ignore[arg-type]
            if j != len(points_with_doubles) - 1 and points_with_doubles[j + 1][1].elevation
            else current_elevation
        )
        if j == 0 or j == len(points_with_doubles) - 1:
            reduced_points.append(point_with_doubles)
        elif (
                current_elevation != last_elevation and current_elevation != next_elevation
        ):
            if math.copysign(1, current_elevation - last_elevation) != math.copysign(
                    1, next_elevation - current_elevation
            ):
                reduced_points.append(point_with_doubles)
        j += 1
    return reduced_points


def remove_elevation_differences_smaller_as(
        points: List[Tuple[int, GPXTrackPoint]], minimal_delta: int
) -> Tuple[List[Tuple[int, GPXTrackPoint]], float, float]:
    filtered_points: List[Tuple[int, GPXTrackPoint]] = []
    elevation_gain = 0.0
    elevation_loss = 0.0
    i = 0
    for point in points:
        if i == 0:
            filtered_points.append(point)
        else:
            delta = (point[1].elevation - filtered_points[-1][1].elevation) if point[1].elevation and filtered_points[-1][1].elevation else 0
            delta_to_second_last = (
                point[1].elevation - filtered_points[-2][1].elevation
                if len(filtered_points) > 1 and point[1].elevation and filtered_points[-2][1].elevation
                else 0
            )
            delta_from_last = (
                filtered_points[-1][1].elevation - filtered_points[-2][1].elevation
                if len(filtered_points) > 1 and filtered_points[-1][1].elevation and filtered_points[-2][1].elevation
                else 0
            )
            if abs(delta) >= minimal_delta:
                filtered_points.append(point)
                if delta > 0:
                    elevation_gain += delta
                else:
                    elevation_loss += delta
            elif abs(delta_to_second_last) > abs(delta_from_last):
                filtered_points.pop(-1)
                filtered_points.append(point)
                if delta > 0:
                    elevation_gain += delta
                else:
                    elevation_loss += delta
        i += 1
    return filtered_points, elevation_gain, elevation_loss


def get_cleaned_track_elevation(points: List[GPXTrackPoint]) -> List[float]:
    flattened_points: List[GPXTrackPoint] = []
    reduced_track_points_for_interval = reduce_track_to_relevant_elevation_points(
        points
    )
    relevant_points, _, _ = remove_elevation_differences_smaller_as(
        reduced_track_points_for_interval, 10
    )
    for i, point_with_index in enumerate(relevant_points):
        flattened_points.append(point_with_index[1])
        if i < len(relevant_points) - 1:
            flattened_points.extend(
                fill_missing_points(point_with_index, relevant_points[i + 1], points)
            )
    if relevant_points[-1][0] < len(points) - 1:
        flattened_points.extend(
            fill_missing_points(relevant_points[-1], (len(points), points[-1]), points)
        )
    return [
        e.elevation - flattened_points[i - 1].elevation if i != 0 and e.elevation and flattened_points[i - 1].elevation else 0  # type: ignore[operator]
        for i, e in enumerate(flattened_points)
    ]


def fill_missing_points(start_point: Tuple[int, GPXTrackPoint], end_point: Tuple[int, GPXTrackPoint], points: List[GPXTrackPoint]) -> List[GPXTrackPoint]:
    res = []
    points_in_between = points[start_point[0] + 1: end_point[0] - 1]
    if len(points_in_between) > 0:
        res.append(points_in_between[0])
        is_increasing = start_point[1].elevation and end_point[1].elevation and start_point[1].elevation < end_point[1].elevation
        for element in points_in_between:
            if is_increasing:
                if element.elevation and res[-1].elevation and element.elevation <= res[-1].elevation:
                    element.elevation = res[-1].elevation
            elif not is_increasing:
                if element.elevation and res[-1].elevation and element.elevation >= res[-1].elevation:
                    element.elevation = res[-1].elevation
            res.append(element)
    return res


def prefix_filename(fn: str) -> str:
    if fn.endswith(".yaml"):
        return fn.replace(".yaml", SUFFIX + ".yaml")
    else:
        return fn.replace(".gpx", SUFFIX + ".gpx")


def write_extensions_to_yaml(extensions: List[Extension], yaml_file: str) -> None:
    with open(yaml_file, "w", encoding="utf-8") as file:
        yaml.dump(
            {"extensions": [e.to_dict() for e in extensions]},
            file,
            default_flow_style=False,
        )
    print(f"Written extensions to {yaml_file}")
