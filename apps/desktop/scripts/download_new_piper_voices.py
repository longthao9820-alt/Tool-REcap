"""Install verified upstream Piper models, preserving source cards and checksums."""
import hashlib
import json
import sys
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "desktop"))
BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
SPEAKERS = [
    ("de_DE-kerstin-low", "female"),
    ("en_US-hfc_female-medium", "female"),
    ("en_US-hfc_male-medium", "male"),
    ("en_GB-alba-medium", "female"),
]


def main() -> None:
    with urlopen(BASE + "voices.json", timeout=60) as response:
        index = json.load(response)
    provider = ROOT / "runtime/speech/engines/piper/provider.json"
    manifest = json.loads(provider.read_text(encoding="utf-8"))
    new_voices = []
    for key, gender in SPEAKERS:
        entry = index[key]
        files = entry["files"]
        card_relative = next(name for name in files if name.endswith("MODEL_CARD"))
        with urlopen(BASE + card_relative, timeout=60) as response:
            card = response.read().decode("utf-8")
        license_line = next(line for line in card.splitlines() if "License:" in line)
        if "See URL" in license_line:
            raise RuntimeError(f"License requires review: {key}")
        print(key, license_line, flush=True)
        for relative, metadata in files.items():
            target = ROOT / "runtime/speech/models/piper" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            expected = int(metadata["size_bytes"])
            if target.is_file() and target.stat().st_size == expected:
                continue
            temporary = target.with_suffix(target.suffix + ".partial")
            digest = hashlib.md5()
            with urlopen(BASE + relative, timeout=180) as response, temporary.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
                    digest.update(chunk)
            if temporary.stat().st_size != expected or digest.hexdigest().lstrip("0") != metadata["md5_digest"].lstrip("0"):
                raise RuntimeError(f"Download checksum failed: {relative}")
            temporary.replace(target)
            print("Downloaded", relative, flush=True)
        language = entry["language"]["code"].replace("_", "-")
        model = next(name for name in files if name.endswith(".onnx"))
        voice_id = "piper.new_" + key.replace("-", ".").lower()
        license_name = license_line.split("License:", 1)[1].strip()
        new_voices.append({
            "voice_id": voice_id, "technical_voice_id": entry["name"],
            "display_name": entry["name"].replace("_", " ").title() + " (mới)",
            "language": language, "gender": gender, "license": license_name,
            "commercial_use": False, "personal_use_only": True,
            "attribution_required": "CC0" not in license_name,
            "reference_audio": "runtime/speech/models/piper/" + model,
            "source": BASE.replace("/resolve/", "/blob/") + card_relative,
            "model_version": key + ":" + files[model]["md5_digest"],
            "description": f"Piper {entry['quality']}, {entry['language']['name_english']}. Nguồn và giấy phép kèm model; chỉ dùng cá nhân.",
        })
    manifest["voices"] = [voice for voice in manifest["voices"] if voice["language"] not in {"de-DE", "en-US", "en-GB"} and voice.get("gender") in {"male", "female"}] + new_voices
    manifest["disabled_voice_prefixes"] = ["piper.de.", "piper.en."]
    temporary = provider.with_suffix(".partial")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(provider)
    from recap_tool.voice_system import UnifiedTTSManager
    manager = UnifiedTTSManager(ROOT)
    try:
        for voice in new_voices:
            manager.preview(voice["voice_id"], voice["language"])
            print("Preview OK", voice["voice_id"], flush=True)
        manager.catalog.rebuild()
    finally:
        manager.shutdown_all()


if __name__ == "__main__":
    main()
