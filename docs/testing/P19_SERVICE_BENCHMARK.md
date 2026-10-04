# P19 service baseline

Status: **final offline service structure verified**. Full-scale licensed-data
performance and Rust speed claims remain unverified. The three-repetition timing
checkpoint and final structural rerun below have separate source identities.

The measured workload is the deterministic `prepare_offline_demo` fixture:
20 securities × 180 synthetic weekday sessions (3,600 price rows), three factor
definitions, a frozen raw-price snapshot, and a real isolated SQLite project.
Each repetition creates a new database and registers its user, membership and
snapshot before calling `evaluate_factor_batch`. The runner performs the actual
Python calculations and saves returns, factor values and research cards.

Earlier Windows 11 / AMD64 / CPython 3.12.14 checkpoint, three repetitions:

| Phase | p50 seconds | p95 seconds | Panel reads | Factor calculations |
| --- | ---: | ---: | ---: | ---: |
| Cold batch, three factors | 19.156 | 19.208 | 1 | 3 |
| Identical immutable retry | 0.158 | 0.165 | 0 | 0 |

Peak sampled process RSS was 218,243,072 bytes (10 ms sampling). Every repetition
verified all nine saved artifacts by the real project-scoped read service, and
the retry retained identical result payloads, artifact IDs and content hashes.
Independent function instrumentation agreed with service diagnostics. A separate
untimed check changed fees: the evaluation hash changed and computation ran
again; reverting to the original configuration reused the intact original result.
There were no provider requests.

The complete measurements, source code identity, dependency versions/lock hash,
snapshot hashes, I/O counts and per-repetition integrity results are in
[performance-service-before-boundary-fixes-2026-10-05.json](performance-service-before-boundary-fixes-2026-10-05.json).
The source was frozen during measurement; its runtime hash was checked before
and after. The checkout had uncommitted changes, so the recorded source digest
is the exact engine identity; the recorded Git HEAD alone does not identify it.
This is the IC-window-repaired checkpoint before the final warmup access,
legacy strategy permission and obsolete-worker publication repairs. Timing data
from another source identity must not be pooled with these samples.

```powershell
.venv312/Scripts/python.exe scripts/benchmark_research.py --mode service --repeats 3 --label p19-service-synthetic --output benchmark-results/service.json
```

Cold means an empty evaluation database, not an emptied operating-system disk
cache. Schema creation, initial fixture preparation and artifact readback are
outside the timed service interval. This was a development workstation, not an
isolated performance lab, and three samples are a small descriptive baseline.
The retry performs a different amount of work by reusing immutable evidence;
the result is not evidence of a faster numerical algorithm. Python correctness
rests on separate golden/regression tests. The old incorrect parser baseline is
not a numerical oracle. No Rust speedup, real-data scale target, Linux runtime,
PostgreSQL concurrency or external-provider throughput is claimed here.

The first service attempt correctly failed because IC output included warmup
observations outside the registered evaluation window. The calculation module
was repaired and tested before these successful measurements. The card boundary
check was retained. The CI workflow now executes one service repetition and the
same identity/read/compute/invalidation assertions on each configured platform;
configuration of that job does not itself establish a Linux pass.

After the final access-window, legacy-project authorization, obsolete-worker
fencing, runtime-dependency identity repairs and final type-check corrections, a new one-repetition structural
run passed on the final frozen source. Its complete record is
[performance-service-2026-10-05.json](performance-service-2026-10-05.json).
It again observed one panel read/three calculations for the cold batch, zero/zero
for identical retry, nine identical verified artifacts, and successful changed-cost
invalidation with the original results retained. Source identity was stable and
now also binds installed numerical dependency versions, platform and lock digest.

Full pytest and browser E2E validation were allowed concurrently during this last
run; CPU load was not isolated. Its single recorded elapsed time is diagnostic
only, not a new performance baseline or a p95 distribution estimate. No speedup
claim is derived from that final structural run, and it is not pooled with the
earlier three samples.

```powershell
.venv312/Scripts/python.exe scripts/benchmark_research.py --mode service --repeats 1 --label p19-service-post-boundary-and-type-fix-structural-check --output docs/testing/performance-service-2026-10-05.json
```
