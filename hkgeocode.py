#!/usr/bin/env python3
"""Convert Hong Kong 1980 Grid (HK80) coordinates to HKGeoCode.

HKGeoCode (香港地理碼 / HKGCode) encodes a location in Hong Kong as up to
six Crockford Base32 characters at three nested resolutions:

    Level 1 (2 characters): 2 km × 2 km  district cell
    Level 2 (4 characters): 100 m × 100 m  neighbourhood cell
    Level 3 (6 characters): 5 m × 5 m  standard cell

The south-west origin is Easting 800000 m, Northing 800000 m. Coverage is
64 km east by 48 km north (32 × 24 large cells). Each pair of characters
is (easting index, northing index).

Definition and conversion steps:
    https://zh.wikipedia.org/zh-hk/香港地理碼
"""

from __future__ import annotations

import argparse
import math
import sys

# Crockford Base32 without I, L, O, U (to avoid confusion with 1 and 0).
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

ORIGIN_EASTING = 800_000.0
ORIGIN_NORTHING = 800_000.0

# (cell size in metres, easting index count, northing index count)
_LEVELS = (
    (2_000.0, 32, 24),  # large cell
    (100.0, 20, 20),  # medium cell
    (5.0, 20, 20),  # small cell
)

COVERAGE_EASTING = ORIGIN_EASTING + _LEVELS[0][0] * _LEVELS[0][1]  # 864000
COVERAGE_NORTHING = ORIGIN_NORTHING + _LEVELS[0][0] * _LEVELS[0][2]  # 848000

CELL_SIZE_M = {2: 2_000.0, 4: 100.0, 6: 5.0}

_LENGTHS = {2, 4, 6}


def coverage_grid_shape(cell_size: float = 100.0) -> tuple[int, int]:
    """Return (n_cols, n_rows_from_south) for the full HKGeoCode coverage."""
    n_cols = int(round((COVERAGE_EASTING - ORIGIN_EASTING) / cell_size))
    n_rows = int(round((COVERAGE_NORTHING - ORIGIN_NORTHING) / cell_size))
    return n_cols, n_rows


def hk80_to_grid_index(
    easting: float,
    northing: float,
    cell_size: float = 100.0,
) -> tuple[int, int]:
    """Return (column, row_from_south) of the cell containing the HK80 point.

    Column 0 is the westernmost cell; row 0 is the southernmost cell.
    """
    col = math.floor((easting - ORIGIN_EASTING) / cell_size)
    row = math.floor((northing - ORIGIN_NORTHING) / cell_size)
    n_cols, n_rows = coverage_grid_shape(cell_size)
    if not (0 <= col < n_cols and 0 <= row < n_rows):
        raise HKGeoCodeError(
            f"HK80 ({easting}, {northing}) is outside HKGeoCode coverage"
        )
    return int(col), int(row)


class HKGeoCodeError(ValueError):
    """Raised when coordinates or options are outside the HKGeoCode definition."""


def _encode_index(index: int) -> str:
    if not 0 <= index < len(CROCKFORD):
        raise HKGeoCodeError(f"index {index} is outside Crockford Base32 (0–31)")
    return CROCKFORD[index]


def hk80_to_hkgeocode(
    easting: float,
    northing: float,
    length: int = 6,
) -> str:
    """Return the HKGeoCode for an HK80 grid position.

    Args:
        easting: HK1980 Grid easting in metres.
        northing: HK1980 Grid northing in metres.
        length: 2, 4 or 6 characters (2 km, 100 m or 5 m precision).

    Returns:
        Upper-case HKGeoCode of the requested length.

    Raises:
        HKGeoCodeError: if ``length`` is invalid or the point is outside coverage.
    """
    if length not in _LENGTHS:
        raise HKGeoCodeError("length must be 2, 4 or 6")

    delta_e = easting - ORIGIN_EASTING
    delta_n = northing - ORIGIN_NORTHING

    if not (0.0 <= delta_e < COVERAGE_EASTING - ORIGIN_EASTING):
        raise HKGeoCodeError(
            f"easting {easting} is outside HKGeoCode coverage "
            f"[{ORIGIN_EASTING:g}, {COVERAGE_EASTING:g})"
        )
    if not (0.0 <= delta_n < COVERAGE_NORTHING - ORIGIN_NORTHING):
        raise HKGeoCodeError(
            f"northing {northing} is outside HKGeoCode coverage "
            f"[{ORIGIN_NORTHING:g}, {COVERAGE_NORTHING:g})"
        )

    chars: list[str] = []
    remainder_e = delta_e
    remainder_n = delta_n
    levels_needed = length // 2

    for size, x_count, y_count in _LEVELS[:levels_needed]:
        x_index = math.floor(remainder_e / size)
        y_index = math.floor(remainder_n / size)
        if not (0 <= x_index < x_count and 0 <= y_index < y_count):
            raise HKGeoCodeError(
                f"HK80 ({easting}, {northing}) is outside HKGeoCode coverage"
            )
        chars.append(_encode_index(x_index))
        chars.append(_encode_index(y_index))
        remainder_e -= x_index * size
        remainder_n -= y_index * size

    return "".join(chars)


