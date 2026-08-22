# hkgeocode

Convert [Hong Kong 1980 Grid](https://www.geodetic.gov.hk/en/gi/refdoc.htm) easting and northing to [HKGeoCode](https://zh.wikipedia.org/zh-hk/%E9%A6%99%E6%B8%AF%E5%9C%B0%E7%90%86%E7%A2%BC) (香港地理碼 / HKGCode).

HKGeoCode is a public geocode from the Lands Department Survey and Mapping Office. A code is six Crockford Base32 characters (0–9 and A–Z, excluding I, L, O and U) in three nested layers:

| Layer | Characters | Cell size | Grid |
| --- | --- | --- | --- |
| Large (district) | 2 | 2 km × 2 km | 32 × 24 |
| Medium (neighbourhood) | 4 | 100 m × 100 m | 20 × 20 |
| Small (standard) | 6 | 5 m × 5 m | 20 × 20 |

The south-west origin is easting **800000 m**, northing **800000 m**. Coverage is 64 km east by 48 km north. The south-west large cell is `00`; the north-east large cell is `ZQ`. Each pair of characters is the easting index then the northing index.

## Usage

Requires Python 3.9+. No third-party packages.

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
hk80_to_hkgeocode(856793.009, 832366.405, 2)    # 'WG'
```

Run the built-in checks (Wikipedia examples plus the Sharp Peak sheet):

```text
python hkgeocode.py --self-test
```

## Conversion

For HK80 easting *E* and northing *N* (metres):

1. ΔE = *E* − 800000, ΔN = *N* − 800000
2. Large: x₁ = ⌊ΔE / 2000⌋ (0–31), y₁ = ⌊ΔN / 2000⌋ (0–23)
3. Medium: x₂ = ⌊(ΔE mod 2000) / 100⌋ (0–19), y₂ likewise
4. Small: x₃ = ⌊((ΔE mod 2000) mod 100) / 5⌋ (0–19), y₃ likewise

The code is the Crockford character for x₁ y₁ x₂ y₂ x₃ y₃.

This encoding follows the [Wikipedia definition](https://zh.wikipedia.org/zh-hk/%E9%A6%99%E6%B8%AF%E5%9C%B0%E7%90%86%E7%A2%BC). Lands Department is expected to publish an official generation API; use that service when an official code is required.
