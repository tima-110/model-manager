# CRUD cheatsheet — model-list-manager writes (confirm first)

All writes go through the CLI, never hand-edited JSON.
Reads are always safe; every command below mutates `models.json` unless noted.

## Reads (safe, recon)
- `models list [--json]` — conceptual models, variants, tags, provider IDs
- `scores list --filter <name> [--json]` — AA slugs + scores by name match
- `scores list --selected-models` — only already-mapped slugs
- `aliases resolve <id>` — trace any provider/conceptual ID to identity + scores
- `aliases audit <csv-ids>` — mapped vs unmapped coverage
- `aliases discover <provider> <csv-ids>` — suggested variant mappings
- `models tag list` / `models tag tier --dry-run` — preview tier placement
- `advisor compare <csv-ids>` / `advisor best [--metric intelligence|coding|agentic]`

## Create
- `models add <model-id> --family <F> --display-name <D> [--default-variant standard]`
  — new conceptual model shell (no slug, no providers yet).
- `aliases add <model> --variant <v> --aa-slug <slug> [--family] [--display-name]`
  — attaches the AA slug (creates variant if missing). NOTE: the CLI does not
  snapshot scores inline (only `models discover` does, via
  `scores.get_scores_for_slug`). Always run `scores sync` after so the
  variant's `scores` snapshot is backfilled from `model_scores.json`.
- `scores sync` — backfill `scores` snapshots for all variants with an
  `aa_slug` (preserves existing agentic on API gaps). Required after any
  `aliases add --aa-slug`, not needed after `models discover`.
- `models discover <model-id> [--provider <p>]` — interactive 3-phase flow
  (slug → variants → provider mapping). `--refresh` refetches caches + scores
  (network/spend — approval first). Never `--yolo` without explicit approval.
- `aliases add <model> --variant <v> --provider <p> --provider-id <pid>`
  — attach one provider ID to a variant (repeat per provider; confirm each).

## Update (variants, providers, tags)
- `models variant update <model> <variant> --litellm-disable | --litellm-enable`
  — exclude/re-include variant from LiteLLM generation.
- `models variant update <model> <variant> --litellm-disable-provider <p> | --litellm-enable-provider <p>`
  — per-provider exclusion.
- `models tag tier [--dry-run] [--t1-ratio X --t2-ratio Y]` — recompute tier-1/2/3
  vs leader. `--dry-run` previews; without it, writes tags.
- `models tag set <model> <variant> <tag>` / `models tag remove <model> <variant> <tag>`
  — manual tags.

## Delete / exclude
- `models remove <model>` — drops the whole conceptual model + variants.
  Prefer `--litellm-disable` when the user just wants it out of generation.
- Per-provider exclusion (above) is reversible; `remove` is not — confirm twice.

## Verify after every change
1. `models list` — variant present, slug + providers attached.
2. `aliases resolve <new-provider-id>` — climbs to the right identity + scores.
3. `models tag tier --dry-run` — expected tier vs leader (report ratio).
4. Hand `litellm generate` preview to the litellm-free-coverage flow — out of
   scope here unless the user asks.

## DeepSeek V4.1 Flash worked example (test case, dry-run)
- Recon: `scores list --filter deepseek` → `deepseek-v4-1-flash` (DeepSeek V4.1
  Flash (Max), I:39.5, C/A null, TTFT 0.804, TPS 214.9). Non-reasoning slug
  exists but skipped (reasoning-only default). Caches (2026-10-03): nvidia
  `deepseek-ai/deepseek-v4.1-flash`, ollama `deepseek-v4.1-flash`,
  huggingface `deepseek-ai/DeepSeek-V4.1-Flash`; openrouter-free + gemini: none.
  `aliases audit` → all 3 unmapped. No `deepseek-v4-1` in `models list`.
- Decision 0: dot release → NEW conceptual model `deepseek-v4-1`
  (family Deepseek AI), variant `flash`, set as `--default-variant flash`
  since it is the only variant known.
- Phase 1+2 (proposed, not executed):
  `models add deepseek-v4-1 --family "Deepseek AI" --display-name "Deepseek V4.1" --default-variant flash`
  `aliases add deepseek-v4-1 --variant flash --aa-slug deepseek-v4-1-flash`
  `scores sync` (required: `aliases add` sets the slug but not the snapshot)
- Phase 3 (proposed): map ALL three provider IDs to `flash` (confirm each).
- Verify: `models list`, `aliases resolve deepseek-ai/deepseek-v4.1-flash`,
  `models tag tier --dry-run`.
