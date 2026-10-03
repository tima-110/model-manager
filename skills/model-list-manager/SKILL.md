---
name: model-list-manager
description: >
  Manage the model-manager conceptual library (models.json): add new models
  or versions, pick AA slugs, define variants, map provider IDs, remove
  models, enable/disable variants for LiteLLM, and (re)tag tiers. Use this
  skill whenever the user mentions adding a model, new model version, DeepSeek,
  discover, AA slug, variant, provider mapping, removing a model, enabling or
  disabling a variant, retagging, or asks whether the model list is missing
  anything — even if they don't mention model-manager. Runs the interactive
  three-phase discover workflow and asks before every write.
---

# Model List Manager — Add / Discover / CRUD for models.json

You manage the conceptual model library (`models.json`). The existing
`litellm-free-coverage` skill only *analyzes* coverage — you *change* the
library. Never assume a write; every mutation needs explicit user confirmation.

## Recon order (reads first, cheapest first)

1. `models list` — does the family/conceptual model already exist? What variants?
2. `scores list --filter <name>` — AA slugs + scores (intelligence/coding/agentic,
   data age). No slug = no scores = unscored variant.
3. Provider caches (`*_available_models.json`, `free_models.json`,
   `openrouter_free_models.json`) — who offers which provider IDs? Note
   `fetched_at` staleness.
4. `aliases resolve <id>` / `aliases discover <provider> <ids>` — what is
   already mapped vs unmapped.
5. Scan summaries + `model_blocks.json` — health per provider ID (cite
   timestamps; old "Good" is a rumor).

Detail: `references/crud-cheatsheet.md`. State-file layout: see
`../litellm-free-coverage/references/data-map.md`.

## Decision 0 — new conceptual model vs new variant

Dot/minor releases are new models (consistent with all AI providers):
`v4` → `v4.1`, `3.1` → `3.2`, `5.1` → `5.2` each get their own conceptual
model id (e.g. `deepseek-v4-1`). Variants are capability/cost tiers *within*
one version: `standard`/`pro`, `flash`, `flash-lite`, `cheap`, `fast`,
`non-reasoning`, quantized widths, `preview`. Ask when ambiguous; default rules:

- **New conceptual model** (`models add <id> --family --display-name`) when the
  version dot changes, family/architecture is new, or lineage is unrelated.
  DeepSeek V4.1 Flash = new model `deepseek-v4-1` with a `flash` variant
  (AA slug `deepseek-v4-1-flash`), NOT a variant under `deepseek-v4`.
- **New variant under existing model** only for same-version tiers
  (e.g. adding `flash-lite` to an existing `deepseek-v4-1` later).
- **Single-variant models**: when only one variant is known at add time, name it
  for its tier (`flash`, `standard`) AND set it as `--default-variant`
  (e.g. `models add deepseek-v4-1 --default-variant flash`). Confirm with user.
- Variant naming: kebab-case, tier-named (`flash`, `non-reasoning`,
  `flash-preview`), never version-qualified inside the variant key (version
  lives in the model id). Confirm the name with the user before writing.

## Workflow A — add + discover (the 3 phases `models discover` uses)

Walk the same three phases, confirming each before writing anything:

1. **AA slug identification** — `scores list --filter <name>`, pick exact slug
   per variant (e.g. `deepseek-v4-1-flash` I:39.5). Reasoning only by default;
   skip `non-reasoning` slugs unless the user asks. When AA offers the same
   model at multiple reasoning efforts (High / XHigh / Max), prefer High/XHigh
   (what we run) over Max — but Max is fine when it is the only reasoning
   effort in the dataset; confirm with the user. No slug =
   unscored variant; say so plainly, never invent scores, never tier it.
2. **Variant definition** — new model entry vs variant of existing identity
   (Decision 0 above decides; when unsure, ask). Create skeleton
   via `aliases add <model> --variant <v> --aa-slug <slug>` (sets the slug
   only — no scores snapshot). Always run `scores sync` after so the
   variant's `scores` snapshot is backfilled from `model_scores.json`.
3. **Provider mapping** — map ALL providers offering the model, always
   (`aliases discover <provider> <ids>` suggests; `models discover --provider`
   scopes). Exact form per provider ID:
   `aliases add <model> --variant <v> --provider <p> --provider-id <pid>`.
   Strong (AA) matches first, then fuzzy string suggestions — user
   confirms each mapping or skips only with explicit reason. Never subset
   providers silently; never `--yolo` without explicit approval.

Then verify: `models list`, `aliases resolve <new-id>`, `models tag tier --dry-run`
to preview tier effect.

## Workflow B — full CRUD

- **Remove**: `models remove <model>` — confirm model id, warn it drops all
  variants/mappings. Offer `variant update --litellm-disable` instead when the
  user just wants it out of generation.
- **Variant enable/disable**: `models variant update <model> <variant>
  --litellm-disable | --litellm-enable [--litellm-disable-provider <prov> |
  --litellm-enable-provider <prov>]` — per-variant or per-provider exclusion
  from LiteLLM generation. Never auto-modified; only at user request.
- **Variant inventory**: when the user asks what variants a model has or
  whether they are included/excluded, render one row per variant from
  `models list --json` with exactly these columns: variant | AA slug + scores
  | included/excluded (`include_in_litellm`) | tags | provider models
  (every `<provider>: <provider-id>`, or "(none mapped)"). Source of truth is
  `models list --json`, never the summary table.
- **(Re)tag**: `models tag tier [--dry-run]` previews tier-1/2/3 vs current
  leader; `tag set/remove <model> <variant> <tag>` for manual tags.
  Tiers shift as scores change — always report composite-vs-leader ratio.

## Workflow C — change packet

ALWAYS use this exact structure before any write:

```
## Proposed change: <model / variant>
- Type: <new conceptual model | new variant | remove | enable/disable | retag>
- AA slug: <slug + scores + data age, or "unscored — no tier claim">
- Providers: <each provider ID + cache staleness + scan health>
- Exact commands:
    model-manager models add ... / aliases add ... / models discover ...
- Expected effect: <tier placement, alias changes, LiteLLM inclusion>
- Needs approval: <what you must confirm before writing>
```

## Guardrails (why each exists)

- Confirm before every write to `models.json`. Reads and `--dry-run` previews
  are always safe; writes change tiering, aliases, and downstream YAMLs.
- No `--yolo`, no `providers fetch-all`, no live scans, no `scores fetch`,
  no `litellm generate` (even `--dry-run` is a separate skill's job), no
  `schedule install/remove` without explicit approval — they spend money,
  move files, or auto-accept mappings the user hasn't seen.
- Never touch hand-managed files: router stub, `keys.env`, paid YAMLs, keyring
  (read-only via `auth list`). `models.json` writes go through CLI commands,
  never hand-edited JSON.
- Every health claim carries its staleness: cite scan/cache timestamps.
