# hkgeocode

Convert [Hong Kong 1980 Grid](https://www.geodetic.gov.hk/en/gi/refdoc.htm) easting and northing to [HKGeoCode](https://zh.wikipedia.org/zh-hk/%E9%A6%99%E6%B8%AF%E5%9C%B0%E7%90%86%E7%A2%BC) (香港地理碼 / HKGCode), and bin point features onto the 100 m HKGeoCode grid as a GeoTIFF.

HKGeoCode is a public geocode from the Lands Department Survey and Mapping Office. A code is six Crockford Base32 characters (0–9 and A–Z, excluding I, L, O and U) in three nested layers:

| Layer | Characters | Cell size | Grid |
| --- | --- | --- | --- |
| Large (district) | 2 | 2 km × 2 km | 32 × 24 |
| Medium (neighbourhood) | 4 | 100 m × 100 m | 20 × 20 |
| Small (standard) | 6 | 5 m × 5 m | 20 × 20 |

The south-west origin is easting **800000 m**, northing **800000 m** (EPSG:2326). Coverage is 64 km east by 48 km north. The south-west large cell is `00`; the north-east large cell is `ZQ`. Each pair of characters is the easting index then the northing index.

## Convert a coordinate

`hkgeocode.py` needs Python 3.9+ and the standard library only.

```text
python hkgeocode.py EASTING NORTHING
python hkgeocode.py -e EASTING -n NORTHING
python hkgeocode.py -e EASTING -n NORTHING --length 4
```

Example — Sharp Peak trigonometrical station 63 (official HK80 sheet: E = 856793.009, N = 832366.405), which Wikipedia lists as `WG73JD`:

```text
python hkgeocode.py 856793.009 832366.405
WG73JD
```

Import as a library:

```python
from hkgeocode import hk80_to_hkgeocode

hk80_to_hkgeocode(856793.009, 832366.405)       # 'WG73JD'
hk80_to_hkgeocode(856793.009, 832366.405, 4)    # 'WG73'
hk80_to_hkgeocode(856793.009, 832366.405, 2)    # 'WG'
```

Run the built-in checks (Wikipedia examples plus the Sharp Peak sheet):

```text
python hkgeocode.py --self-test
```

## Points to 100 m raster

`points_to_hkgcode_raster.py` bins point features onto the four-character HKGeoCode grid (100 m × 100 m, EPSG:2326) and writes a GeoTIFF. Pixel (0, 0) is the north-west corner. Empty cells are nodata 0. The raster extent is snapped to HKGeoCode cell edges (use `--full-extent` for the whole 64 km × 48 km coverage).

Depends on `numpy`, `requests`, `tifffile`, `Pillow`, and `pyproj`.

Default source is the CSDI [Coordinates of Bus Stops](https://portal.csdi.gov.hk/server/rest/services/common/td_rcd_1638874475129_49745/FeatureServer) layer (HK80, layer `STOP_BUS`):

```text
python points_to_hkgcode_raster.py -o output/bus_stops_hkgcode_100m.tif
```

That also writes a colour overlay and a Leaflet viewer:

| File | Role |
| --- | --- |
| `output/bus_stops_hkgcode_100m.tif` | Count raster, EPSG:2326, 100 m pixels |
| `output/bus_stops_hkgcode_100m.png` | Lon/lat overlay, discrete YlOrRd by stop count |
| `output/viewer.html` | Map: greyscale OSM basemap + overlay |

Serve the folder and open the viewer:

```text
python -m http.server 8765 --directory output
```

Then open http://127.0.0.1:8765/viewer.html

The legend uses one colour per integer count (ColorBrewer YlOrRd: 1 pale yellow … 6 dark red).

### Bus-stop example

From the CSDI layer (4,480 stops, all inside HKGeoCode coverage):

| Stops in cell | Number of 100 m cells |
| ---: | ---: |
| 1 | 2,716 |
| 2 | 749 |
| 3 | 69 |
| 4 | 12 |
| 5 | 1 |
| 6 | 1 |

**3,548** occupied cells. The only cell with 6 stops is **`G8K3`** (south-west corner E 833900, N 816300).

Open the GeoTIFF in QGIS (or similar) on an HK80 basemap. The value of each pixel is the number of stops in that neighbourhood cell.

Other inputs:

```text
python points_to_hkgcode_raster.py --geojson points.geojson -o output/counts.tif
python points_to_hkgcode_raster.py --csdi-url <FeatureLayerURL> --length 4
```

`--length 2` or `6` bins to 2 km or 5 m cells instead of 100 m.

## Conversion formula

For HK80 easting *E* and northing *N* (metres):

1. ΔE = *E* − 800000, ΔN = *N* − 800000
2. Large: x₁ = ⌊ΔE / 2000⌋ (0–31), y₁ = ⌊ΔN / 2000⌋ (0–23)
3. Medium: x₂ = ⌊(ΔE mod 2000) / 100⌋ (0–19), y₂ likewise
4. Small: x₃ = ⌊((ΔE mod 2000) mod 100) / 5⌋ (0–19), y₃ likewise

The code is the Crockford character for x₁ y₁ x₂ y₂ x₃ y₃.

This encoding follows the [Wikipedia definition](https://zh.wikipedia.org/zh-hk/%E9%A6%99%E6%B8%AF%E5%9C%B0%E7%90%86%E7%A2%BC). Lands Department is expected to publish an official generation API; use that service when an official code is required.
