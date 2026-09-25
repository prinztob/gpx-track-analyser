"""
GPX Track Heatmap Generator

This module generates an MBTiles heatmap from a list of GPX tracks.
The heatmap shows the frequency of used roads/paths and can be used
as an overlay on other maps.

Optimized for large datasets (~1000 tracks covering the world).
Optimized for single-worker execution (e.g., Chaquopy on Android).
"""

import argparse
import io
import math
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Iterator

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter

from utils import haversine_distance_m

# Constants for tile generation
TILE_SIZE = 256
MAX_ZOOM = 18
MIN_ZOOM = 0

# Maximum distance (in meters) between consecutive track points.
# Segments longer than this are assumed to be GPS signal gaps
# and are not drawn (to avoid spurious diagonal lines).
MAX_SEGMENT_DISTANCE_M = 500.0


@dataclass
class BoundingBox:
    """Geographic bounding box."""

    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float

    def expand(self, lat: float, lon: float) -> None:
        """Expand bounding box to include the given point."""
        self.min_lat = min(self.min_lat, lat)
        self.max_lat = max(self.max_lat, lat)
        self.min_lon = min(self.min_lon, lon)
        self.max_lon = max(self.max_lon, lon)

    @classmethod
    def empty(cls) -> "BoundingBox":
        """Create an empty bounding box."""
        return cls(min_lat=90.0, max_lat=-90.0, min_lon=180.0, max_lon=-180.0)


