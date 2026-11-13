# Code Review — model-manager

| Field | Value |
|---|---|
| Date | 2026-11-08 |
| Reviewed commit | `85cc578` (working tree clean) |
| Version | 1.1.10 |
| Scope | Whole repository: `src/` (11,472 lines), `tests/` (5,847 lines, 290 tests), `scripts/`, repo-root artifacts |
| Review method | Five-axis review (correctness, readability, architecture, security, performance) with executed repros, not just static reading |
| Verdict | **Request changes** (2 Critical, 5 Required, 4 Structural, 7 Optional) |

## Verification story

```
$ python -m pytest -q
310 passed, 2 failed, 41 warnings in 6.24s
```

The two failures are not flaky. Investigating them exposed the highest-severity issue in the repository (see C1). Findings marked "Reproduced" were confirmed by running the code; the rest were confirmed by reading the call paths and checking tests/callers that reach them.

---

## Summary

| ID | Severity | Finding | Area |
|---|---|---|---|
| C1 | Critical | Test suite writes to the user's real data directory and calls the live AA API | Tests |
| C2 | Critical | Corrupt `models.json` is silently replaced with an empty library (data loss) | Storage |
| R1 | Required | `models remove` exits 0 when the model is missing | CLI correctness |
| R2 | Required | `providers fetch-all` / `scan-all` error handling is unreachable; failures exit 0 | CLI correctness |
| R3 | Required | `scores fetch` crawls the same paginated endpoint twice per run | Performance / architecture |
| R4 | Required | Five network calls have no timeout | Robustness |
| R5 | Required | `save_config` emits invalid TOML for strings containing `"`, `\`, or newlines | Config correctness |
| S1 | Structural | `scripts/sync_aa_scores.py` is a drifted fork of the ingestion logic | Duplication |
| S2 | Structural | Tracked scratch artifacts and one-off scripts at repo root | Repo hygiene |
| S3 | Structural | Oversized files: `radar.py` (1,240), `dashboard.py` (961), `cli/models.py` (775) | Maintainability |
| S4 | Structural | `variant update` performs two load-modify-write cycles (partial updates) | Correctness / design |
| N1 | Nit | 18 `utcnow()` call sites → 41 deprecation warnings | Maintenance |
| N2 | Nit | 88 `except Exception`, 13 bare `pass`; two silently discard ledger failures | Observability |
| N3 | Nit | Broad swallow in `scores.py:201, 214` hides permission/schema errors | Observability |
| N4 | Nit | Unused `config` parameter in `save_free_models` | Readability |
| N5 | Nit | Empty payload treated as success; can overwrite scores with zero models | Correctness (edge) |
| N6 | Nit | `dashboard.py:480` interpolates score values without `html.escape` | Security (low) |
| N7 | Optional | Plaintext secret export; stale `keyring_dump.env` present on this machine | Security |

---

## Critical

### C1. Tests mutate real user data and hit the live API

**Severity:** Critical — data corruption in normal developer workflow
**Location:** `tests/test_cli.py:23-38`

`test_scores_fetch_no_key` and `test_scores_sync_no_file` invoke the CLI with no isolated config. Two ambient dependencies make them non-hermetic:

1. `~/.config/model-manager/config.toml` pins `data_dir = "/home/castor/.local/share/model-manager"`, so `load_config(None)` resolves real state.
2. `auth.get_secret()` (`domain/auth.py:10-22`) falls back to the OS keyring, so deleting `ARTIFICIAL_ANALYSIS_API_KEY` from `os.environ` does not remove the key.

**Reproduced.** A full-suite run rewrote three production files:

```
aa_raw_response.json  1,059,287 bytes  2026-11-08 14:13:49
model_scores.json       194,109 bytes  2026-11-08 14:13:49
models.json              36,272 bytes  2026-11-08 14:13:50
```

`pytest` performed a live HTTP crawl of `artificialanalysis.ai` and rewrote the model library. The test asserts `exit_code == 1`, but the command exits 0 because the fetch succeeds — so the test "passes" on a machine without credentials for the wrong reason, and corrupts live state on a machine with them.

**Recommended fix:**

- Add an autouse fixture in `tests/conftest.py` that redirects `XDG_CONFIG_HOME` and `XDG_DATA_HOME` to `tmp_path`, and monkeypatches `model_manager.domain.auth.get_secret` to return `None` unless a test opts in.
- Assert inside the fixture that `model_manager.config.find_config()` does not resolve to a path outside `tmp_path`, so the protection cannot silently regress.
- Audit the other ~98 `runner.invoke` call sites. `test_prices.py:65-67` and `test_tags.py:301-304` already use the correct pattern (write a `config.toml` in `tmp_path` and pass `-c`); apply it uniformly.

**Acceptance criteria:** running `pytest` with a populated real config, a real keychain entry, and no network access produces the same result and leaves file mtimes under `~/.local/share/model-manager` unchanged.

---

### C2. Corrupt `models.json` is silently replaced with an empty library

**Severity:** Critical — one-way data loss
**Location:** `src/model_manager/domain/storage.py:8-21`, plus every write path

`load_models_data` returns an empty document on `JSONDecodeError`:

```python
except json.JSONDecodeError:
    return {"meta": {}, "models": {}}
