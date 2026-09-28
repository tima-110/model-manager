# Evidence rules — what proves what

A claim without cited evidence is a guess. Cite file + timestamp for every claim.

## "Free on provider X"
Requires **all three**: (1) present in the provider's fetched list (cache file,
check `fetched_at`), (2) clean probe or Active/Good scan summary, (3) no
`model_blocks.json` entry. A catalog listing alone proves nothing (it may be
paid, gated, or stale).

## "Healthy / ready to serve"
`litellm_scan.json` `up` status with a fresh timestamp beats provider-scan
`Good` (proxy-level proof > upstream ping). State which one you're citing.
Single samples are noisy — say so when the record is thin.

## "New version of Y"
Naming-pattern match **plus** same family/creator check from the fetched
metadata. kimi-k2 → kimi-k3 counts; similar slugs from different creators do
not. Recommend map-to-variant only when the `aa_slug` lineage matches.

## "Tier N needs attention"
Resolve the alias to its target first (`aliases resolve tierN`), then evaluate
the *target*. A tier alias is never evidence of health by itself.

## "Should be removed / EOL"
Requires a retirement marker (410, retired/removed/EOL message, catalog
absence across two fetches) — never a single 500/503/429, which are transient
by policy. Check `model_blocks.json` reason before asserting.

## "Belongs in tier N" (estimated, for unmapped models)
Composite = mean of available intelligence/coding/agentic; divide by the
current library leader's composite (find via top `scores list` entries);
apply `tags.tier1_min_ratio` / `tier2_min_ratio` from config. Label
explicitly **estimated** — confirmed only after mapping + `models tag tier`.

## "No score data"
State `unscored — needs discovery`. Never tier, rank, or recommend inclusion
of an unscored model beyond suggesting discovery.

## "Community standing" (model assessments only)
Always use web search — never declare it unanswerable from local data alone.
Report reception signals: launch coverage, benchmark commentary, community
pickup (HuggingFace likes/downloads, OpenRouter usage rank if findable),
known issues. Two to three dated findings with sources; state plainly what you
could not find.

## Staleness rule
Every health claim states its data age (`scan_timestamp`, file mtimes). Older
than the last scheduled run = say so explicitly.
