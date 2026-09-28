# Command cheatsheet — analysis reads (safe) vs actions (approval-gated)

## Reads (always safe)
- `models list [--json]` — library, variants, tags
- `scores list --selected-models [--filter X]` — scores of mapped models only
- `scores list --filter X --refresh` — re-fetches first (network; prefer cached)
- `aliases resolve <id>` — trace any ID to identity + scores
- `aliases audit <ids>` / `aliases discover <provider> <ids>` — mapping coverage gaps
- `advisor best [--metric intelligence|coding|agentic]` — top mapped model per metric
- `advisor compare <ids>` — side-by-side
- `doctor` — health report incl. blocks, proxy reachability, restart log
- `schedule status` — timer state (read-only)
- `litellm config check` — integrity validation (read-only)
- `dashboard --no-open` — regenerates the HTML page (writes data_dir only)

## Actions (explicit approval first)
- `models discover <id>`, `models add`, `models variant update` — library writes
- `auth set` — writes keychain (only at user request)
- `litellm generate all --dry-run` — safe preview; without `--dry-run` writes `/etc/litellm`
- `litellm scan [--dry-run]` — hits live APIs (spend); `--dry-run` only lists targets
- `providers fetch-all`, `providers ... scan` — network + API spend, rewrites caches
- `scores fetch` — rewrites scores (tier inputs shift; aliases may reshuffle)
- `schedule install/remove`, cost-map builds — system-level side effects