def lat_lon_to_tile_coords(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    """
    Convert latitude/longitude to tile coordinates.

    Returns fractional tile coordinates (x, y) at the given zoom level.
    """
    n = 2**zoom
    x = (lon + 180.0) / 360.0 * n
    lat_rad = math.radians(lat)
    y = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n
    return x, y


# Pre-computed bounds for fast validation (avoid function call overhead)
VALID_LAT_MIN = -85.0
VALID_LAT_MAX = 85.0
VALID_LON_MIN = -180.0
VALID_LON_MAX = 180.0


def is_valid_coordinate(lat: float, lon: float) -> bool:
    """Check if a coordinate is valid (not null island and within bounds)."""
    if lat == 0.0 and lon == 0.0:
        return False
    if abs(lat) > 85.0:  # Web Mercator limit
        return False
    if abs(lon) > 180.0:
        return False
    return True


def _parse_gpx_points_iterative(element: ET.Element) -> Iterator[tuple[float, float]]:
    """
    Iteratively parse points from a GPX element (trkpt, rtept, or wpt).

    Uses direct attribute access for speed, avoiding gpxpy object overhead.
    Yields (lat, lon) tuples for valid coordinates.
    """
    lat_attr = element.get('lat')
    lon_attr = element.get('lon')

    if lat_attr is not None and lon_attr is not None:
        try:
            lat = float(lat_attr)
            lon = float(lon_attr)
            # Inline validation for speed
            if not (lat == 0.0 and lon == 0.0):
                if VALID_LAT_MIN <= lat <= VALID_LAT_MAX:
                    if VALID_LON_MIN <= lon <= VALID_LON_MAX:
                        yield (lat, lon)
        except ValueError:
            pass


def load_single_gpx_file_fast(gpx_file: Path) -> list[list[tuple[float, float]]]:
    """
    Load tracks from a single GPX file using fast streaming XML parsing.

    This is significantly faster than gpxpy.parse() because:
    1. Uses ElementTree's iterative parsing (lower memory, faster)
    2. Avoids creating complex gpxpy objects
    3. Uses direct string-to-float conversion
    4. Inlines coordinate validation

    Returns a list of tracks, where each track is a list of (lat, lon) tuples.
    """
    tracks: list[list[tuple[float, float]]] = []

    try:
        # Use iterparse for streaming - only load elements we need
        # This avoids building the full tree in memory
        context = ET.iterparse(str(gpx_file), events=('start', 'end'))

        current_track_points: list[tuple[float, float]] = []
        current_segment_points: list[tuple[float, float]] = []
        in_track = False
        in_segment = False
        in_route = False

        for event, elem in context:
            tag = elem.tag
            # Handle namespaced tags (e.g., {http://www.topografix.com/GPX/1/1}trkpt)
            if '}' in tag:
                tag = tag.split('}')[1]

            if event == 'start':
                if tag == 'trk':
                    in_track = True
                    current_track_points = []
                elif tag == 'trkseg' and in_track:
                    in_segment = True
                    current_segment_points = []
                elif tag == 'rte':
                    in_route = True
                    current_segment_points = []
                elif tag == 'trkpt' and in_segment:
                    for point in _parse_gpx_points_iterative(elem):
                        current_segment_points.append(point)
                elif tag == 'rtept' and in_route:
                    for point in _parse_gpx_points_iterative(elem):
                        current_segment_points.append(point)

            elif event == 'end':
                if tag == 'trkseg' and in_segment:
                    in_segment = False
                    current_track_points.extend(current_segment_points)
                    current_segment_points = []
                elif tag == 'trk' and in_track:
                    in_track = False
                    if current_track_points:
                        tracks.append(current_track_points)
                    current_track_points = []
                elif tag == 'rte' and in_route:
                    in_route = False
                    if current_segment_points:
                        tracks.append(current_segment_points)
                    current_segment_points = []
                # Clear element to save memory during parsing
                elem.clear()

    except Exception as e:
        print(f"Warning: Failed to load {gpx_file}: {e}", file=sys.stderr)

    return tracks


def load_single_gpx_file(gpx_file: Path) -> list[list[tuple[float, float]]]:
    """
    Load tracks from a single GPX file.

    Uses the fast streaming parser by default.
    """
    return load_single_gpx_file_fast(gpx_file)


def load_gpx_tracks_sequential(gpx_files: list[Path]) -> list[list[tuple[float, float]]]:
    """
    Load tracks from GPX files sequentially (optimized for single-worker).

    This is faster than multiprocessing when num_workers=1 because:
    1. No process spawn overhead
    2. No inter-process communication
    3. No pickle serialization of track data

    Returns a list of tracks, where each track is a list of (lat, lon) tuples.
    """
    all_tracks: list[list[tuple[float, float]]] = []
    total_files = len(gpx_files)

    print(f"Loading {total_files} GPX files sequentially...")

    for i, gpx_file in enumerate(gpx_files, 1):
        try:
            tracks = load_single_gpx_file_fast(gpx_file)
            all_tracks.extend(tracks)
            if i % 100 == 0:
                print(f"  Loaded {gpx_file.name} ({i}/{total_files}) - {len(tracks)} tracks")
        except Exception as e:
            print(f"Warning: Failed to process {gpx_file}: {e}", file=sys.stderr)

    return all_tracks


def load_gpx_tracks(
    gpx_files: list[Path], num_workers: int = 4
) -> list[list[tuple[float, float]]]:
    """
    Load tracks from GPX files.

    Automatically chooses sequential loading for single worker (faster due to
    no multiprocessing overhead) or parallel loading for multiple workers.

    Returns a list of tracks, where each track is a list of (lat, lon) tuples.
    """
    # Use sequential loading for single worker - much faster due to no IPC overhead
    if num_workers <= 1:
        return load_gpx_tracks_sequential(gpx_files)

    all_tracks: list[list[tuple[float, float]]] = []
    total_files = len(gpx_files)

    print(f"Loading {total_files} GPX files using {num_workers} workers...")

    with ProcessPoolExecutor(max_workers=num_workers) as executor:
        futures = {executor.submit(load_single_gpx_file_fast, f): f for f in gpx_files}

        completed = 0
        for future in as_completed(futures):
            completed += 1
            gpx_file = futures[future]
            try:
                tracks = future.result()
                all_tracks.extend(tracks)
                if completed % 100 == 0:
                    print(
                        f"  Loaded {gpx_file.name} ({completed}/{total_files}) - {len(tracks)} tracks"
                    )
            except Exception as e:
                print(f"Warning: Failed to process {gpx_file}: {e}", file=sys.stderr)

    return all_tracks


def get_tracks_bounds(tracks: list[list[tuple[float, float]]]) -> Optional[BoundingBox]:
    """Calculate the bounding box of all tracks."""
    if not tracks:
        return None

    bounds = BoundingBox.empty()
    for track in tracks:
        for lat, lon in track:
            bounds.expand(lat, lon)

    return bounds


def get_tiles_for_segment(
    lat1: float, lon1: float, lat2: float, lon2: float, zoom: int
) -> set[tuple[int, int]]:
    """
    Get all tiles that a line segment passes through.

    Uses a simple grid traversal algorithm.
    """
    x1, y1 = lat_lon_to_tile_coords(lat1, lon1, zoom)
    x2, y2 = lat_lon_to_tile_coords(lat2, lon2, zoom)

    tiles: set[tuple[int, int]] = set()

    # Get integer tile coordinates
    tx1, ty1 = int(x1), int(y1)
    tx2, ty2 = int(x2), int(y2)

    # Add both endpoint tiles
    tiles.add((tx1, ty1))
    tiles.add((tx2, ty2))

    # If same tile, we're done
    if tx1 == tx2 and ty1 == ty2:
        return tiles

    # Simple line traversal - sample points along the line
    dx = x2 - x1
    dy = y2 - y1
    length = math.sqrt(dx * dx + dy * dy)

    if length == 0:
        return tiles

    # Sample at 0.5 tile intervals
    num_samples = max(1, int(length * 2))
    for i in range(num_samples + 1):
        t = i / num_samples
        x = x1 + dx * t
        y = y1 + dy * t
        tiles.add((int(x), int(y)))

    return tiles


def _haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great-circle distance between two points in meters."""
    return haversine_distance_m(lat1, lon1, lat2, lon2)


def compute_tile_coverage(
    tracks: list[list[tuple[float, float]]],
    zoom: int,
    max_segment_distance_m: float = MAX_SEGMENT_DISTANCE_M,
) -> dict[tuple[int, int], list[tuple[float, float, float, float]]]:
    """
    Compute which tiles are covered by tracks at a given zoom level.

    Returns a dictionary mapping (tile_x, tile_y) to a list of line segments
    (lat1, lon1, lat2, lon2) that pass through that tile.

    Segments longer than max_segment_distance_m are skipped to avoid
    drawing spurious lines caused by GPS signal gaps.
    """
    tile_segments: dict[tuple[int, int], list[tuple[float, float, float, float]]] = (
        defaultdict(list)
    )

    for track in tracks:
        if len(track) < 2:
            continue

        for i in range(len(track) - 1):
            lat1, lon1 = track[i]
            lat2, lon2 = track[i + 1]

            # Skip segments that are too long (GPS gaps)
            if max_segment_distance_m > 0:
                dist = _haversine_distance_m(lat1, lon1, lat2, lon2)
                if dist > max_segment_distance_m:
                    continue

            # Get tiles this segment passes through
            tiles = get_tiles_for_segment(lat1, lon1, lat2, lon2, zoom)

            for tx, ty in tiles:
                tile_segments[(tx, ty)].append((lat1, lon1, lat2, lon2))

    return tile_segments


def _clip_line_to_tile(
    x0: float, y0: float, x1: float, y1: float, size: int
) -> Optional[tuple[int, int, int, int]]:
    """
    Clip a line segment to the rectangle [0, size-1] x [0, size-1] using the
    Cohen-Sutherland algorithm.

    Returns the clipped integer pixel endpoints (x0, y0, x1, y1), or None if
    the segment lies entirely outside the tile.
    """
    INSIDE = 0
    LEFT = 1
    RIGHT = 2
    BOTTOM = 4
    TOP = 8
    xmin, xmax = 0.0, float(size - 1)
    ymin, ymax = 0.0, float(size - 1)

    def _code(x: float, y: float) -> int:
        c = INSIDE
        if x < xmin:
            c |= LEFT
        elif x > xmax:
            c |= RIGHT
        if y < ymin:
            c |= BOTTOM
        elif y > ymax:
            c |= TOP
        return c

    code0 = _code(x0, y0)
    code1 = _code(x1, y1)

    while True:
        if not (code0 | code1):  # both inside
            return int(x0), int(y0), int(x1), int(y1)
        if code0 & code1:  # both outside same region
            return None

        # Pick the point outside the clip rectangle
        code_out = code0 if code0 else code1
        dx = x1 - x0
        dy = y1 - y0

        if code_out & TOP:
            x = x0 + dx * (ymax - y0) / dy if dy else x0
            y = ymax
        elif code_out & BOTTOM:
            x = x0 + dx * (ymin - y0) / dy if dy else x0
            y = ymin
        elif code_out & RIGHT:
            y = y0 + dy * (xmax - x0) / dx if dx else y0
            x = xmax
        else:  # LEFT
            y = y0 + dy * (xmin - x0) / dx if dx else y0
            x = xmin

        if code_out == code0:
            x0, y0 = x, y
            code0 = _code(x0, y0)
        else:
            x1, y1 = x, y
            code1 = _code(x1, y1)


def draw_line_on_grid(
    grid: np.ndarray, p1: tuple[int, int], p2: tuple[int, int]
) -> None:
    """Draw a line on the grid using Bresenham's algorithm, incrementing frequency."""
    x0, y0 = p1
    x1, y1 = p2

    dx = abs(x1 - x0)
    dy = abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx - dy

    while True:
        if 0 <= y0 < grid.shape[0] and 0 <= x0 < grid.shape[1]:
            grid[y0, x0] += 1.0

        if x0 == x1 and y0 == y1:
            break

        e2 = 2 * err
        if e2 > -dy:
            err -= dy
            x0 += sx
        if e2 < dx:
            err += dx
            y0 += sy


def _resample_track_uniform(
    track: list[tuple[float, float]],
    step_m: float,
    max_segment_distance_m: float,
) -> list[list[tuple[float, float]]]:
    """
    Resample a single track to (approximately) uniform point spacing.

    Returns a list of sub-tracks: the track is split wherever consecutive
    points are further apart than ``max_segment_distance_m`` (GPS gaps), so
    no bridging lines are introduced.  Within each sub-track, points are
    interpolated every ``step_m`` meters, so drawing a traversal adds a
    uniform weight per pixel regardless of the original GPS recording
    interval or travel speed.
    """
    sub_tracks: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = [track[0]]
    carry = 0.0

    for i in range(len(track) - 1):
        lat1, lon1 = track[i]
        lat2, lon2 = track[i + 1]
        seg_dist = _haversine_distance_m(lat1, lon1, lat2, lon2)

        # GPS gap: do not interpolate across it, start a new sub-track
        if max_segment_distance_m > 0 and seg_dist > max_segment_distance_m:
            if len(current) >= 2:
                sub_tracks.append(current)
            current = [(lat2, lon2)]
            carry = 0.0
            continue

        if seg_dist <= 0.0:
            continue

        remaining = seg_dist
        while carry + remaining >= step_m:
            t = (step_m - carry) / remaining
            lat1 = lat1 + (lat2 - lat1) * t
            lon1 = lon1 + (lon2 - lon1) * t
            current.append((lat1, lon1))
            remaining -= step_m - carry
            carry = 0.0
        carry += remaining

    # Close the track at its original end point so the final < step_m
    # stretch is not cut off.
    if current[-1] != track[-1]:
        current.append(track[-1])
    if len(current) >= 2:
        sub_tracks.append(current)

    return sub_tracks


def resample_tracks_uniform(
    tracks: list[list[tuple[float, float]]],
    step_m: float = 10.0,
    max_segment_distance_m: float = MAX_SEGMENT_DISTANCE_M,
) -> list[list[tuple[float, float]]]:
    """
    Resample all tracks to (approximately) uniform point spacing of ``step_m``.

    After resampling, a pixel's frequency approximately reflects the number
    of times a path was traveled, instead of the density of recorded GPS
    points.  Set ``step_m`` to 0 to disable resampling.
    """
    if step_m <= 0:
        return tracks

    resampled: list[list[tuple[float, float]]] = []
    for track in tracks:
        if len(track) < 2:
            continue
        resampled.extend(
            _resample_track_uniform(track, step_m, max_segment_distance_m)
        )
    return resampled


def _build_tile_grid(
    tile_x: int,
    tile_y: int,
    zoom: int,
    segments: list[tuple[float, float, float, float]],
    tile_size: int,
    gaussian_sigma: float,
) -> np.ndarray:
    """
    Build the frequency grid for a single tile (without colour-mapping).

    Draws all segments onto a zero-initialised grid, then optionally applies
    a Gaussian blur.  Returns the resulting float32 array.
    """
    grid = np.zeros((tile_size, tile_size), dtype=np.float32)

    for lat1, lon1, lat2, lon2 in segments:
        x1, y1 = lat_lon_to_tile_coords(lat1, lon1, zoom)
        x2, y2 = lat_lon_to_tile_coords(lat2, lon2, zoom)

        fx1 = (x1 - tile_x) * tile_size
        fy1 = (y1 - tile_y) * tile_size
        fx2 = (x2 - tile_x) * tile_size
        fy2 = (y2 - tile_y) * tile_size

        clipped = _clip_line_to_tile(fx1, fy1, fx2, fy2, tile_size)
        if clipped is None:
            continue

        draw_line_on_grid(grid, (clipped[0], clipped[1]), (clipped[2], clipped[3]))

    if gaussian_sigma > 0:
        grid = gaussian_filter(grid, sigma=gaussian_sigma)

    return grid


def _compute_tile_max_with_blur(
    tile_x: int,
    tile_y: int,
    zoom: int,
    segments: list[tuple[float, float, float, float]],
    tile_size: int,
    gaussian_sigma: float,
) -> float:
    """
    Compute the maximum frequency for a single tile after Gaussian blur.

    This builds a minimal grid and applies blur to get the correct max value
    that matches what will be used in tile generation.

    Optimizations over full _build_tile_grid:
    1. Uses float32 consistently (no type conversions)
    2. Inlines the line drawing to avoid function call overhead
    3. Returns immediately after finding max (no PNG conversion)
    """
    grid = np.zeros((tile_size, tile_size), dtype=np.float32)

    for lat1, lon1, lat2, lon2 in segments:
        x1, y1 = lat_lon_to_tile_coords(lat1, lon1, zoom)
        x2, y2 = lat_lon_to_tile_coords(lat2, lon2, zoom)

        fx1 = (x1 - tile_x) * tile_size
        fy1 = (y1 - tile_y) * tile_size
        fx2 = (x2 - tile_x) * tile_size
        fy2 = (y2 - tile_y) * tile_size

        clipped = _clip_line_to_tile(fx1, fy1, fx2, fy2, tile_size)
        if clipped is None:
            continue

        # Inline Bresenham's algorithm for speed
        x0, y0, x1_coord, y1_coord = clipped
        dx = abs(x1_coord - x0)
        dy = abs(y1_coord - y0)
        sx = 1 if x0 < x1_coord else -1
        sy = 1 if y0 < y1_coord else -1
        err = dx - dy

        while True:
            if 0 <= y0 < tile_size and 0 <= x0 < tile_size:
                grid[y0, x0] += 1.0

            if x0 == x1_coord and y0 == y1_coord:
                break

            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x0 += sx
            if e2 < dx:
                err += dx
                y0 += sy

    if gaussian_sigma > 0:
        grid = gaussian_filter(grid, sigma=gaussian_sigma)

    return float(np.max(grid))


def _effective_sigma(base_sigma: float, zoom: int) -> float:
    """
    Scale the Gaussian blur sigma with the zoom level.

    High zoom levels need the full spread so that slightly offset GPS
    recordings of the same path blend into a single band.  At low zoom
    levels those offsets already collapse into the same pixel, so only a
    minimal blur is applied there - a wide blur would turn every track
    into a broad band that hides the basemap (especially because the
    color ramp draws any non-zero pixel with a visible alpha floor).
    The minimum spread is applied at zoom <= 10 and the full
    ``base_sigma`` at zoom >= 15, with linear interpolation in between.
    """
    if base_sigma <= 0:
        return 0.0
    min_sigma = max(0.5, base_sigma / 4.0)
    if zoom <= 10:
        return min_sigma
    if zoom >= 15:
        return base_sigma
    t = (zoom - 10) / 5.0
    return min_sigma + t * (base_sigma - min_sigma)


def compute_global_reference_frequency(
    tile_coverage: dict[tuple[int, int], list[tuple[float, float, float, float]]],
    zoom: int,
    tile_size: int = 256,
    gaussian_sigma: float = 1.0,
    num_workers: int = 1,
    normalization_percentile: float = 99.5,
) -> float:
    """
    Compute the global reference frequency used to normalise tiles at a zoom level.

    Instead of the absolute maximum (where a single outlier tile - a race
    loop, a trailhead - would darken the whole map), the reference is the
    ``normalization_percentile`` of the per-tile maxima, floored at 1.0.

    Every tile at the same zoom level is normalised against this single
    reference value, preventing colour discontinuities at tile boundaries.
    """
    if not tile_coverage:
        return 0.0

    tile_maxima: list[float] = []

    # For small tile counts or single worker, process sequentially
    # (avoids multiprocessing overhead)
    if num_workers <= 1 or len(tile_coverage) < 4:
        for (tx, ty), segments in tile_coverage.items():
            tile_maxima.append(
                _compute_tile_max_with_blur(tx, ty, zoom, segments, tile_size, gaussian_sigma)
            )
    else:
        # Parallel processing for large tile sets
        from concurrent.futures import ProcessPoolExecutor, as_completed

        with ProcessPoolExecutor(max_workers=num_workers) as executor:
            futures = {
                executor.submit(_compute_tile_max_with_blur, tx, ty, zoom, segments, tile_size, gaussian_sigma): (tx, ty)
                for (tx, ty), segments in tile_coverage.items()
            }

            for future in as_completed(futures):
                try:
                    tile_maxima.append(future.result())
                except Exception as e:
                    tx, ty = futures[future]
                    print(f"  Warning: Failed to compute max for tile ({tx}, {ty}): {e}", file=sys.stderr)

    if not tile_maxima:
        return 0.0

    reference = float(
        np.percentile(np.asarray(tile_maxima, dtype=np.float64), normalization_percentile)
    )
    return max(reference, 1.0)


def generate_single_tile(
    tile_x: int,
    tile_y: int,
    zoom: int,
    segments: list[tuple[float, float, float, float]],
    tile_size: int = 256,
    gaussian_sigma: float = 1.0,
    global_max_freq: float = 0.0,
) -> tuple[int, int, bytes]:
    """
    Generate a single tile from track segments.

    A Gaussian convolution is applied to the raw frequency grid before
    colour-mapping so that slightly offset GPS tracks blend together
    instead of appearing as separate lines.

    ``global_max_freq`` should be the reference frequency for this zoom level
    (obtained from :func:`compute_global_reference_frequency`).  When provided
    (> 0) every tile is normalised against the same scale, which eliminates
    colour discontinuities at tile boundaries.  When omitted (or 0) the tile
    falls back to its own local maximum — useful for single-tile previews.

    Colors use a Strava-style gradient (deep blue -> teal -> green -> yellow
    -> red).  Any non-zero pixel is drawn with a visible alpha floor, so
    paths traveled only once or twice remain visible.

    Returns (tile_x, tile_y, png_bytes).
    """
    grid = _build_tile_grid(tile_x, tile_y, zoom, segments, tile_size, gaussian_sigma)

    # Use the caller-supplied global max when available; fall back to local max
    # only for standalone / preview usage.
    max_freq = global_max_freq if global_max_freq > 0.0 else float(np.max(grid))

    # Create tile image using numpy for speed
    if max_freq <= 0:
        # Empty tile - return transparent PNG
        img = Image.new("RGBA", (tile_size, tile_size), (0, 0, 0, 0))
    else:
        # Vectorized color mapping
        normalized = np.clip(grid / max_freq, 0, 1)
        # Logarithmic scaling
        normalized = np.log1p(normalized * 9) / np.log(10)

        # Create RGBA array
        rgba = np.zeros((tile_size, tile_size, 4), dtype=np.uint8)

        # Strava-style heat gradient: any non-zero value is immediately
        # visible (deep blue with an alpha floor) and ramps through teal,
        # green and yellow to red at the top end.  Only exactly zero is
        # transparent, so even paths traveled once or twice are visible.
        # Deep blue (0 < n < 0.2)
        mask1 = (normalized > 0) & (normalized < 0.2)
        t1 = normalized[mask1] / 0.2
        rgba[mask1, 0] = (30 + t1 * 40).astype(np.uint8)   # 30 -> 70
        rgba[mask1, 1] = (30 + t1 * 60).astype(np.uint8)   # 30 -> 90
        rgba[mask1, 2] = (160 + t1 * 60).astype(np.uint8)  # 160 -> 220
        rgba[mask1, 3] = (150 + t1 * 50).astype(np.uint8)  # alpha 150 -> 200

        # Blue to teal (0.2 <= n < 0.4)
        mask2 = (normalized >= 0.2) & (normalized < 0.4)
        t2 = (normalized[mask2] - 0.2) / 0.2
        rgba[mask2, 0] = ((1 - t2) * 70).astype(np.uint8)   # 70 -> 0
        rgba[mask2, 1] = (90 + t2 * 110).astype(np.uint8)   # 90 -> 200
        rgba[mask2, 2] = (220 - t2 * 30).astype(np.uint8)  # 220 -> 190
        rgba[mask2, 3] = (200 + t2 * 30).astype(np.uint8)  # alpha 200 -> 230

        # Teal to green (0.4 <= n < 0.6)
        mask3 = (normalized >= 0.4) & (normalized < 0.6)
        t3 = (normalized[mask3] - 0.4) / 0.2
        rgba[mask3, 0] = (t3 * 60).astype(np.uint8)         # 0 -> 60
        rgba[mask3, 1] = (200 + t3 * 20).astype(np.uint8)   # 200 -> 220
        rgba[mask3, 2] = ((1 - t3) * 190).astype(np.uint8)  # 190 -> 0
        rgba[mask3, 3] = (230 + t3 * 15).astype(np.uint8)   # alpha 230 -> 245

        # Green to yellow (0.6 <= n < 0.8)
        mask4 = (normalized >= 0.6) & (normalized < 0.8)
        t4 = (normalized[mask4] - 0.6) / 0.2
        rgba[mask4, 0] = (60 + t4 * 195).astype(np.uint8)  # 60 -> 255
        rgba[mask4, 1] = (220 + t4 * 10).astype(np.uint8)  # 220 -> 230
        rgba[mask4, 3] = (245 + t4 * 10).astype(np.uint8)  # alpha 245 -> 255

        # Yellow to red (0.8 <= n <= 1.0)
        mask5 = normalized >= 0.8
        t5 = (normalized[mask5] - 0.8) / 0.2
        rgba[mask5, 0] = 255
        rgba[mask5, 1] = (230 - t5 * 190).astype(np.uint8)  # 230 -> 40
        rgba[mask5, 3] = 255

        img = Image.fromarray(rgba, mode="RGBA")

    # Convert to PNG bytes
    buffer = io.BytesIO()
    img.save(buffer, format="PNG", optimize=True)
    return tile_x, tile_y, buffer.getvalue()


def create_mbtiles(
    tiles_data: dict[tuple[int, int, int], bytes],
    output_path: Path,
    name: str = "GPX Heatmap",
    description: str = "Heatmap showing frequency of GPX track usage",
) -> None:
    """
    Create an MBTiles file from tile data.

    tiles_data is a dictionary mapping (zoom, x, y) to tile PNG data.
    """
    # Remove existing file if present
    if output_path.exists():
        output_path.unlink()

    # Create SQLite database
    conn = sqlite3.connect(str(output_path))
    cursor = conn.cursor()

    # Create tables
    cursor.execute("""
        CREATE TABLE metadata (
            name TEXT,
            value TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE tiles (
            zoom_level INTEGER,
            tile_column INTEGER,
            tile_row INTEGER,
            tile_data BLOB
        )
    """)

    # Insert metadata
    zoom_levels = [z for z, _, _ in tiles_data.keys()]
    metadata = [
        ("name", name),
        ("type", "overlay"),
        ("description", description),
        ("version", "1.0"),
        ("format", "png"),
        ("minzoom", str(min(zoom_levels))),
        ("maxzoom", str(max(zoom_levels))),
    ]

    cursor.executemany("INSERT INTO metadata (name, value) VALUES (?, ?)", metadata)

    # Insert tiles
    for (zoom, x, y), data in tiles_data.items():
        # MBTiles uses TMS coordinate system (y is flipped)
        tms_y = (2**zoom - 1) - y
        cursor.execute(
            "INSERT INTO tiles (zoom_level, tile_column, tile_row, tile_data) VALUES (?, ?, ?, ?)",
            (zoom, x, tms_y, data),
        )

    # Create index
    cursor.execute("""
        CREATE UNIQUE INDEX tile_index ON tiles (zoom_level, tile_column, tile_row)
    """)

    conn.commit()
    conn.close()

def generate_heatmap(
    gpx_files: list[Path],
    output_path: Path,
    min_zoom: int = 6,
    max_zoom: int = 16,
    name: str = "GPX Heatmap",
    description: str = "Heatmap showing frequency of GPX track usage",
    num_workers: int = 1,
    gaussian_sigma: float = 2.0,
    max_segment_distance_m: float = MAX_SEGMENT_DISTANCE_M,
    resample_step_m: float = 10.0,
    normalization_percentile: float = 99.5,
    progress_callback=None,
) -> None:
    """
    Generate a heatmap MBTiles file from GPX tracks.

    Args:
        gpx_files: List of GPX file paths
        output_path: Output MBTiles file path
        min_zoom: Minimum zoom level
        max_zoom: Maximum zoom level
        name: Name for the tile layer
        description: Description for the tile layer
        num_workers: Number of parallel workers for tile generation
        gaussian_sigma: Standard deviation for the Gaussian blur applied to
            each tile's frequency grid at high zoom levels (see
            :func:`_effective_sigma` for the zoom-dependent scaling).  Larger
            values spread the heat further and merge offset tracks more
            aggressively.  Set to 0 to disable.
        max_segment_distance_m: Maximum distance (meters) between consecutive
            track points.  Segments longer than this are skipped to avoid
            drawing spurious lines caused by GPS signal gaps.  Set to 0 to
            disable.
        resample_step_m: Distance in meters for uniform track resampling.
            Tracks are interpolated to this spacing before drawing so that
            pixel counts reflect how often a path was traveled, independent
            of the GPS recording interval or travel speed.  GPS gaps are
            preserved.  Set to 0 to disable resampling.
        normalization_percentile: Percentile (0-100) of the per-tile frequency
            maxima used as the color-normalization reference for a zoom
            level, instead of the absolute maximum.  Keeps a few outlier
            tiles from darkening the whole map.
        progress_callback: Optional callable invoked as
            progress_callback(current_zoom_index, total_zoom_levels) at the
            start of each zoom level.
    """
    start_time = time.time()
    tracks = load_gpx_tracks(gpx_files, num_workers)
    load_time = time.time() - start_time
    print(f"Reading GPX tracks took {load_time:.2f} seconds")

    if not tracks:
        print("Error: No tracks found in GPX files", file=sys.stderr)
        sys.exit(1)

    total_points = sum(len(track) for track in tracks)
    print(f"Loaded {len(tracks)} tracks with {total_points} total points")

    bounds = get_tracks_bounds(tracks)
    if bounds is None:
        print("Error: Could not determine track bounds", file=sys.stderr)
        sys.exit(1)

    print(
        f"Track bounds: lat [{bounds.min_lat:.4f}, {bounds.max_lat:.4f}], "
        f"lon [{bounds.min_lon:.4f}, {bounds.max_lon:.4f}]"
    )

    # Resample tracks to uniform spacing so that pixel counts reflect the
    # number of traversals instead of the GPS point density.  GPS gaps are
    # handled here (tracks are split), so the coverage pass can skip its
    # per-segment gap check afterwards.
    if resample_step_m > 0:
        print(f"Resampling tracks to uniform {resample_step_m} m spacing...")
        resample_start = time.time()
        tracks = resample_tracks_uniform(tracks, resample_step_m, max_segment_distance_m)
        print(
            f"  Resampled to {len(tracks)} sub-tracks "
            f"in {time.time() - resample_start:.2f} seconds"
        )
        coverage_max_segment_distance_m = 0.0
    else:
        coverage_max_segment_distance_m = max_segment_distance_m

    # Generate tiles for each zoom level
    all_tiles: dict[tuple[int, int, int], bytes] = {}

    for zoom in range(min_zoom, max_zoom + 1):
        if progress_callback:
            try:
                progress_callback(zoom - min_zoom + 1, max_zoom - min_zoom + 1)
            except Exception:
                pass
        print(f"Computing tile coverage for zoom level {zoom}...")
        tile_coverage = compute_tile_coverage(
            tracks, zoom, coverage_max_segment_distance_m
        )

        if not tile_coverage:
            print(f"  No tiles to generate for zoom level {zoom}")
            continue

        print(f"  Generating {len(tile_coverage)} tiles...")

        # Zoom-dependent blur: full spread at high zoom merges slightly
        # offset GPS tracks into a single band; minimal spread at low zoom
        # keeps lines thin so the basemap stays visible.
        sigma = _effective_sigma(gaussian_sigma, zoom)
        print(f"  Effective blur sigma: {sigma:.2f}")

        # Pre-pass: compute the global reference frequency across all tiles
        # so that every tile at this zoom level uses the same colour scale.
        # A high percentile of the per-tile maxima is used instead of the
        # absolute maximum so that a few outlier tiles do not darken the
        # whole map.
        print(f"  Computing global reference frequency for zoom {zoom}...")
        reference_start = time.time()
        reference_frequency = compute_global_reference_frequency(
            tile_coverage, zoom, TILE_SIZE, sigma, num_workers, normalization_percentile
        )
        reference_time = time.time() - reference_start
        print(f"  Computing global reference frequency for zoom {zoom} took {reference_time:.2f} seconds")
        print(f"  Reference frequency: {reference_frequency:.2f}")

        # Generate tiles - sequential for single worker, parallel for multiple
        tile_count = 0
        tile_gen_start = time.time()
        if num_workers <= 1:
            # Sequential generation - faster for single worker (no IPC overhead)
            for (tx, ty), segments in tile_coverage.items():
                try:
                    _, _, png_data = generate_single_tile(
                        tx, ty, zoom, segments, TILE_SIZE, sigma, reference_frequency
                    )
                    if png_data and len(png_data) > 32:  # Skip nearly empty tiles
                        all_tiles[(zoom, tx, ty)] = png_data
                        tile_count += 1
                except Exception as e:
                    print(f"  Warning: Failed to generate tile: {e}", file=sys.stderr)
        else:
            # Parallel generation for multiple workers
            with ProcessPoolExecutor(max_workers=num_workers) as executor:
                futures = []
                for (tx, ty), segments in tile_coverage.items():
                    futures.append(
                        executor.submit(
                            generate_single_tile,
                            tx,
                            ty,
                            zoom,
                            segments,
                            TILE_SIZE,
                            sigma,
                            reference_frequency,
                        )
                    )

                for future in as_completed(futures):
                    try:
                        tx, ty, png_data = future.result()
                        if png_data and len(png_data) > 32:  # Skip nearly empty tiles
                            all_tiles[(zoom, tx, ty)] = png_data
                            tile_count += 1
                    except Exception as e:
                        print(f"  Warning: Failed to generate tile: {e}", file=sys.stderr)

        tile_gen_time = time.time() - tile_gen_start
        print(f"  Generated {tile_count} non-empty tiles in {tile_gen_time:.2f} seconds")

    print(f"Total tiles generated: {len(all_tiles)}")

    # Create MBTiles file
    print(f"Creating MBTiles file: {output_path}")
    create_mbtiles(all_tiles, output_path, name, description)
    print("Done!")


def collect_gpx_files(path: Path) -> list[Path]:
    """
    Collect GPX files from a path.

    If path is a directory, returns all .gpx files in it (non-recursive).
    If path is a file, returns it if it's a .gpx file.
    """
    gpx_files: list[Path] = []

    if path.is_dir():
        gpx_files = sorted(path.glob("*.gpx"))
    elif path.is_file() and path.suffix.lower() == ".gpx":
        gpx_files = [path]

    return gpx_files


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate a heatmap MBTiles file from GPX tracks"
    )
    parser.add_argument(
        "input_path",
        type=Path,
        help="Directory containing GPX files or a single GPX file",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("heatmap.mbtiles"),
        help="Output MBTiles file (default: heatmap.mbtiles)",
    )
    parser.add_argument(
        "--min-zoom", type=int, default=6, help="Minimum zoom level (default: 6)"
    )
    parser.add_argument(
        "--max-zoom", type=int, default=16, help="Maximum zoom level (default: 16)"
    )
    parser.add_argument(
        "--name",
        default="GPX Heatmap",
        help="Name for the tile layer (default: GPX Heatmap)",
    )
    parser.add_argument(
        "--description",
        default="Heatmap showing frequency of GPX track usage",
        help="Description for the tile layer",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of parallel workers for tile generation (default: 4)",
    )
    parser.add_argument(
        "--sigma",
        type=float,
        default=2.0,
        help=(
            "Standard deviation for Gaussian blur applied to each tile's "
            "frequency grid at high zoom levels (scaled down at lower "
            "zooms).  Larger values merge offset tracks more "
            "aggressively; use 0 to disable (default: 2.0)"
        ),
    )
    parser.add_argument(
        "--resample-step",
        type=float,
        default=10.0,
        help=(
            "Distance in meters for uniform track resampling, so pixel "
            "counts reflect how often a path was traveled regardless of "
            "GPS recording interval or speed.  Use 0 to disable "
            "(default: 10.0)"
        ),
    )
    parser.add_argument(
        "--max-segment-distance",
        type=float,
        default=MAX_SEGMENT_DISTANCE_M,
        help=(
            f"Maximum distance (meters) between consecutive track points. "
            f"Segments longer than this are skipped to avoid spurious lines "
            f"from GPS gaps. Use 0 to disable (default: {MAX_SEGMENT_DISTANCE_M})"
        ),
    )

    args = parser.parse_args()

    # Collect GPX files from input path
    if not args.input_path.exists():
        print(f"Error: Input path does not exist: {args.input_path}", file=sys.stderr)
        sys.exit(1)

    gpx_files = collect_gpx_files(args.input_path)
    if not gpx_files:
        if args.input_path.is_dir():
            print(
                f"Error: No GPX files found in directory: {args.input_path}",
                file=sys.stderr,
            )
        else:
            print(
                f"Error: Input file is not a GPX file: {args.input_path}",
                file=sys.stderr,
            )
        sys.exit(1)

    generate_heatmap(
        gpx_files=gpx_files,
        output_path=args.output,
        min_zoom=args.min_zoom,
        max_zoom=args.max_zoom,
        name=args.name,
        description=args.description,
        num_workers=args.workers,
        gaussian_sigma=args.sigma,
        max_segment_distance_m=args.max_segment_distance,
        resample_step_m=args.resample_step,
    )


if __name__ == "__main__":
    main()
