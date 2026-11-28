"""Generate speech or an image using a configured Cuttle model provider."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from tempfile import NamedTemporaryFile
from uuid import uuid4

from dotenv import load_dotenv

from api.model_providers import cache, service
from api.model_providers.catalog import get_provider, manifests
from api.model_providers.types import ImageGenerationRequest, SpeechRequest
from api.shared_media import stage_file
from core.runtime_paths import env_file, output_dir, runtime_state_path


OPERATIONS = {"speech": "speech_generation", "image": "image_generation"}
EXTENSIONS = {
    "image/png": ".png",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
}


def _read_input(path, limit):
    if path == "-":
        text = sys.stdin.read(limit + 1)
    else:
        with Path(path).open(encoding="utf-8-sig") as stream:
            text = stream.read(limit + 1)
    if len(text) > limit:
        raise ValueError(f"Input exceeds {limit} characters.")
    text = text.strip()
    if not text:
        raise ValueError("Input must contain text.")
    return text


def _save_artifact(kind, name, data, mime, share):
    suffix = EXTENSIONS.get(mime)
    if not data or not suffix:
        raise ValueError("The provider returned empty media or an unsupported format.")
    filename = f"{name or kind}-{uuid4().hex}{suffix}"
    direct_share = share and kind == "speech"
    path = (output_dir() / "generated-media" / filename if direct_share
            else runtime_state_path("generated-media", filename))
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = NamedTemporaryFile(dir=path.parent, delete=False)
    temporary = Path(stream.name)
    try:
        with stream:
            stream.write(data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    artifact = {"path": str(path), "mime": mime, "bytes": len(data)}
    if direct_share:
        artifact["url"] = f"/output/generated-media/{filename}"
    elif share:
        staged = stage_file(path)
        if staged.get("success"):
            artifact["url"] = staged["url"]
        else:
            artifact["share_error"] = "Image sharing failed. The local file is available."
    return artifact


def _request(args, manifest):
    if args.kind == "image":
        return ImageGenerationRequest(_read_input(args.input_file, 32_000),
                                      args.model, size=args.size, quality=args.quality)
    model = args.model or manifest.speech["default_model"]
    caps = service.cached_capabilities(manifest.id, model)
    limit = caps.get("max_input_characters") or 4000
    # The existing OpenAI audio adapter caps the Speech endpoint at 4,096.
    if manifest.id == "openai":
        limit = min(limit, 4096)
    if args.speed is not None:
        bounds = manifest.speech.get("speed_range")
        if not bounds or not bounds[0] <= args.speed <= bounds[1]:
            raise ValueError("Speed is outside the supported range for this provider.")
    return SpeechRequest(_read_input(args.input_file, limit),
                         model, args.voice, speed=args.speed)


def _generate(args):
    manifest = get_provider(args.provider)
    if not manifest.configured():
        names = " or ".join(manifest.credential_env)
        raise ValueError(f"Configure {manifest.label} API credentials ({names}) before generating media.")
    if args.name and not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,47}", args.name):
        raise ValueError("Name must use 1–48 letters, digits, underscores, or hyphens.")
    request = _request(args, manifest)
    attribution = {"usage_task": "agent:media", "session_id": os.getenv("CUTTLE_CHAT_SESSION_ID")}
    try:
        if args.kind == "speech":
            result = service.synthesize_speech(manifest.id, request, **attribution)
            data = result.audio
        else:
            result = service.generate_image(manifest.id, request, **attribution)
            data = result.image
    except Exception as exc:
        raise ValueError(cache.public_error(exc, f"{manifest.label} {args.kind} generation")) from None
    artifact = _save_artifact(args.kind, args.name, data, result.mime, args.share)
    return {"kind": args.kind, "provider": manifest.id, "model": request.model,
            "artifact": artifact, "usage": result.usage}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    verbs = parser.add_subparsers(dest="kind", required=True)
    for kind, operation in OPERATIONS.items():
        command = verbs.add_parser(kind)
        command.add_argument("--provider", required=True,
                             choices=[p.id for p in manifests() if operation in p.operations])
        command.add_argument("--model", required=kind == "image")
        command.add_argument("--input-file", default="-", help="UTF-8 text file or - for stdin")
        command.add_argument("--name", help="Friendly filename prefix")
        command.add_argument("--share", action="store_true", help="Return a signed-in Cuttle chat URL")
        if kind == "speech":
            command.add_argument("--voice", required=True)
            command.add_argument("--speed", type=float)
        else:
            command.add_argument("--size", default="1024x1024", help="Provider-supported size")
            command.add_argument("--quality", default="medium",
                                 choices=["auto", "low", "medium", "high", "xhigh", "max"])
    args = parser.parse_args(argv)
    load_dotenv(env_file(), override=False)
    try:
        result = {"success": True, **_generate(args)}
    except (ValueError, OSError) as exc:
        result = {"success": False, "error": str(exc)}
    print(json.dumps({"schema_version": 1, **result}))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
