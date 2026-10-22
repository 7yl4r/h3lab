# Measurement scripts

These produce the **measured** numbers that `docs/DESIGN.md` and
`docs/DECISIONS.md` cite. They are not tests — a test asserts something; a
measurement reports what actually happened, and the docs quote it.

They all read the build's caches, so they need a build to exist:

```bash
PYTHONPATH=src python -m h3lab.run --regional --year 2017 --resolutions 7,8
```

| script | what it measures | cited in |
|---|---|---|
| `measure_sizes.py` | bytes served by the archive, the mosaic cache cost, the implied cost of the full record | `DESIGN.md` §7.1, D-29 |
| `check_mosaics.py` | that every built mosaic opens, matches the analysis grid and has a readable last block | build QA |
| `validate_change.py` | the change layer's flag rate, break-bin distribution and injected-step power | `DESIGN.md` §6.1, D-27 |
| `change_power.py` | the same power, conditioned on how much of the year a cell saw | `DESIGN.md` §6.1, D-27 |
| `harmonics_probe.py` | whether a better seasonal fit (3 harmonics) removes the excess flag rate on real data | `DESIGN.md` §6.1, D-27 |
| `season_check.py` | the regional's wet/dry NDVI against the pilot's, on the pilot's season windows | `DESIGN.md` §3.3, D-28 |
| `check_rookery.py` | the Rookery Bay shard on its own: coverage, breaks, and the NDVI drop across the Irma bin | D-23 |
| `site_budget.py` | the published site's size, the per-file worst case, and how many more years fit under GitHub Pages' limits | `DESIGN.md` §7.1a, D-30 |

Run them with the repository's interpreter and `PYTHONPATH=src`:

```bash
PYTHONPATH=src python tools/measure/measure_sizes.py
```

`season_check.py` additionally needs the tracked pilot bundle
`data/rookery_bay_h3_res8.bin.gz`; it reports a skip if that file is absent.
