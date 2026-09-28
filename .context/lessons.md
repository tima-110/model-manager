# Lessons Learned — model-manager

Project-specific insights. If an entry turns out to apply broadly to other
projects using the same tool or pattern, copy it to ~/practices/lessons.md.

### 2026-09-28: Block-Aware Tier Tags With Alias Cascade
**Context:** Tier tags were pure score math over all variants, so dead/blocked high-scorers pinned the scale and aliases could point at unservable targets; `schedule run` never re-tagged.
**Insight:** Band tiers over the live set only (scored + unblocked + LiteLLM-included, leader = best live composite), strip `tier-*` from non-live variants, and cascade alias picks (tier2 falls back to tier1 value, tier3 to tier2) so all three aliases stay defined whenever ≥1 live model exists. Tagging runs post-scan/pre-generate in the schedule pipeline plus a re-tag in the Phase-B repass. `include_in_litellm` keys inside provider_ids maps are by-design exclusion flags, not junk — skip `include_*` keys at each call site.
**Apply when:** Tier/alias health diverges from scores, or a tier alias resolves to a dead target.
**Global?** No — specific to this project's tier/alias/block-ledger design.
### 2026-05-25: Variant-Based Model Mapping
**Context:** Mapping provider-specific model IDs to Artificial Analysis (AA) scores.
**Insight:** A simple key-value alias map is insufficient because different providers may offer different versions (variants) of the same model (e.g., quantized vs. full), each with different performance indices.
**Apply when:** Building a mapping layer where one conceptual entity can have multiple performance profiles across different providers.

### 2026-07-25: NVIDIA API Model Metadata via NVCF Functions
**Context:** Enriching NVIDIA model fetch with deployment status and model details.
**Insight:** The `/v1/models` endpoint at `integrate.api.nvidia.com` is an OpenAI-compatible listing with only 4 fields (id, object, created, owned_by). The per-model `/v1/models/{id}` returns the same 4 fields. Model metadata is available via the separate NVCF functions API at `api.nvcf.nvidia.com/v2/nvcf/functions`, which returns deployment status (ACTIVE/INACTIVE/DEGRADING), health endpoints, and creation timestamps. Matching between the two APIs requires name normalization: strip the owner prefix from v1 IDs and the `ai-` prefix from NVCF function names. About 47% of v1 models can be matched to NVCF functions. There is no public pricing/free-tier API — the only way to determine availability is to probe endpoints directly. The LiteLLM cost map (local file) has context window and pricing for ~18 NVIDIA models and could be cross-referenced as a future enrichment.
**Apply when:** Working with NVIDIA's model catalog API.
**Global?** Yes — NVIDIA's API structure is consistent across projects.`

### 2026-09-26: Sentinel Flags Inside ID Maps Leak Into Generated Model Names
**Context:** Deployed `litellm-gemini-test.yaml` contained 10 phantom `gemini/include_in_litellm` routes that LiteLLM could never serve.
**Insight:** `models.py` stores the `include_in_litellm` flag as a sibling key inside provider_ids maps, and every generator iterated all map keys as model IDs. Any map with the flag set (not exactly `False`) emitted a phantom model into model_list, fallbacks, and aliases. Fixed centrally in `_iter_provider_ids` with a metadata-key denylist so all collectors/generators are covered at once.
**Apply when:** Iterating a JSON map that mixes entity IDs with metadata flags — filter flags at the single iterator, not at each call site.
**Global?** Yes — sentinel-keys-beside-IDs is a recurring JSON-convention hazard in any codegen pipeline.
