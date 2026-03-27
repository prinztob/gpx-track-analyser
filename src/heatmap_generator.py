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
    R = 6371000.0  # Earth radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


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


def frequency_to_color(
    frequency: float, max_frequency: float
) -> tuple[int, int, int, int]:
    """
    Convert frequency value to RGBA color.

    Uses a heat color scheme: transparent -> blue -> green -> yellow -> red.
    """
    if frequency <= 0 or max_frequency <= 0:
        return 0, 0, 0, 0  # Transparent

    # Normalize frequency
    norm = min(1.0, frequency / max_frequency)

    # Apply logarithmic scaling for better visualization
    norm = math.log(1 + norm * 9) / math.log(10)

    # Heat color gradient
    if norm < 0.25:
        # Transparent to blue
        t = norm / 0.25
        r, g, b = 0, 0, int(255 * t)
        a = int(255 * t * 0.5)
    elif norm < 0.5:
        # Blue to green
        t = (norm - 0.25) / 0.25
        r, g, b = 0, int(255 * t), int(255 * (1 - t))
        a = int(128 + 64 * t)
    elif norm < 0.75:
        # Green to yellow
        t = (norm - 0.5) / 0.25
        r, g, b = int(255 * t), 255, 0
        a = int(192 + 32 * t)
    else:
        # Yellow to red
        t = (norm - 0.75) / 0.25
        r, g, b = 255, int(255 * (1 - t)), 0
        a = int(224 + 31 * t)

    return (r, g, b, a)


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


