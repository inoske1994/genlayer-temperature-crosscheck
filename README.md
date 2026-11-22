# TemperatureCrossCheck

A GenLayer Intelligent Contract that records the daily maximum temperature for one coordinate from two independent publishers, the distance between them, and a verdict that code derives from a tolerance the caller supplies.

## What it records

Call `record` with a coordinate, a UTC day, and a tolerance in tenths of a degree. The contract fetches both sources, reduces each to one integer, and stores the pair:

```json
{
  "assessment": {
    "day": "2026-08-01",
    "day_basis": "UTC calendar day",
    "delta_tenths": 35,
    "lat": "50.11",
    "lon": "8.68",
    "model_max_tenths": 252,
    "model_source": "open-meteo:archive:era5",
    "station_hours": 24,
    "station_max_tenths": 287,
    "station_source": "brightsky:dwd",
    "tolerance_tenths": 15,
    "verdict": "DIVERGE"
  },
  "id": "1",
  "record_key": "50.11|8.68|2026-08-01|15"
}
```

Read it as: at that coordinate, on that UTC day, the DWD station series reached 28.7 degrees and the ERA5 archive reached 25.2, so the two publishers were 3.5 degrees apart. With a tolerance of 1.5 degrees, the verdict is `DIVERGE`.

| Verdict | Rule |
| --- | --- |
| `AGREE` | `delta_tenths <= tolerance_tenths` |
| `DIVERGE` | `delta_tenths > tolerance_tenths` |

The tolerance belongs to the caller. The contract does not decide what an acceptable distance is, it only measures and records it.

## The two publishers

| Source | What it is | Endpoint |
| --- | --- | --- |
| `brightsky:dwd` | Hourly station observations from the German weather service, served by Bright Sky | `api.brightsky.dev/weather` |
| `open-meteo:archive:era5` | The ERA5 reanalysis archive, served by Open-Meteo | `archive-api.open-meteo.com/v1/archive` |

These are not two copies of the same feed. One is a measurement at a physical station, the other is a global model that interpolates a grid cell to the coordinate. That is exactly why their difference is interesting: an agreement written on a station number and settled on model output takes on a distance that nobody measures until the day it matters.

Both are free public services with no key, which also means no service level agreement. If either is unavailable, the call reverts rather than storing a guess.

## Why a contract is useful here

Any settlement that depends on weather has to pick one number and live with it. The choice of dataset is usually invisible in the agreement, and the gap between the candidate datasets is the basis risk that one side quietly carries.

This contract does not resolve that gap, and it does not pay anybody. It makes the gap a public, timestamped, reproducible fact: these two publishers said these two numbers for this coordinate and this day, and validators agreed on the record.

Uses that outlive a demo: parametric agreements that want to document which source they settle on and how far the alternative was, tenders and audits that need a defensible number years later, and data quality work that tracks when a model and a station start to drift apart.

## Consensus design

There is no language model in this contract. Every stored value is arithmetic over two HTTP responses, so the only non-deterministic step is the fetching itself.

The write method runs inside `gl.vm.run_nondet`. The leader fetches both endpoints, parses them, computes every field, and returns a JSON object. The validator fetches both endpoints itself, runs the same computation, and accepts only on full equality:

```python
def validator_fn(leaders_res) -> bool:
    if not isinstance(leaders_res, gl.vm.Return):
        return False
    try:
        proposed = json.loads(leaders_res.calldata)
        checked = json.loads(leader_fn())
    except Exception:
        return False
    if not isinstance(proposed, dict):
        return False
    return proposed == checked
```

Every field is covered by that comparison: both temperatures, the station hour count, the delta, the tolerance, the verdict, both source names, the day basis, and the canonical coordinate strings. There is no field the leader decides alone, which was the failure mode in an earlier project of mine and the reason it was rejected.

State changes only after agreement. Five paths revert instead of writing: a host answering with an error, a payload that is not JSON, a station with fewer than 20 observations for the day, a station series with no row for that day, and a model answer that names a different day. A record is never half written, and a gap in the data never turns into an `AGREE`.

`docs/CONSENSUS.md` covers the rule, what it stops, and what it cannot catch.

## Live runs on Studionet