```

Every caller then appends to that empty document and saves it, destroying the previous contents.

**Reproduced:**

```python
# models.json = '{"models": {"keep-me": {"variants": {}}}'   (truncated)
models.add_model(cfg, "new-one")
# → models.json now contains ONLY "new-one"; "keep-me" is gone
```

Writes are also non-atomic (`Path.write_text` in `storage.py:20`; `open(..., "w")` in `discovery.py:428`, `scores.py:58/152/188`). A crash or kill mid-write creates exactly the corruption that then erases the rest. `domain/models.py` alone has ~15 load-modify-save sites.

**Recommended fix:**

1. Write atomically: temp file in the same directory, `flush`/`fsync`, then `os.replace`.
2. On `JSONDecodeError`, do not pretend the file is empty. Either raise a `StorageError` that callers must handle, or move the bad file to `models.json.corrupt-<timestamp>` and refuse to write over it.
3. Apply the same policy to the other JSON state files (blocks ledger, reference sets, score caches). `scores.py:173` and `scores.py:230` already raise on corruption — use that as the template.

**Acceptance criteria:** a test that truncates `models.json`, runs a mutating command, and asserts the command fails loudly, the corrupt file is preserved under a `.corrupt-*` name, and unrelated models are still recoverable.

---

## Required

### R1. `models remove` exits 0 when the model is missing

**Severity:** Required — automation cannot detect failure
**Location:** `src/model_manager/cli/models.py:539-545`

The JSON branch raises `typer.Exit(1)`; the text branch only prints.

**Reproduced:**

```
$ model-manager models remove nope-xyz -c /tmp/mm-review.toml
Error: Conceptual model nope-xyz not found.
exit: 0
```

**Fix:** move `raise typer.Exit(1)` below the `if json_output` block so both branches fail. Every other command in `cli/models.py` and `cli/reference_sets.py` already does this; this is the single outlier.

---

### R2. `providers fetch-all` / `scan-all` can never fail

**Severity:** Required — dead error handling, false success
**Location:** `src/model_manager/cli/common.py:69-70`; `src/model_manager/cli/providers.py:63-73, 90-100`

`_run_discovery_cli_workflow` and `_run_scan_cli_workflow` catch `Exception`, print, and return normally. The `*-all` commands wrap those calls in `try/except` and collect `errors`, but the except block is unreachable, so `raise typer.Exit(1)` never fires. If every provider fails, the command still exits 0.

**Fix:** let the workflows re-raise (or return a success flag and a list of errors) and keep the existing aggregation in the `*-all` commands.

**Acceptance criteria:** a test that monkeypatches `providers.run_discovery_workflow` to raise, invokes `providers fetch-all`, and asserts `exit_code == 1` with the provider named in the output.

---

### R3. `scores fetch` crawls the same paginated endpoint twice per run

**Severity:** Required — doubled request volume, inconsistent snapshots, removable code
**Location:** `src/model_manager/cli/scores.py:30,40`; `src/model_manager/domain/scores.py:21-59, 61-91, 155-189`; `src/model_manager/domain/schedule.py:397-398`

`fetch_aa_data` walks every page of `/api/v2/language/models/free`. Immediately afterwards, `merge_agentic_scores` calls `fetch_agentic_index_from_api`, which walks every page of the same endpoint again. The same pair runs in the scheduled pipeline and in `models discover --refresh`.

The data is already in the first payload. `process_aa_data` iterates `m["evaluations"]` but reads `agentic` from a separately passed map instead of:

```python
evals.get("artificial_analysis_agentic_index")
```

**Recommended fix:** read the agentic value directly in `process_aa_data`, then delete `fetch_agentic_index_from_api` and `merge_agentic_scores` (or reduce the latter to a migration helper). This removes ~60 lines and one full crawl per run.

**Evidence it is also currently doing zero work:** the saved live payload has the field present for 726 models and non-null for none (`artificial_analysis_agentic_index` is documented as always null on the free tier). The second crawl merges nothing.

---

### R4. Five network calls have no timeout

**Severity:** Required — CLI can hang indefinitely
**Location:** `src/model_manager/domain/discovery.py:58, 130, 207, 231, 265`

These `urllib.request.urlopen(req)` calls omit `timeout=`. Every other fetch in the codebase pins 10–30 seconds (`discovery.py:176,340`, `scores.py:41,78`, `prices.py:47`, `cost_map.py:15`, `litellm_scan.py:96,209`). A stalled TCP connect blocks `providers fetch-all`, `models discover --refresh`, and the scheduled pipeline forever.

**Fix:** add `timeout=` uniformly, ideally a shared constant or helper so future fetches cannot forget it.

---

### R5. `save_config` emits invalid TOML for special characters

**Severity:** Required — written config may be unreadable on next start
**Location:** `src/model_manager/config.py:183-193`

Strings are wrapped in quotes without escaping.

**Reproduced:**

```python
_format_toml_val('a"b')   # → "a"b"
tomllib.loads('k = "a"b"')  # TOMLDecodeError: Expected newline or end of document
```

Any `api_base`, path, or provider value containing a quote, backslash, or newline produces a config the app cannot load. The writer is reached by `init`, `schedule install/remove`, and any command that persists config.

**Fix:** serialise the string case with `json.dumps` (TOML basic strings share JSON escaping for these characters) or use a real TOML writer.

**Acceptance criteria:** round-trip property test — arbitrary strings (quotes, backslashes, tabs, newlines, Unicode) survive `save_config` → `load_config`.

---

## Structural

### S1. `scripts/sync_aa_scores.py` is a drifted fork

**Location:** `scripts/sync_aa_scores.py`

It duplicates `fetch_aa_data` / `process_aa_data` (`:24-117`) and has already diverged from `domain/scores.py`:

- writes to a hardcoded `./data` (`:9-11`), which `.context/lessons.md` (2026-11-03) records as legacy — the CLI reads `platformdirs.user_data_dir`;
- reads the API key from `.env` (`:15-22`), bypassing the keyring/env-first path in `domain/auth.py`;
- omits the `performance`-nested `ttft`/`tps` extraction entirely, so its output lacks metrics the app now requires;
- scrapes the agentic index from a Next.js HTML blob with regex (`:56-81`), which is brittle and unrelated to the API path.

No module, test, or document references it.

**Fix:** delete it, or reduce it to a thin wrapper that calls `model_manager.domain.scores`. Do not keep two implementations of the same ingestion contract.

---

### S2. Tracked scratch artifacts and one-off scripts

**Location:** repo root

| File | Size | Status |
|---|---|---|
| `fetch_test.json` | 20 KB | unreferenced |
| `scan_test.json` | 100 KB | unreferenced |
| `scores_basic.json` | 136 KB | unreferenced |
| `scores_filtered.json` | 4 KB | unreferenced |
| `scores_selected.json` | 4 KB | unreferenced |
| `scores_sorted.json` | 112 KB | unreferenced |
| `analyze_models.py` | — | hardcoded `/home/castor/...`, reads legacy `model_aliases.json` |
| `fuzzy_analyze.py` | — | same |
| `fuzzy_analyze_2_3.py` | — | same |

None are referenced by code, tests, or docs. ~380 KB of JSON and three machine-specific scripts.

**Action:** confirm removal (per the dead-code rule, do not delete silently) and add `*_test.json` / scratch dumps to `.gitignore` if they keep being produced. `data/` is already ignored.

---

### S3. Oversized files

| File | Lines | Note |
|---|---|---|
| `src/model_manager/radar.py` | 1,240 | ~700 lines are an HTML/CSS/JS f-string template |
| `src/model_manager/dashboard.py` | 961 | same shape |
| `src/model_manager/cli/models.py` | 775 | all model/fallback/variant/tag subcommands |

Not urgent, but this is the "materially grows an already-large file" signal. Extract templates into package data (`model_manager/templates/radar.html`) and split `cli/models.py` by command group. That also makes the JS independently reviewable and lets escaping be tested.

---

### S4. `variant update` performs two load-modify-write cycles

**Location:** `src/model_manager/cli/models.py:225-249`

The command calls `models.update_variant(...)` (load, mutate, save) and then `models.update_variant_limits(...)` (load again, mutate, save). If the second raises `ValueError` — for example an invalid limit value or an unmapped provider — the first change is already persisted. A single command can therefore leave a partial update.

**Fix:** add one domain function that applies inclusion flags and limits in one load/save, and validate before writing.

---

## Nits and optional

### N1. `utcnow()` deprecation

18 call sites, 41 warnings:

- `domain/models.py` ×10 (`:49, 84, 128, 235, 244, 336, 366, 384, 401`)
- `domain/reference_sets.py` ×4 (`:27, 39, 69, 89`)
- `domain/tags.py` ×2 (`:163, 202`)
- `domain/aliases.py:115`
- `domain/scores.py:125`
- `domain/discovery.py:431`

Replace with `datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")`. The output format is unchanged; existing tests should stay green.