def compute_global_max_frequency(
    tile_coverage: dict[tuple[int, int], list[tuple[float, float, float, float]]],
    zoom: int,
    tile_size: int = 256,
    gaussian_sigma: float = 1.0,
    num_workers: int = 1,
) -> float:
    """
    Compute the global maximum frequency value across all tiles at a zoom level.

    This pre-pass is required so that every tile at the same zoom level uses
    an identical normalisation scale, preventing colour discontinuities at
    tile boundaries.
    
    Optimized version that:
    1. Uses inlined Bresenham algorithm (avoids function call overhead)
    2. Supports parallel processing for multiple tiles
    3. Still applies Gaussian blur to get correct max values
    """
    if not tile_coverage:
        return 0.0
    
    # For small tile counts or single worker, process sequentially
    # (avoids multiprocessing overhead)
    if num_workers <= 1 or len(tile_coverage) < 4:
        global_max = 0.0
        for (tx, ty), segments in tile_coverage.items():
            local_max = _compute_tile_max_with_blur(tx, ty, zoom, segments, tile_size, gaussian_sigma)
            if local_max > global_max:
                global_max = local_max
        return global_max
    
    # Parallel processing for large tile sets
    from concurrent.futures import ProcessPoolExecutor, as_completed
    
    global_max = 0.0
    with ProcessPoolExecutor(max_workers=num_workers) as executor:
        futures = {
            executor.submit(_compute_tile_max_with_blur, tx, ty, zoom, segments, tile_size, gaussian_sigma): (tx, ty)
            for (tx, ty), segments in tile_coverage.items()
        }
        
        for future in as_completed(futures):
            try:
                local_max = future.result()
                if local_max > global_max:
                    global_max = local_max
            except Exception as e:
                tx, ty = futures[future]
                print(f"  Warning: Failed to compute max for tile ({tx}, {ty}): {e}", file=sys.stderr)
    
    return global_max


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

    ``global_max_freq`` should be the maximum frequency value observed across
    **all** tiles at this zoom level (obtained from
    :func:`compute_global_max_frequency`).  When provided (> 0) every tile is
    normalised against the same scale, which eliminates colour discontinuities
    at tile boundaries.  When omitted (or 0) the tile falls back to its own
    local maximum — useful for single-tile previews.

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

        # Apply heat color gradient (vectorized)
        # Transparent to blue (0-0.25)
        mask1 = (normalized > 0) & (normalized < 0.25)
        t1 = normalized[mask1] / 0.25
        rgba[mask1, 2] = (t1 * 255).astype(np.uint8)  # Blue
        rgba[mask1, 3] = (t1 * 127.5).astype(np.uint8)  # Alpha

        # Blue to green (0.25-0.5)
        mask2 = (normalized >= 0.25) & (normalized < 0.5)
        t2 = (normalized[mask2] - 0.25) / 0.25
        rgba[mask2, 1] = (t2 * 255).astype(np.uint8)  # Green
        rgba[mask2, 2] = ((1 - t2) * 255).astype(np.uint8)  # Blue out
        rgba[mask2, 3] = (128 + t2 * 64).astype(np.uint8)  # Alpha

        # Green to yellow (0.5-0.75)
        mask3 = (normalized >= 0.5) & (normalized < 0.75)
        t3 = (normalized[mask3] - 0.5) / 0.25
        rgba[mask3, 0] = (t3 * 255).astype(np.uint8)  # Red
        rgba[mask3, 1] = 255  # Green
        rgba[mask3, 3] = (192 + t3 * 32).astype(np.uint8)  # Alpha

        # Yellow to red (0.75-1.0)
        mask4 = normalized >= 0.75
        t4 = (normalized[mask4] - 0.75) / 0.25
        rgba[mask4, 0] = 255  # Red
        rgba[mask4, 1] = ((1 - t4) * 255).astype(np.uint8)  # Green out
        rgba[mask4, 3] = (224 + t4 * 31).astype(np.uint8)  # Alpha

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
        gaussian_sigma: Standard deviation for Gaussian blur applied to each
            tile's frequency grid.  Larger values spread the heat further and
            merge offset tracks more aggressively.  Set to 0 to disable.
        max_segment_distance_m: Maximum distance (meters) between consecutive
            track points.  Segments longer than this are skipped to avoid
            drawing spurious lines from GPS signal gaps.  Set to 0 to disable.
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

    # Generate tiles for each zoom level
    all_tiles: dict[tuple[int, int, int], bytes] = {}

    for zoom in range(min_zoom, max_zoom + 1):
        print(f"Computing tile coverage for zoom level {zoom}...")
        tile_coverage = compute_tile_coverage(tracks, zoom, max_segment_distance_m)

        if not tile_coverage:
            print(f"  No tiles to generate for zoom level {zoom}")
            continue

        print(f"  Generating {len(tile_coverage)} tiles...")

        # Pre-pass: compute the global maximum frequency across all tiles so
        # that every tile at this zoom level uses the same colour scale.
        # Without this, tiles with fewer track pixels appear brighter than
        # neighbouring tiles, causing visible colour seams at tile edges.
        print(f"  Computing global frequency maximum for zoom {zoom}...")
        global_max_start = time.time()
        global_max = compute_global_max_frequency(
            tile_coverage, zoom, TILE_SIZE, gaussian_sigma, num_workers
        )
        global_max_time = time.time() - global_max_start
        print(f"  Computing global frequency maximum for zoom {zoom} took {global_max_time:.2f} seconds")
        print(f"  Global max frequency: {global_max:.2f}")

        # Generate tiles - sequential for single worker, parallel for multiple
        tile_count = 0
        tile_gen_start = time.time()
        if num_workers <= 1:
            # Sequential generation - faster for single worker (no IPC overhead)
            for (tx, ty), segments in tile_coverage.items():
                try:
                    _, _, png_data = generate_single_tile(
                        tx, ty, zoom, segments, TILE_SIZE, gaussian_sigma, global_max
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
                            gaussian_sigma,
                            global_max,
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
        "--min-zoom", type=int, default=8, help="Minimum zoom level (default: 8)"
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
        default=1.0,
        help=(
            "Standard deviation for Gaussian blur applied to each tile's "
            "frequency grid.  Larger values merge offset tracks more "
            "aggressively; use 0 to disable (default: 1.0)"
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
    )


if __name__ == "__main__":
    main()
