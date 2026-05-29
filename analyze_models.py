import json
from pathlib import Path

DATA_DIR = Path("/home/castor/.local/share/model-manager")

def load_json(filename):
    with open(DATA_DIR / filename, 'r') as f:
        return json.load(f)

def main():
    aliases = load_json("model_aliases.json")
    scores = load_json("model_scores.json")
    
    openrouter_data = load_json("openrouter_free_models.json")
    nvidia_data = load_json("nvidia_available_models.json")
    ollama_data = load_json("ollama_available_models.json")
    
    openrouter_ids = {m['id'] for m in openrouter_data['models']}
    nvidia_ids = {m['id'] for m in nvidia_data['models']}
    ollama_ids = {m['id'] for m in ollama_data['models']}
    
    results = []
    
    for model_id, model_info in aliases['models'].items():
        display_name = model_info['display_name']
        
        # Check each variant
        for variant_name, variant_info in model_info['variants'].items():
            aa_slug = variant_info['aa_slug']
            provider_ids = variant_info.get('provider_ids', {})
            
            # Check availability in all three providers
            has_openrouter = any(pid in openrouter_ids for pid in provider_ids.get('openrouter', []))
            has_nvidia = any(pid in nvidia_ids for pid in provider_ids.get('nvidia', []))
            has_ollama = any(pid in ollama_ids for pid in provider_ids.get('ollama', []))
            
            if has_openrouter and has_nvidia and has_ollama:
                # Check intelligence score
                score_info = scores['models'].get(aa_slug)
                if score_info:
                    intelligence = score_info['scores'].get('intelligence')
                    if intelligence and intelligence > 40:
                        results.append({
                            "model": display_name,
                            "variant": variant_name,
                            "intelligence": intelligence,
                            "aa_slug": aa_slug
                        })
    
    if not results:
        print("No models found matching all criteria.")
        return
        
    print(f"{'Model':<30} | {'Variant':<15} | {'Intelligence':<12}")
    print("-" * 60)
    for res in results:
        print(f"{res['model']:<30} | {res['variant']:<15} | {res['intelligence']:<12}")

if __name__ == "__main__":
    main()