### N2. Broad exception handling

88 `except Exception` in `src/`, 13 with a bare `pass`. Most are deliberate provider-resilience. Two are worth changing because they hide a broken subsystem:

- `cli/common.py:44` — silently discards `blocks.record_fetch_observations` failures;
- `cli/common.py:168` — silently discards `blocks.record_assessment_observations` failures.

Log these at verbose/debug level instead of swallowing.

### N3. Over-broad swallow in score reads

`domain/scores.py:201` (`get_scores_for_slug`) and `:214` (`list_all_scores`) use `except Exception: pass` / `return {}`. Narrow to `(json.JSONDecodeError, OSError)`; a permission or schema error should not look like "no scores".

### N4. Unused parameter

`discovery.save_free_models(config, models, path)` never uses `config`. Drop the parameter or use it for `data_dir` derivation instead of the separate `path` argument.

### N5. Empty payload treated as success

`fetch_aa_data` returns `{"data": []}` as a success and `process_aa_data` then rewrites `model_scores.json` with zero models. If the upstream API returns a valid but empty catalog, the dashboard and radar silently go blank until the next fetch. Treat an empty catalog as a failed fetch (do not write).

### N6. Score values interpolated without escaping

`dashboard.py:480` inserts `intel`/`coding` into HTML without `html.escape`. They are normally numbers from AA, but `model_scores.json` is user-editable. Cheap to escape for consistency with the rest of the renderer.

