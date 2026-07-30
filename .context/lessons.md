# Lessons Learned — model-manager

Project-specific insights. If an entry turns out to apply broadly to other
projects using the same tool or pattern, copy it to ~/practices/lessons.md.

<!-- Append entries below. Newest at bottom. -->
### 2026-05-25: Variant-Based Model Mapping
**Context:** Mapping provider-specific model IDs to Artificial Analysis (AA) scores.
**Insight:** A simple key-value alias map is insufficient because different providers may offer different versions (variants) of the same model (e.g., quantized vs. full), each with different performance indices.
**Apply when:** Building a mapping layer where one conceptual entity can have multiple performance profiles across different providers.

### 2026-07-25: NVIDIA API Model Metadata via NVCF Functions
**Context:** Enriching NVIDIA model fetch with deployment status and model details.
**Insight:** The `/v1/models` endpoint at `integrate.api.nvidia.com` is an OpenAI-compatible listing with only 4 fields (id, object, created, owned_by). The per-model `/v1/models/{id}` returns the same 4 fields. Model metadata is available via the separate NVCF functions API at `api.nvcf.nvidia.com/v2/nvcf/functions`, which returns deployment status (ACTIVE/INACTIVE/DEGRADING), health endpoints, and creation timestamps. Matching between the two APIs requires name normalization: strip the owner prefix from v1 IDs and the `ai-` prefix from NVCF function names. About 47% of v1 models can be matched to NVCF functions. There is no public pricing/free-tier API — the only way to determine availability is to probe endpoints directly. The LiteLLM cost map (local file) has context window and pricing for ~18 NVIDIA models and could be cross-referenced as a future enrichment.
**Apply when:** Working with NVIDIA's model catalog API.
**Global?** Yes — NVIDIA's API structure is consistent across projects.`
