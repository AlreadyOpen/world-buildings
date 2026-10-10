# world-skylines

Every building in the world with a known height of 30 m or more, as 3D-ready vector tiles for zooms 6-13, rebuilt for each
[Overture Maps](https://overturemaps.org) release and published for browsers to read in place.

Overture's own `buildings.pmtiles` carries heights only at zoom 14, so from further out every city is flat. This fills that gap.
Official building data replaces Overture inside its country: Hong Kong from
[AlreadyOpen/hk-buildings](https://huggingface.co/datasets/alreadyopen/hk-buildings), the Netherlands from [3DBAG](https://3dbag.nl).

Part of [AlreadyOpen](https://github.com/AlreadyOpen): open, re-runnable pipelines over public data.

| Output | Where | Format |
|---|---|---|
| Tall buildings with footprints, one file per source | [huggingface.co/datasets/alreadyopen/world-skylines](https://huggingface.co/datasets/alreadyopen/world-skylines) | GeoParquet (ZSTD) |
| Vector tiles z6-13, layer `skylines` (`height`, `min_height`, `name`), one file per source | [huggingface.co/buckets/alreadyopen/world-skylines](https://huggingface.co/buckets/alreadyopen/world-skylines) | PMTiles |
| Release, row counts per file, sha256 per output, sources | both, `manifest.json` | JSON |

| The same files, mirrored | [kaggle.com/datasets/helenkwok/world-skylines](https://www.kaggle.com/datasets/helenkwok/world-skylines) | uploaded after each publish |

Both Hugging Face locations answer CORS and HTTP Range requests (MapLibre with the `pmtiles` protocol; DuckDB for the Parquet).

## Run it

```bash
pip install -r requirements.txt          # plus tippecanoe (apt install tippecanoe / brew install tippecanoe)
python skylines.py extract               # reads only height + bbox of all ~2.5 billion buildings, then footprints of the tall ones
python skylines.py build                 # -> data/out/<source>.parquet, <source>.pmtiles, build.json
python skylines.py check                 # fails if an Overture building is left inside an official source's country
python publish.py                        # -> Hugging Face (needs HF_TOKEN); refuses if a count drops > 10 % (manifest.py)
```

`--files 00058,00377` limits `extract` to some of Overture's 512 files (a quick test); `SKY_DATA` moves the data folder.
The GitHub Action `.github/workflows/bake.yml` checks weekly and rebuilds when Overture publishes a new release. Overture
deletes each release about 60 days after publishing it, so the release is always looked up in its STAC catalogue, never fixed.

## Height checks (Overture only)

OpenStreetMap heights include errors (an 800 m chapel, an 816 m villa). A building is moved to `rejected.parquet`, with its
reason, when its height divided by its floor count (top minus base, since a building part's floors cover only its own tier) is over 12 m (`floors`), or when
a whole building is 300 m or taller without a plausible 2.5-6 m storey (`wikidata`), unless a Wikidata building or tower within 300 m has a height within 15 % of it. Official
sources are taken as published. Rejected buildings stay listed so they can be fixed upstream.

## Sources and terms

- Overture Maps Foundation, buildings theme (buildings and building parts: towers such as Burj Khalifa carry their height only on their parts), [ODbL-1.0](https://opendatacommons.org/licenses/odbl/1-0/) (includes OpenStreetMap
  data). Attribution: Overture Maps Foundation; © OpenStreetMap contributors. The `overture.*` outputs are a derived database
  and are therefore also ODbL-1.0.
- Official sources keep their own terms and are published as separate files, never merged into the ODbL files:
  Hong Kong (`hk.*`): Lands Department via the CSDI Portal, [CSDI Terms of Use](https://portal.csdi.gov.hk/csdi-webpage/doc/TNC).
  Netherlands (`nl.*`): © 3DBAG by tudelft3d and 3DGI, [CC BY 4.0](https://docs.3dbag.nl/en/copyright/), read from its WFS
  with a server-side filter; changed: height = `b3_h_70p - b3_h_maaiveld` (70th-percentile roof minus ground), under 30 m left out.
- Country outlines (for official coverage): Eurostat GISCO, © EuroGeographics for the administrative boundaries.
- Height check: Wikidata (CC0). Declared sources with licences: `sources.json`; CI checks them (`check.py`).

Outputs are provided as is, with no warranty, and are not endorsed by any source.

Code: Apache-2.0.
