# Consensus

This document explains what validators check, why the rule is shaped this way, and what it cannot catch.

## One rule

The write method reads two public endpoints and computes integers from them. The leader does that work and returns a JSON object. The validator repeats the whole thing from its own two fetches and compares:

```python
return proposed == checked
```

That equality covers every field of the record: `station_max_tenths`, `model_max_tenths`, `station_hours`, `delta_tenths`, `tolerance_tenths`, `verdict`, `station_source`, `model_source`, `day_basis`, `lat`, `lon`, and `day`. There is no field the leader contributes alone, and no aggregation across nodes. A difference anywhere means rejection.

This shape was chosen for a specific reason. A validator that only inspects the leader's output commits nothing of its own, so any leader field that validators do not re-derive is a place where the stored record can be better than the checked one. An earlier contract of mine had exactly that defect and was rejected for it. Here the validator reaches its own conclusions and then compares.

## Why there is no model

Each source reduces to one integer: the maximum hourly `temperature` over the UTC day for the station series, and `daily.temperature_2m_max[0]` for the model. The delta is a subtraction of two integers and the verdict is one comparison against the caller's tolerance.

There is nothing for a language model to interpret, and putting one in the loop would only add a source of disagreement between nodes. The hard part of this contract is agreeing on two simultaneous web fetches, not reading two numbers.

## Determinism boundaries

| Value | Deterministic | Under consensus |
| --- | --- | --- |
| HTTP response bodies | no | yes, through equality of every derived field |
| the two daily maxima | given the bodies | yes |
| station hour count | given the body | yes |
| delta and verdict | yes, integer arithmetic | yes |
| source names and day basis | yes, constants | yes |
| whether a source has data for the day | no | yes, because missing data reverts |

Temperatures are converted to tenths with `"%.1f"` before any comparison, so no floating point value ever reaches stored state. Hour counts and deltas are plain integers.

## What this rule stops

| Attempted by a leader | Stopped by |
| --- | --- |
| Report a station value that the station did not publish | full equality against the validator's own fetch |
| Use the model value twice and call the gap zero | full equality |
| Flip the verdict while keeping both numbers | full equality |
| Swap the sources, for example report ERA5 as the station series | both source names are in the comparison |
| Hide a partial day, for example 9 observed hours | the hour count is stored and compared, and fewer than 20 hours reverts |
| Answer for a different day | the model day is checked, and `day` is part of the comparison |
| Return a stale record from an earlier call | every field is recomputed from fresh fetches |
| Write a partial record when validators disagree | state is written only after `run_nondet` returns |

Four of these are exercised by tests: a tampered station value, a flipped verdict, a changed source name, and a failed leader.

## What it cannot catch

Validity is not correctness. If both publishers are wrong in the same direction, the record faithfully stores agreement on a wrong pair of numbers. The contract proves what the sources said, not what the weather was.

Agreement between the two sources does not mean they are independent in the way a statistics user might assume. The station series is a measurement, the model is a reanalysis that ingests station observations among other inputs, so the two are related at the source. A small delta is therefore weaker evidence than it looks, and a large delta is the more informative case.

The station is not pinned in the record. Bright Sky picks a source near the coordinate that has data for the requested day, and the record stores only the hour count. Re-running the same call months later can select a different station and produce a slightly different pair, with no way to tell from the record alone. Pinning the station identifier would have been the stronger design, and it is the first thing to add in a future version.

Retention is outside the contract. Bright Sky and Open-Meteo are free services without guarantees, and their archives do not go back indefinitely in every configuration. A day that answers today may not answer in five years, which limits how far a record can be independently re-checked.

Revisions are not tracked. If either publisher revises a value, an old record keeps the old numbers and the key cannot be written again. The record is a snapshot with named sources, not a live mirror.

Both validators and leader fetch at slightly different moments. For archived days this is harmless because the series is fixed. If a source is updated between two fetches inside one consensus round, the validator rejects and the transaction reverts rather than storing a mixture. That is fail safe, not a fix.
