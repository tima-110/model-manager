import json
from pathlib import Path

DATA_DIR = Path("/home/castor/.local/share/model-manager")

def load_json(filename):
    with open(DATA_DIR / filename, 'r') as f:
        return json.load(f)

def main():
    scores = load_json("model_scores.json")
    
    openrouter_data = load_json("openrouter_free_models.json")
    nvidia_data = load_json("nvidia_available_models.json")
    ollama_data = load_json("ollama_available_models.json")
    
    openrouter_ids = [m['id'] for m in openrouter_data['models']]
    nvidia_ids = [m['id'] for m in nvidia_data['models']]
    ollama_ids = [m['id'] for m in ollama_data['models']]
    
    results = []
    
    for slug, info in scores['models'].items():
        intelligence = info['scores'].get('intelligence')
        if intelligence is None or intelligence <= 30:
            continue
            
        # We search for the slug in the IDs.
        # Some slugs might be too generic (e.g. 'gpt-5'), so we try to be a bit careful.
        # But let's start with simple substring match.
        
        # Try matching the slug itself
        # also try replacing '-' with '_' just in case
        slug_variants = [slug, slug.replace('-', '_')]
        
        found_in_all = False
        for variant in slug_variants:
            # Check if this variant is a substring of at least one ID in each provider
            has_openrouter = any(variant in pid for pid in openrouter_ids)
            has_nvidia = any(variant in pid for pid in nvidia_ids)
            has_ollama = any(variant in pid for pid in ollama_ids)
            
            if has_openrouter and has_nvidia and has_ollama:
                found_in_all = True
                # Store which IDs matched for verification
                match_or = next(pid for pid in openrouter_ids if variant in pid)
                match_nv = next(pid for pid in nvidia_ids if variant in pid)
                match_ol = next(pid for pid in ollama_ids if variant in pid)
                break
        
        if found_in_all:
            results.append({
                "model": info['name'],
                "slug": slug,
                "intelligence": intelligence,
                "ids": (match_or, match_nv, match_ol)
            })
    
    if not results:
        print("No models found matching all criteria with fuzzy matching.")
        return
        
    print(f"{'Model':<30} | {'Intelligence':<12} | {'IDs'}")
    print("-" * 80)
    for res in results:
        ids_str = f"OR: {res['ids'][0]}, NV: {res['ids'][1]}, OL: {res['ids'][2]}"
        print(f"{res['model']:<30} | {res['intelligence']:<12} | {ids_str}")

if __name__ == "__main__":
    main()
