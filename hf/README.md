---
pretty_name: World skylines (tall buildings with heights)
license: odbl
tags:
  - geospatial
  - buildings
  - 3d
  - overture-maps
---

# World skylines

Every building in the world with a known height of 30 m or more, with its footprint, height, base height and name, rebuilt
for each Overture Maps release by [AlreadyOpen/world-skylines](https://github.com/AlreadyOpen/world-skylines).

Overture's own building tiles carry heights only at zoom 14, so cities look flat from further out. This layer fills zooms 6-13.

One file per source, never merged, so each keeps its own licence:

- Dataset `overture.parquet` (and one per official source, e.g. `hk.parquet`): `id, height, min_height, name, source, geometry`
  (GeoParquet, WGS84), tallest first
- Bucket [alreadyopen/world-skylines](https://huggingface.co/buckets/alreadyopen/world-skylines) `overture.pmtiles` (and
  `hk.pmtiles`, ...): vector tiles z6-13, layer `skylines`, for MapLibre `fill-extrusion` (height `height`, base `min_height`).
  Overture's file leaves out every country that has an official file.
- `sources.json`: the Overture release, official sources used, row counts, and sha256 of each output

```python
import duckdb
duckdb.sql("SELECT name, height FROM 'hf://datasets/alreadyopen/world-skylines/skylines.parquet' LIMIT 10")
```

## Height checks (Overture only)

OpenStreetMap heights include errors (an 800 m chapel, an 816 m villa). A building is moved to `rejected.parquet`, with its
reason, when its height divided by its floor count (top minus base, since a building part's floors cover only its own tier) is over 12 m (`floors`), or when
a whole building is 300 m or taller without a plausible 2.5-6 m storey (`wikidata`), unless a Wikidata building or tower within 300 m has a height within 15 % of it. Official
sources are taken as published. Rejected buildings stay listed so they can be fixed upstream.

## Sources and terms

- Overture Maps Foundation buildings and building parts (which include OpenStreetMap data; tall towers such as Burj Khalifa carry their height only on their parts), [ODbL-1.0](https://opendatacommons.org/licenses/odbl/1-0/).
  Attribution: Overture Maps Foundation; © OpenStreetMap contributors. `overture.*` is a derived database, so it is ODbL-1.0 too.
- Each official file is under its source's own terms (listed in `sources.json`), e.g. `hk.*`: Lands Department via the
  CSDI Portal, under the [CSDI Terms of Use](https://portal.csdi.gov.hk/csdi-webpage/doc/TNC). The card's `license: odbl`
  covers the Overture files; the official files are not relicensed.

Provided as is, with no warranty, and not endorsed by any of the sources.