### N7. Plaintext secret export

`domain/service_env.py` exports every `model-manager` keyring entry to a plaintext `0600` file so systemd timers can run without an unlocked login keyring. The tradeoff is deliberate and documented, and the refuse-to-clobber guard (`:89-103`) is good. Two follow-ups:

- there is a stale `~/.config/model-manager/keyring_dump.env` on this machine from a past session — recommend deleting it;
- longer term, prefer `systemd-creds` / `LoadCredential` over a plaintext `EnvironmentFile`.

---

## What is good and should not regress

- **No shell injection surface.** Every `subprocess` call is list-form with `shell=False`; git calls add a timeout and `GIT_TERMINAL_PROMPT=0` (`domain/git.py:26-39`).
- **Safe YAML.** All output uses `yaml.SafeDumper` with a quoting representer (`domain/yaml_gen.py:20-46`); all input uses `safe_load`. No `yaml.load`, no `pickle`, no `eval`.
- **HTML/JS escaping is actually correct.** The dashboard consistently `html.escape`s model/provider text; the radar serialises data with `json.dumps(...).replace("</", "<\\/")` and uses an `esc()` helper for every interpolated string. This is the area reviewers usually find broken, and it is handled.
- **Pagination correctness.** `fetch_aa_data` does not write the raw file when a later page fails, so a partial catalog never replaces a complete one; `merge_agentic_scores` preserves prior values on gaps. Apply the same instinct to `models.json` (C2).
- **Secret handling.** Env-first, keyring second; `export_keyring_to_env` refuses to destroy a good file when the keyring is locked; env file is `0600`.
- **Test volume and structure.** 290 tests over ~11.5k source lines, with working isolation patterns already present in `test_prices.py`, `test_init.py`, and `test_tags.py`. The fix for C1 is to apply an existing pattern, not invent one.
- **Documentation.** `.context/lessons.md` records real operational lessons (endpoint retirement, platformdirs state location, sentinel-key hazards) that materially help future maintenance.

