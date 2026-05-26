# Lessons Learned — model-manager

Project-specific insights. If an entry turns out to apply broadly to other
projects using the same tool or pattern, copy it to ~/practices/lessons.md.

<!-- Append entries below. Newest at bottom. -->
### 2026-05-25: Variant-Based Model Mapping
**Context:** Mapping provider-specific model IDs to Artificial Analysis (AA) scores.
**Insight:** A simple key-value alias map is insufficient because different providers may offer different versions (variants) of the same model (e.g., quantized vs. full), each with different performance indices.
**Apply when:** Building a mapping layer where one conceptual entity can have multiple performance profiles across different providers.
**Global?** No — specific to this project's domain of model rankings.
