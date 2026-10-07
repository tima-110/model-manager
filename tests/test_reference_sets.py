import json
import pytest
from pathlib import Path
from model_manager.config import load_config
from model_manager.domain import reference_sets

def test_reference_sets_crud(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    models_file = data_dir / "models.json"

    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(f'data_dir = "{data_dir}"\n')
    cfg = load_config(cfg_file)

    # 1. Create reference set
    reference_sets.create_reference_set(cfg, "test-set", display_name="Test Set")
    data = json.loads(models_file.read_text())
    assert "test-set" in data["reference_sets"]
    assert data["reference_sets"]["test-set"]["display_name"] == "Test Set"

    # 2. List reference sets
    sets = reference_sets.list_reference_sets(cfg)
    assert "test-set" in sets

    # 3. Add item
    assert reference_sets.add_item(cfg, "test-set", "my-model", "std", "my-slug")
    data = json.loads(models_file.read_text())
    items = data["reference_sets"]["test-set"]["items"]
    assert len(items) == 1
    assert items[0] == {"model": "my-model", "variant": "std", "aa_slug": "my-slug"}

    # 4. Remove item
    assert reference_sets.remove_item(cfg, "test-set", "my-model", "std")
    data = json.loads(models_file.read_text())
    assert len(data["reference_sets"]["test-set"]["items"]) == 0

    # 5. Remove reference set
    assert reference_sets.remove_reference_set(cfg, "test-set")
    data = json.loads(models_file.read_text())
    assert "test-set" not in data["reference_sets"]
