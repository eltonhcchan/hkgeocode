# HKGCode or Hong Kong Geocode

HKGCode or Hong Kong Geocode, is a public geocode from the Survey and Mapping Office, Lands Department and details can be found in [wikipedia](https://zh.wikipedia.org/wiki/%E9%A6%99%E6%B8%AF%E5%9C%B0%E7%90%86%E7%A2%BC). Acoording to the wiki, it is a new geocode and is derived from the HK1980 grid system, i.e. EPSG:2326, a projected coordinate system covering Hong Kong region only. In other words, it is a local grid system, and HKGCode inherits the same. This code is six Crockford Base32 characters (0–9 and A–Z, excluding I, L, O and U) in three nested layers:

| Layer | Characters | Cell size | Grid |
| --- | --- | --- | --- |
| Large (district) | 2 | 2 km × 2 km | 32 × 24 |
| Medium (neighbourhood) | 4 | 100 m × 100 m | 20 × 20 |
| Small (standard) | 6 | 5 m × 5 m | 20 × 20 |

The south-west origin is easting **800000 m**, northing **800000 m** (EPSG:2326). Coverage is 64 km east by 48 km north. The south-west large cell is `00`; the north-east large cell is `ZQ`. Each pair of characters is the easting index then the northing index.


## Convert HK1980 grid easting and northing to hkgeocode
Convert [Hong Kong 1980 Grid](https://www.geodetic.gov.hk/en/gi/refdoc.htm) easting and northing to [HKGeoCode](https://zh.wikipedia.org/zh-hk/%E9%A6%99%E6%B8%AF%E5%9C%B0%E7%90%86%E7%A2%BC) (香港地理碼 / HKGCode), and bin point features onto the 100 m HKGeoCode grid as a GeoTIFF.
`hkgeocode.py` requires Python 3.9+ and only the standard library.

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

## Map explorer

`explorer.html` is a hkgeocode explorer. It draws **only cells that intersect the current map view** (up to 12,000). Turn on **All cell boundaries at selected level** to outline every in-view cell at 2 km / 100 m / 5 m, and **Identifiers at cell centres** to label every one of those cells on a canvas overlay. Each identifier stays on the visible part of its cell while you pan, so labels do not vanish when the geographic centre leaves the viewport. Parent, neighbour, and child outlines are clipped the same way.

Click [here](https://eltonhcchan.github.io/hkgeocode/) to try it now.

```text
python -m http.server 8765
```

Then open http://127.0.0.1:8765/explorer.html (or `#code=H8FB2H`). Click a location to select a cell; use the panel for parent, **neighbours** (up to eight adjacent cells in view), children-in-view, and resolution (2 km, 100 m, 5 m, or auto by zoom). **Fit cell** (or going to a code) zooms until the selected cell fills the view, including 100 m cells (the map can overzoom OSM tiles to zoom 22). **Reset** clears the selection. The panel footer shows the HKGeoCode under the pointer. **Basemap** defaults to the Lands Department topographic map (colour) with Traditional Chinese labels; **LandsD grey** is the same tiles in greyscale, **OpenStreetMap** is colour OSM, and **Esri Open Streets** is Esri World Street Map (WGS84 XYZ twins of the HK80 vector-tile services overlay the Web Mercator grid).

**Polygon to cells** loads an Esri FeatureServer/MapServer polygon layer from a REST URL. The explorer checks that the layer is polygon geometry (errors go to the panel console). Choose a **classification field**, then turn the mode on: a cell is included only if its **centroid** falls inside a polygon, so cells never overlap or double-count. Polygons draw on top of the grid; cells and polygons that share an attribute value use the same colour.

## GeoLibre plugin

`plugin/` is an external GeoLibre plugin with the same grid workflow as the built-in DGGS plugins: cells in the current view, automatic or manual size (2 km, 100 m, 5 m), a 12,000-cell limit, and click-to-identify with parent, neighbours, GeoJSON, and CSV. After install it is listed under Plugins → Installed.

```text
cd plugin
npm install
npm test
npm run package:geolibre
```

The zip is `plugin/geolibre-plugin/hkgeocode-0.1.0.zip`. Point GeoLibre at the unpacked `plugin/geolibre-plugin` folder (Settings → Manage Plugins), or run `npm run dev` and open http://127.0.0.1:5174/ (`?hkgeocode=WG73JD` selects that cell).

## Points to 100 m raster

`points_to_hkgcode_raster.py` bins point features onto the four-character HKGeoCode grid (100 m × 100 m, EPSG:2326) and writes a GeoTIFF. Pixel (0, 0) is the north-west corner. Empty cells are nodata 0. The raster extent is snapped to HKGeoCode cell edges (use `--full-extent` for the whole 64 km × 48 km coverage).

Depends on `numpy`, `requests`, `tifffile`, `Pillow`, `pyproj`, and `fiona` (File Geodatabase).

The first argument is the point source: an ArcGIS FeatureServer/MapServer URL, or a File Geodatabase (`.gdb`). In both cases the program requires **exactly one point feature layer** (tables are ignored; extra line/polygon layers are rejected).

```text
python points_to_hkgcode_raster.py https://.../FeatureServer -o output/counts.tif
python points_to_hkgcode_raster.py https://.../FeatureServer/0 -o output/counts.tif
python points_to_hkgcode_raster.py path/to/stops.gdb -o output/counts.tif
```

Default source is the CSDI [Coordinates of Bus Stops](https://portal.csdi.gov.hk/server/rest/services/common/td_rcd_1638874475129_49745/FeatureServer) service (one point layer, `STOP_BUS`):

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

`--length 2` or `6` bins to 2 km or 5 m cells instead of 100 m. Coordinates are requested or reprojected to EPSG:2326 before binning.

## CSDI chatbot

`chatbot_app.py` is a Streamlit chatbot that takes **two point layers from the CSDI portal**, bins each onto the HKGeoCode grid, measures how the two layers are spatially related, and then answers questions about the datasets with histograms, bar charts, tables and a map. The proof-of-concept pair is [Coordinates of Bus Stops](https://portal.csdi.gov.hk/csdi-webpage/dataset/td_rcd_1638874475129_49745) and [Wi-Fi.HK](https://portal.csdi.gov.hk/csdi-webpage/dataset/dpo_rcd_1629267205215_74392).

```text
pip install -r requirements-chatbot.txt
streamlit run chatbot_app.py
```

Language model via [OpenRouter](https://openrouter.ai/) (tool calling is required, so pick a model that supports it):

```text
set OPENROUTER_API_KEY=sk-or-...                 # required for the LLM agent
set OPENROUTER_MODEL=openai/gpt-4o-mini          # optional; e.g. anthropic/claude-3.5-sonnet, google/gemini-2.0-flash-001
```

The key and model can also be entered in the sidebar. Other OpenAI-compatible servers still work through `OPENAI_BASE_URL` / `OPENAI_API_KEY` (or the sidebar Base URL field). Without a key the app falls back to a keyword-driven rule-based agent that calls the same analysis tools.

**Sidebar.** Type a keyword for layer A and layer B; the app searches the CSDI catalogue (`geoportal/rest/metadata/search`) and keeps only datasets whose ArcGIS FeatureServer has a point layer. You can also paste a FeatureServer layer URL. Choose the resolution (100 m or 2 km cells) and click **Run analysis**.

**Analysis.** Points are requested in EPSG:2326 and encoded with `hkgeocode.py`. For each layer the bot reports points, occupied cells, the count-per-cell distribution and the top cells. Spatial correlation between A and B uses:

| Measure | Meaning |
| --- | --- |
| Pearson / Spearman on per-cell counts | over the occupied cells and over the whole study area (every cell inside the 2 km cells occupied by either layer, zeros included) |
| Jaccard overlap, conditional shares | how many cells hold both layers |
| Bivariate Moran's I (queen contiguity, permutation test) | whether cells with many A are surrounded by cells with many B; local HH / LL / HL / LH clusters |
| Nearest-neighbour distances vs a random baseline | median distance from each A to the closest B (and B to A), share within 100 / 200 / 500 m |

The summary map (folium) shows co-location classes per cell (A only, B only, both), a 2 km overview of shared cells, the local Moran clusters and the raw points; it is also saved to `output/chatbot_map_<length>.html` with the cells as GeoJSON.

**Chat.** Example questions: `histogram of Wi-Fi per cell`, `bar chart of Wi-Fi by venue type`, `bar chart of wifi by district weighted by hotspots`, `which 2 km cells have bus stops but no Wi-Fi?`, `how many wifi in Yuen Long`, `what is in cell H9GB`, `correlation at 2 km`, `what should we do next?`. The tools behind these (`chatbot/tools.py`) are shared by the LLM and rule-based agents.

Modules: `chatbot/csdi.py` (catalogue search, FeatureServer download), `chatbot/grid.py` (vectorised HKGeoCode encoding, cell counts), `chatbot/correlation.py` (numpy/scipy spatial statistics), `chatbot/charts.py` (plotly), `chatbot/mapping.py` (folium), `chatbot/tools.py`, `chatbot/llm.py`.

## Conversion formula

For HK80 easting *E* and northing *N* (metres):

1. ΔE = *E* − 800000, ΔN = *N* − 800000
2. Large: x₁ = ⌊ΔE / 2000⌋ (0–31), y₁ = ⌊ΔN / 2000⌋ (0–23)
3. Medium: x₂ = ⌊(ΔE mod 2000) / 100⌋ (0–19), y₂ likewise
4. Small: x₃ = ⌊((ΔE mod 2000) mod 100) / 5⌋ (0–19), y₃ likewise

The code is the Crockford character for x₁ y₁ x₂ y₂ x₃ y₃.

This encoding follows the [Wikipedia definition](https://zh.wikipedia.org/zh-hk/%E9%A6%99%E6%B8%AF%E5%9C%B0%E7%90%86%E7%A2%BC).