def hkgeocode_cell_origin(code: str) -> tuple[float, float, float]:
    """Return (easting, northing, cell_size_m) of the cell's south-west corner."""
    code = code.strip().upper()
    if len(code) not in _LENGTHS or len(code) % 2:
        raise HKGeoCodeError("HKGeoCode must be 2, 4 or 6 characters")
    if any(ch not in CROCKFORD for ch in code):
        raise HKGeoCodeError(
            f"HKGeoCode {code!r} contains characters outside Crockford Base32"
        )

    easting = ORIGIN_EASTING
    northing = ORIGIN_NORTHING
    cell_size = _LEVELS[0][0]
    inverse = {ch: i for i, ch in enumerate(CROCKFORD)}

    for level, (size, x_count, y_count) in enumerate(_LEVELS):
        offset = level * 2
        if offset >= len(code):
            break
        x_index = inverse[code[offset]]
        y_index = inverse[code[offset + 1]]
        if x_index >= x_count or y_index >= y_count:
            raise HKGeoCodeError(
                f"HKGeoCode {code!r} has an out-of-range index at level {level + 1}"
            )
        easting += x_index * size
        northing += y_index * size
        cell_size = size

    return easting, northing, cell_size


def _self_test() -> int:
    """Check the Wikipedia / Lands Department worked examples."""
    cases = [
        # Coverage corners (large cells 00 and ZQ).
        (800_000.0, 800_000.0, 2, "00"),
        (862_000.0, 846_000.0, 2, "ZQ"),
        # Sharp Peak trig station 63: official HK80 sheet, Wikipedia WG73JD.
        (856_793.009, 832_366.405, 6, "WG73JD"),
        # South-west corner of the Clock Tower small cell from Wikipedia.
        (835_510.0, 817_185.0, 6, "H8FB2H"),
        (835_700.0, 817_200.0, 4, "H8HC"),
        (839_505.0, 817_015.0, 6, "K8FA13"),
        (828_420.0, 821_985.0, 6, "EA4K4H"),
        (802_000.0, 812_000.0, 2, "16"),
        (838_000.0, 820_000.0, 2, "KA"),
        (862_000.0, 844_000.0, 2, "ZP"),
        (844_000.0, 802_000.0, 2, "P1"),
    ]
    failed = 0
    for easting, northing, length, expected in cases:
        got = hk80_to_hkgeocode(easting, northing, length)
        status = "ok" if got == expected else "FAIL"
        if got != expected:
            failed += 1
        print(f"  {status}: E={easting} N={northing} -> {got} (expected {expected})")
    return failed


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert Hong Kong 1980 Grid easting/northing (metres) to HKGeoCode."
        )
    )
    parser.add_argument(
        "easting",
        nargs="?",
        type=float,
        help="HK80 easting in metres (positional alternative to -e)",
    )
    parser.add_argument(
        "northing",
        nargs="?",
        type=float,
        help="HK80 northing in metres (positional alternative to -n)",
    )
    parser.add_argument(
        "-e",
        "--easting",
        dest="easting_opt",
        type=float,
        metavar="METRES",
        help="HK80 easting in metres",
    )
    parser.add_argument(
        "-n",
        "--northing",
        dest="northing_opt",
        type=float,
        metavar="METRES",
        help="HK80 northing in metres",
    )
    parser.add_argument(
        "-l",
        "--length",
        type=int,
        choices=sorted(_LENGTHS),
        default=6,
        help="code length: 2 (2 km), 4 (100 m) or 6 (5 m). Default: 6",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run built-in checks against published examples and exit",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.self_test:
        failed = _self_test()
        return 1 if failed else 0

    easting = args.easting_opt if args.easting_opt is not None else args.easting
    northing = args.northing_opt if args.northing_opt is not None else args.northing
    if easting is None or northing is None:
        print(
            "error: easting and northing are required "
            "(positional EASTING NORTHING, or -e / -n)",
            file=sys.stderr,
        )
        return 2

    try:
        print(hk80_to_hkgeocode(easting, northing, args.length))
    except HKGeoCodeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
