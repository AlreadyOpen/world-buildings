# world-skylines

Every building in the world with a known height of 30 m or more, as 3D-ready vector tiles for zooms 6-13, rebuilt for each
[Overture Maps](https://overturemaps.org) release and published for browsers to read in place.

Overture's own `buildings.pmtiles` carries heights only at zoom 14, so from further out every city is flat. This fills that gap.
Official government building data replaces Overture inside its country (Hong Kong first, from
[AlreadyOpen/hk-buildings](https://huggingface.co/datasets/alreadyopen/hk-buildings) once published).

Part of [AlreadyOpen](https://github.com/AlreadyOpen): open, re-runnable pipelines over public data.

| Output | Where | Format |
|---|---|---|
| Tall buildings with footprints, one file per source | [huggingface.co/datasets/alreadyopen/world-skylines](https://huggingface.co/datasets/alreadyopen/world-skylines) | GeoParquet (ZSTD) |
| Vector tiles z6-13, layer `skylines` (`height`, `min_height`, `name`), one file per source | [huggingface.co/buckets/alreadyopen/world-skylines](https://huggingface.co/buckets/alreadyopen/world-skylines) | PMTiles |
| Sources, licences, row counts, sha256 per output | both, `sources.json` | JSON |

Both answer CORS and HTTP Range requests (MapLibre with the `pmtiles` protocol; DuckDB for the Parquet).

## Run it

```bash
pip install -r requirements.txt          # plus tippecanoe (apt install tippecanoe / brew install tippecanoe)
python skylines.py extract               # reads only height + bbox of all ~2.5 billion buildings, then footprints of the tall ones
python skylines.py build                 # -> data/out/<source>.parquet, <source>.pmtiles, sources.json
python skylines.py check                 # fails if an Overture building is left inside an official source's country
python publish.py                        # -> Hugging Face (needs HF_TOKEN)
```

`--files 00058,00377` limits `extract` to some of Overture's 512 files (a quick test); `SKY_DATA` moves the data folder.
The GitHub Action `.github/workflows/bake.yml` checks weekly and rebuilds when Overture publishes a new release. Overture
deletes each release about 60 days after publishing it, so the release is always looked up in its STAC catalogue, never fixed.

## Height checks (Overture only)

OpenStreetMap heights include errors (an 800 m chapel, an 816 m villa). A building is moved to `rejected.parquet`, with its
reason, when its height divided by its floor count is over 8 m (`floors`), or when it is 300 m or taller without a plausible
2.5-6 m storey (`wikidata`), unless a Wikidata building or tower within 300 m has a height within 15 % of it. Official
sources are taken as published. Rejected buildings stay listed so they can be fixed upstream.

## Sources and terms

- Overture Maps Foundation, buildings theme, [ODbL-1.0](https://opendatacommons.org/licenses/odbl/1-0/) (includes OpenStreetMap
  data). Attribution: Overture Maps Foundation; © OpenStreetMap contributors. The `overture.*` outputs are a derived database
  and are therefore also ODbL-1.0.
- Official sources keep their own terms and are published as separate files, never merged into the ODbL files:
  Hong Kong (`hk.*`): Lands Department via the CSDI Portal, [CSDI Terms of Use](https://portal.csdi.gov.hk/csdi-webpage/doc/TNC).
- Country outlines (for official coverage): Eurostat GISCO, © EuroGeographics for the administrative boundaries.

Outputs are provided as is, with no warranty, and are not endorsed by any source.

Code: Apache-2.0.