---

## Recommended sequencing

1. **C1** — isolate the test suite. Small, mechanical, unblocks trustworthy runs for everything else.
2. **C2** — atomic writes plus a corruption policy for JSON state. Add the truncation regression test.
3. **R1, R2, R4, R5** — four small, independent fixes with obvious failure tests.
4. **R3** — collapse the double crawl; net deletion, then adjust `test_scores.py` to assert agentic is read from the first payload.
5. **S1, S2** — remove the drifted fork and the scratch artifacts (confirm before deleting).
6. **S4, N1, N2, N3** — incremental cleanups in the modules touched by the above.
7. **S3** — template extraction, scheduled separately since it is mechanical and review-heavy.

Each of C1–R5 should be its own change with its own regression test, per the one-logical-change rule. S1/S2 are deletions and can ride together.

---

## Remediation checklist

- [ ] C1 — autouse isolation fixture; audit remaining `runner.invoke` sites; suite passes offline with real config present
- [ ] C2 — atomic writes; corrupt-file policy; truncation regression test
- [ ] R1 — `models remove` exits 1 in text mode
- [ ] R2 — provider workflows propagate failures; `fetch-all`/`scan-all` exit 1
- [ ] R3 — single AA fetch; remove `fetch_agentic_index_from_api` / `merge_agentic_scores`
- [ ] R4 — timeouts on all `urlopen` calls
- [ ] R5 — TOML string escaping; round-trip property test
- [ ] S1 — delete or thin-wrap `scripts/sync_aa_scores.py`
- [ ] S2 — remove scratch JSON and one-off scripts (after confirmation)
- [ ] S3 — extract radar/dashboard templates; split `cli/models.py`
- [ ] S4 — single-transaction `variant update`
- [ ] N1 — replace `utcnow()` (18 sites)
- [ ] N2 — surface ledger observation failures
- [ ] N3 — narrow score-read exception handling
- [ ] N4 — remove unused `config` parameter
- [ ] N5 — treat empty catalog as a failed fetch
- [ ] N6 — escape score values in dashboard
- [ ] N7 — delete stale `keyring_dump.env`; document `systemd-creds` option
