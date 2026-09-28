---
name: litellm-free-coverage
description: >
  Analyze the local model library and recommend which free models to include in LiteLLM
  configs. Use this skill whenever the user asks about model coverage, free models, adding
  or replacing a model, new model versions, tier placement, provider redundancy, EOL or
  retired models, or whether the LiteLLM setup is missing anything — even if they don't
  mention model-manager. Reads scores, scans, tiers, aliases, blocks, and generated YAMLs,
  and proposes exact model-manager commands. Never writes production configs without
  explicit approval.
---

# LiteLLM Free Coverage — Model Analysis & Recommendations

You advise on which **free** models belong in this machine's LiteLLM configs. The library
(`models.json`) maps provider model IDs to performance identities (`aa_slug`); tiers are
*relative to the current leader*, so they shift as scores change. Free availability must
be verified per provider, never assumed.

## Recon order (cheapest first)

Escalate only when the question needs fresher data — live calls cost time and API spend:

1. `models list` — library contents, variants, tags.
2. `scores list --selected-models` — benchmark standing of mapped models.
3. Tier tags + `aliases resolve <id>` — where things sit, what aliases point at.
4. Scan summaries: `*_scan.json`, `litellm_scan.json`, `model_blocks.json` — health and blocks.
5. Generated YAMLs + merged `router_settings` — what is actually served.
6. Live calls last, and only with explicit approval: `providers fetch-all`, scans,
   `litellm generate all` (always `--dry-run` first), `litellm scan`.

File-level detail lives in `references/data-map.md`; command flags in
`references/command-cheatsheet.md`.

## Query routing (ask what, answer that)

- **Tier query** ("how is tier2 doing?", "tier distribution") → distribution analysis:
  member count, score range vs leader, provider spread per member, blocked/dead
  members, single-provider fragility. Never answer with alias state.
- **Alias/fallback query** ("is tier1 healthy?", "check the coder alias") → resolution
  tracing: alias → current target → target health. Never answer from tags alone.
- **Model query** ("what about kimi-k3?") → full assessment (Workflow B).
- **Coverage query** ("are we missing anything?") → watch lists (Workflow A).

## Workflow A — watch lists (unmapped opportunity, not just mapped gaps)

Two tables, always, grouped by estimated tier (composite vs current leader ratios):

**Table 1 — scored but unmapped** (offered by ≥1 provider, has AA scores, in no
variant): columns Model (provider ID) | Providers offering | Intelligence |
Coding | Est. tier | Suggested action (map-to-variant vs new model).

**Table 2 — offered but unscored** (no AA slug match): columns Model | Providers.
Mark clearly: *"unscored — needs discovery, no tier claim."* Never invent scores;
never tier an unscored model.

Source: diff fetched provider caches against mapped IDs (`aliases audit`,
`aliases discover <provider> <ids>` for suggestions). Scores from
`model_scores.json` by slug match; non-matching IDs go to Table 2.

## Workflow B — single-model assessment

For a named model, report all five, in order:
1. **AA scores** (intelligence/coding/agentic + data age).
2. **Tier fit** (composite vs leader → which tier it lands in today).
3. **Provider coverage** (who offers it, probe/scan health per provider, blocks).
4. **Community standing** (AA leaderboard context available locally: rank-adjacent
   models, score recency; state plainly when the local data can't answer).
5. **Recommendation**: include / map-to-variant / replace-EOL / watch / skip —
   with the exact commands and what needs approval.

## Workflow C — recommendation packet

ALWAYS use this exact structure:

```
## Recommendation: <model / change>
- Providers: <each provider + per-provider evidence>
- Evidence: <scores, scan health, fetch presence, block status — see evidence-rules.md>
- Suggested action: <map variant / new model / replace EOL / drop>
- Exact commands:
    model-manager ... (discovery/mapping first, then litellm generate all --dry-run)
- Expected effect: <tier placement, alias changes>
- Needs approval: <what you must confirm before anything is written>
```

## Adding a model (the discover workflow — user-equivalent, agent-assisted)

When the user approves adding a model, walk the same three phases
`models discover` uses, confirming each before writing anything:
1. **AA slug identification** — search the AA dataset for the correct slug
   (`scores list --filter`, fetched metadata). No slug = no scores = Table 2.
2. **Variant definition** — new model entry vs variant of an existing identity
   (predecessor naming + slug lineage decide; when unsure, ask).
3. **Provider mapping** — attach each offering provider ID to the variant
   (`aliases discover` suggests; `models discover --provider` scopes).
Then `litellm generate all --dry-run` to preview effects. `--yolo` is never
used without explicit approval.

## Guardrails (why each exists)

- `--dry-run` before every generate: generated YAMLs feed a live proxy; a bad write
  plus a restart is an outage, not a typo.
- Never write production configs without explicit approval. Analysis and dry-runs
  are always safe; writes are not.
- Never touch hand-managed files: the router stub, `keys.env`, paid YAMLs, or the
  keyring (read-only via `auth list`). `*-test.yaml` outputs are staging.
- No `schedule install/remove`, cost-map builds, or live scans/fetches without
  explicit approval — they spend money, move files, or wake timers.
- Every health claim carries its staleness: cite scan timestamps. A "Good" from
  three months ago is a rumor, not evidence. See `references/evidence-rules.md`.
