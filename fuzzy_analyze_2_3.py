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
        if intelligence is None or intelligence < 30:
            continue
            
        slug_variants = [slug, slug.replace('-', '_')]
        
        best_match = None
        max_providers = 0
        
        for variant in slug_variants:
            matches = {}
            if any(variant in pid for pid in openrouter_ids):
                matches['OR'] = next(pid for pid in openrouter_ids if variant in pid)
            if any(variant in pid for pid in nvidia_ids):
                matches['NV'] = next(pid for pid in nvidia_ids if variant in pid)
            if any(variant in pid for pid in ollama_ids):
                matches['OL'] = next(pid for pid in ollama_ids if variant in pid)
            
            if len(matches) > max_providers:
                max_providers = len(matches)
                best_match = matches
        
        if max_providers >= 2:
            results.append({
                "model": info['name'],
                "intelligence": intelligence,
                "providers": best_match,
                "count": max_providers
            })
    
    if not results:
        print("No models found matching the criteria.")
        return
        
    # Sort by intelligence descending
    results.sort(key=lambda x: x['intelligence'], reverse=True)
    
    print(f"{'Model':<40} | {'Intel':<6} | {'Providers'}")
    print("-" * 80)
    for res in results:
        prov_str = ", ".join([f"{k}: {v}" for k, v in res['providers'].items()])
        print(f"{res['model']:<40} | {res['intelligence']:<6} | {prov_str}")

if __name__ == "__main__":
    main()
