---
pretty_name: World buildings (every building with a height)
license: odbl
license_link: https://opendatacommons.org/licenses/odbl/1-0/
tags:
  - geospatial
  - buildings
  - 3d
  - overture-maps
---

# World buildings

Every building in the world that has a known height, with every field its source publishes, rebuilt for each Overture Maps
release by [AlreadyOpen/world-buildings](https://github.com/AlreadyOpen/world-buildings). About 206 million buildings and
building parts.

The world is split into regions (consecutive Overture files, which Overture sorts by location). One region per source
licence, never merged:

- `index.json` (dataset and bucket): the release, the tile layer (`buildings`) and every region with its `source`, `bbox`,
  row count and tile URL. Start here.
- Dataset `v/<release>/<region>/*.parquet`: GeoParquet (WGS84), every Overture field plus `source` and `check`.
- Bucket [alreadyopen/world-buildings](https://huggingface.co/buckets/alreadyopen/world-buildings) `v/<release>/<region>.pmtiles`:
  vector tiles, layer `buildings`, every field, for MapLibre `fill-extrusion` (height `height`, base `min_height`).
  Zoomed out, the tallest come first (150 m+ from zoom 6, 80 m from 8, 40 m from 10, 20 m from 11, 10 m from 12); every
  building is in from zoom 13, and tiles go deeper where a city is dense, so none is dropped.
- `manifest.json` (dataset and bucket): row counts per source, sha256 and size of every file, and the sources.

```python
import duckdb
duckdb.sql("SELECT names.primary, height FROM 'hf://datasets/alreadyopen/world-buildings/v/*/r000/*.parquet' ORDER BY height DESC LIMIT 10")
```

## Height checks (Overture only): flagged, never removed

OpenStreetMap heights include errors (an 800 m chapel, an 816 m villa). Such a building keeps its row and gets a `check` value:
`floors` when its height divided by its floor count (top minus base) is over 12 m, `wikidata` when a whole building is 300 m
or taller without a plausible 2.5-6 m storey; unless a Wikidata building or tower within 300 m has a height within 15 % of
it. Flagged buildings are tiled from zoom 13 only, so a wrong spike does not stand in the skyline. Official sources are taken
as published (`check` is empty).

## Sources and terms

- Overture Maps Foundation buildings and building parts (which include OpenStreetMap data; tall towers such as Burj Khalifa
  carry their height only on their parts), [ODbL-1.0](https://opendatacommons.org/licenses/odbl/1-0/).
  Attribution: Overture Maps Foundation; © OpenStreetMap contributors. The Overture regions are a derived database, so they
  are ODbL-1.0 too.
- Region `hk`: Lands Department via the CSDI Portal, under the
  [CSDI Terms of Use](https://portal.csdi.gov.hk/csdi-webpage/doc/TNC). Overture also carries some Hong Kong buildings (from
  OpenStreetMap); they stay in the Overture regions, so those places appear in both. The card's `license: odbl` covers the
  Overture regions; official regions are not relicensed.
- Region `nl`: © 3DBAG by tudelft3d and 3DGI, [CC BY 4.0](https://docs.3dbag.nl/en/copyright/), every building 3DBAG
  publishes, from its per-tile GeoPackages. Changed: every layer (`pand`, `lod12_2d`, `lod13_2d`, `lod22_2d`, `lod12_3d`,
  `lod13_3d`, `lod22_3d`) and every field kept as `v/<release>/nl/<layer>-<batch>.parquet`, reprojected from RD New to WGS84
  (z stays NAP metres), plus `tile_id` and `fid` (3DBAG's per-tile row key). The tiles draw the LoD1.2 roof parts with their
  building's fields, height = `b3_h_70p - b3_h_maaiveld`. Overture's Netherlands buildings stay in the Overture regions, so the
  Netherlands appears in both.
- Height check only (not published): Wikidata (CC0). Coverage cut only (not published): GISCO country outlines,
  © EuroGeographics for the administrative boundaries.

Provided as is, with no warranty, and not endorsed by any of the sources.