Chain `61999`, contract [`0xdA4F446Fe8D17a45a2ACcC9e237d91e53d1bA9bE`](https://explorer-studio.genlayer.com/address/0xdA4F446Fe8D17a45a2ACcC9e237d91e53d1bA9bE), deployer `0x264465Ee4bCe6E6ba6be6c976dE8CfccA2148B09`. Every call used 0 GEN in execution mode `Normal (Full Consensus)`.

| Step | Transaction | Consensus | Result |
| --- | --- | --- | --- |
| Deploy | [`0x60098650...f537b2`](https://explorer-studio.genlayer.com/tx/0x600986508b3cc5538a1de70551c63be0aa96ec1387e4be34f68ad62354f537b2) | Finalized, 5 of 5 agree, rotation 0 | contract created |
| `record` 2026-08-01, tolerance 15 | [`0x3909640a...8ef82245`](https://explorer-studio.genlayer.com/tx/0x3909640a92dd4eeffa2ee3db1a5baf8187b911de5ff02671346e82ac8ef82245) | Finalized, rotation 0, all validators agree | record `1`, station 287, model 252, delta 35, `DIVERGE` |
| `record` 2026-09-01, tolerance 15 | [`0xbbdc12d6...ce0cecae5`](https://explorer-studio.genlayer.com/tx/0xbbdc12d65e6cbfc508a7cfecda2bf95080aa6a75229b1d0347f44d9900cecae5) | Finalized, rotation 0, all validators agree | record `2`, station 239, model 238, delta 1, `AGREE` |
| `record` 2026-08-01, tolerance 40 | [`0x34487e30...739d18dc1`](https://explorer-studio.genlayer.com/tx/0x34487e30caa39bedf94731b5a8b1f9a4654f5adc372a83651d6b727339d18dc1) | Finalized, rotation 0, all validators agree | record `3`, station 287, model 252, delta 35, `AGREE` |

The third call is the useful one to read next to the first. Same coordinate, same day, same two temperatures, and a different verdict, because the caller changed the tolerance from 15 to 40. The measurement did not move. The decision did.

Before deploying, the same three cases were computed off chain against both live endpoints by a separate script. The values matched the on chain records exactly, tenth by tenth.

## Deploy and call

1. In [Studio](https://studio.genlayer.com), paste `contracts/temperature_crosscheck.py` and deploy with `Normal (Full Consensus)`.
2. Call `record` with `50.11`, `8.68`, `2026-08-01`, `15`. It returns `1` after roughly 20 to 60 seconds.
3. Read `get_record("1")`, `count()`, or `get_key(0)`.

Arguments:

| Argument | Rule |
| --- | --- |
| `lat` | String with exactly two decimals, between `-90.00` and `90.00` |
| `lon` | String with exactly two decimals, between `-180.00` and `180.00` |
| `day` | `YYYY-MM-DD`, a real calendar date from year 2000 onward |
| `tolerance_tenths` | Integer from 0 to 200, where 10 means one degree Celsius |

Coordinates are parsed into hundredths, validated, and rebuilt into a canonical string before the URLs are made, so `50.1` is rejected rather than silently rounded to `50.10`. The record key is the coordinate, the day and the tolerance together, so the same pair can be read against a stricter or looser tolerance without overwriting anything.

## Tests

```
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest tests/direct -q
```

19 direct mode tests, all passing on the code that is deployed. They cover the agreeing case, the diverging case, the inclusive tolerance boundary, zero tolerance, negative temperatures, negative coordinates, five failure paths, seven input validations, the duplicate rule, the two views, and four validator rejections including a tampered station value, a flipped verdict and a changed source name.

Direct mode replaces both endpoints with mock payloads. It proves the contract logic and the validator rule, not that five validators agree. The live table above is the evidence for the network side, and the two kinds of evidence are kept separate on purpose.

## What this does not prove

- It does not prove that either publisher is right. It proves that both were read and that validators agree on what they said.
- The day is a UTC calendar day, not a local one. For a German coordinate in summer that is 02:00 to 01:59 local time, so this daily maximum can differ from the figure a local day would give. UTC was chosen because both endpoints then aggregate the same window exactly.
- The record does not name the station that answered. Bright Sky selects a nearby station with data for that day, and the record only counts the hours it returned. A later re-fetch can pick a different nearby station and return a slightly different value.
- ERA5 is a reanalysis, not a measurement and not a forecast. It interpolates a grid cell to the coordinate, so part of the delta is model structure rather than station error.
- Both datasets are revised over time. A record keeps the numbers that were published when it was written, and the same key cannot be written twice. The record is a snapshot, not a mirror.
- Quality flags are not read. A station hour is counted if a temperature exists, without asking whether DWD flagged it as suspect.
- It is not an insurance product, does not settle payments, and does not decide what tolerance is fair.

More detail, including what the consensus rule cannot catch, is in [docs/CONSENSUS.md](docs/CONSENSUS.md).

## Layout

```
contracts/temperature_crosscheck.py   the contract, one file, only json imported
tests/direct/                         19 direct mode tests
docs/CONSENSUS.md                     consensus rule, threat table, and its limits
requirements-dev.txt                  pinned test toolchain
```

## Data

Station observations come from the German weather service (DWD) through Bright Sky, which serves them as JSON. The model side is the ERA5 reanalysis through the Open-Meteo archive API, queried with `timezone=UTC` and `daily=temperature_2m_max`. Both are free public services, and the record stores which source produced which number so the same two queries can be repeated.

MIT licensed.
