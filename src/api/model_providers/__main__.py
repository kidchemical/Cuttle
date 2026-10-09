"""Read-only model-provider catalog verbs for Cuttle agents."""
import argparse
import json

from .catalog import get_provider, manifests
from .service import cached_capabilities, discover_models, discover_voices


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    verbs = parser.add_subparsers(dest="verb", required=True)
    verbs.add_parser("list", help="Provider declarations (no network)")
    models = verbs.add_parser("models", help="Account model discovery; never generates content")
    models.add_argument("provider")
    models.add_argument("--refresh", action="store_true")
    models.add_argument("--operation", default="text_generation")
    voices = verbs.add_parser("voices", help="Speech voice discovery; never generates content")
    voices.add_argument("provider")
    voices.add_argument("--model", default="")
    voices.add_argument("--refresh", action="store_true")
    inspect = verbs.add_parser("inspect", help="Cached capabilities for one model (no network)")
    inspect.add_argument("provider")
    inspect.add_argument("model")
    args = parser.parse_args()
    if args.verb != "list" and get_provider(args.provider) is None:
        parser.error("Unknown model provider")
    result = ({"providers": [p.to_public_dict() for p in manifests()]} if args.verb == "list" else
              discover_models(args.provider, refresh=args.refresh, operation=args.operation) if args.verb == "models" else
              discover_voices(args.provider, refresh=args.refresh, model=args.model) if args.verb == "voices" else
              cached_capabilities(args.provider, args.model))
    print(json.dumps({"success": True, **result}, indent=2))


if __name__ == "__main__":
    main()
