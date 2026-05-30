# Implementation Plan: Add --sort to `scores list` command

## Goal
Add an optional `--sort` parameter to the `scores list` command to allow users to sort the results by different metrics (alpha, intelligence, coding, math, ttft, tps).

## Changes

### `src/model_manager/cli.py`

1.  **Update `scores_list` signature**:
    - Add `sort: SortOption = typer.Option(SortOption.alpha, "--sort", help="Sort the list by the specified metric.")`.
2.  **Update `scores_list` docstring**:
    - Add documentation for the `--sort` option.
3.  **Implement sorting logic**:
    - Add a sorting step after the filtering logic and before the table rendering.
    - Sorting behavior:
        - `alpha`: Ascending by model name (`id1`).
        - `int`: Descending by intelligence score.
        - `code`: Descending by coding score.
        - `math`: Descending by math score.
        - `ttft`: Ascending by Time to First Token (lower is better).
        - `tps`: Descending by Tokens Per Second.
    - Handle `None` values to ensure they are placed at the end of the list regardless of sort direction.

## Verification Plan

1.  **Basic List**: Run `model-manager scores list` to ensure default (alpha) sorting still works.
2.  **Sort by Score**: Run `model-manager scores list --sort int` and verify the highest intelligence scores are at the top.
3.  **Sort by TTFT**: Run `model-manager scores list --sort ttft` and verify the lowest TTFT values are at the top.
4.  **Sort by TPS**: Run `model-manager scores list --sort tps` and verify the highest TPS values are at the top.
5.  **Filter + Sort**: Run `model-manager scores list --filter "gpt" --sort code` to ensure both work together.
6.  **Missing Scores**: Verify that models with missing scores for the selected metric appear at the bottom.
