# Data map — model-manager state files

All paths relative to `data_dir` (`~/.local/share/model-manager`) unless noted.
Config: `~/.config/model-manager/config.toml`.

## Library & scores
- `models.json` — curated library. `models.<id>.variants.<v>`: `aa_slug` (score
  lookup key), `scores` snapshot, `tags` (`tier-1/2/3` managed, others manual),
  `include_in_litellm` (user exclusion, never auto-modified),
  `provider_ids.<provider>.<pid>` → scan metadata (`availability`, `assessment`).
- `model_scores.json` — processed AA scores keyed by `aa_slug`
  (`intelligence`, `coding`, `agentic`, `ttft`, `tps`). Agentic comes from the
  AA language-models API, not the model-list endpoint.
- `aa_raw_response.json` — raw API dump (debugging only).

## Health & availability
- `<provider>_scan.json` — per-model ping history + summary (`availability`,
  `avg_latency`, `assessment`: Good/Slow/Weak/Dead/Unsupported/Unknown/
  Ratelimited/Unauthorized/Not Found). Missing file = unscanned, not unhealthy.
- `litellm_scan.json` — proxy probe records (`status`, `ttft_ms`, `tps`,
  `reasoning_*`, `finish_reason`); `empty` = HTTP 200 with no usable chunks.
- `model_blocks.json` — availability ledger: targets excluded from generation
  with `{blocked_since, reason, code, source}`. Governed by `[blocking]`
  config; first clean observation releases. Never hand-edit the shape.

## Provider fetch caches (newcomer source for watch lists)
- `*_available_models.json`, `free_models.json`, `openrouter_free_models.json` —
  last fetched provider catalogs with `fetched_at`. Diff IDs against mapped
  `provider_ids` to find unmapped models; scores resolve via AA slug match.

## Generated outputs (`/etc/litellm/`)
- Per-provider `model_list` YAMLs (`*-test.yaml` = staging).
- `litellm-fallbacks.yaml`, `litellm-aliases.yaml` (tier1/2/3 only),
  `litellm-router_settings.yaml` (merged: stub hand aliases + generated tiers —
  the complete alias set lives here, 13 keys typical).
- `litellm-router_settings-stub.yaml` — hand-managed, never overwritten,
  never touched by automation. May reference paid models added outside the tool.
- `restart_requests.jsonl` — restart orders for the external watcher.
  Append-only; never write foreign record types here.
- `schedule_runs.jsonl`, `dashboard.html` (in `data_dir`) — run history, status page.
- `model_prices.json`, `radar.html` (in `data_dir`) — blended $/1M price
  library (overrides > OpenRouter > upstream) and the interactive radar
  snapshot; refreshed by every `schedule run` after the dashboard.
