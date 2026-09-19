from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import wave
from pathlib import Path


_MODELS: dict[str, object] = {}


def _local_hf_snapshot(
    model_dir: str | Path,
    repository_cache_name: str,
    required_files: tuple[str, ...] = (),
) -> Path | None:
    """Return a complete local Hugging Face snapshot without contacting the Hub."""
    snapshots = Path(model_dir) / "hf-cache" / "hub" / repository_cache_name / "snapshots"
    if not snapshots.is_dir():
        return None
    candidates = sorted(
        (
            item
            for item in snapshots.iterdir()
            if item.is_dir() and all((item / relative).is_file() for relative in required_files)
        ),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _supertonic(request: dict) -> None:
    import numpy as np
    import soundfile as sf
    from supertonic import TTS

    model = _MODELS.get("supertonic3")
    if model is None:
        model_dir = Path(request["model_dir"]).parent / "supertonic-3"
        model = TTS(model="supertonic-3", model_dir=str(model_dir), auto_download=False)
        _MODELS["supertonic3"] = model
    style = model.get_voice_style(voice_name=request["voice"]["technical_voice_id"])
    audio, _ = model.synthesize(text=request["text"], voice_style=style, total_steps=8, speed=1.0, lang=request["language"].split("-", 1)[0].lower())
    sf.write(request["output"], np.asarray(audio, dtype=np.float32).squeeze(), int(getattr(model, "sample_rate", 44100)), subtype="PCM_16")


def _kokoro(request: dict) -> None:
    import soundfile as sf
    from kokoro_onnx import Kokoro

    model = _MODELS.get("kokoro")
    if model is None:
        root = Path(request["model_dir"])
        model = Kokoro(str(root / "kokoro-v1.0.int8.onnx"), str(root / "voices-v1.0.bin"))
        _MODELS["kokoro"] = model
    language = {"en-US": "en-us", "en-GB": "en-gb", "es-ES": "es", "fr-FR": "fr-fr"}.get(request["language"], "en-us")
    audio, sample_rate = model.create(request["text"], voice=request["voice"]["technical_voice_id"], speed=1.0, lang=language)
    sf.write(request["output"], audio, sample_rate, subtype="PCM_16")


def _qwen(request: dict) -> None:
    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel

    model = _MODELS.get("qwen")
    if model is None:
        model_root = Path(request["model_dir"]) / "Qwen3-TTS-12Hz-0.6B-CustomVoice"
        snapshot = _local_hf_snapshot(
            request["model_dir"],
            "models--Qwen--Qwen3-TTS-12Hz-0.6B-CustomVoice",
            ("config.json", "model.safetensors"),
        )
        source = str(model_root) if (model_root / "config.json").is_file() else str(snapshot or "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice")
        cuda = torch.cuda.is_available()
        model = Qwen3TTSModel.from_pretrained(source, device_map="cuda:0" if cuda else "cpu", dtype=torch.bfloat16 if cuda else torch.float32)
        _MODELS["qwen"] = model
    torch.manual_seed(int(request.get("seed") or 0))
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(int(request.get("seed") or 0))
    language = {"en-US": "English", "en-GB": "English", "de-DE": "German", "es-ES": "Spanish", "fr-FR": "French"}.get(request["language"], "Auto")
    audio, sample_rate = model.generate_custom_voice(text=request["text"], language=language, speaker=request["voice"]["technical_voice_id"], instruct=request.get("style_instruction") or "")
    sf.write(request["output"], audio[0], sample_rate, subtype="PCM_16")


def _vieneu(request: dict) -> None:
    from vieneu import Vieneu

    model = _MODELS.get("vieneu")
    if model is None:
        snapshot = _local_hf_snapshot(
            request["model_dir"],
            "models--pnnbao-ump--VieNeu-TTS-v3-Turbo",
            ("config.json", "onnx_update/config.json", "onnx_update/vieneu_prefill.onnx"),
        )
        kwargs = {
            "backbone_repo": str(snapshot),
            "onnx_dir": str(snapshot / "onnx_update"),
        } if snapshot else {}
        model = Vieneu(mode="v3turbo", device="auto", **kwargs)
        _MODELS["vieneu"] = model
    audio = model.infer(text=request["text"], voice=request["voice"]["technical_voice_id"], apply_watermark=True)
    model.save(audio, request["output"])


def _chatterbox(request: dict) -> None:
    import numpy as np
    import soundfile as sf
    import torch
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    model = _MODELS.get("chatterbox")
    if model is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        snapshot = _local_hf_snapshot(
            request["model_dir"],
            "models--ResembleAI--chatterbox",
            ("conds.pt", "ve.pt", "s3gen.pt", "t3_mtl23ls_v3.safetensors"),
        )
        model = ChatterboxMultilingualTTS.from_local(snapshot, device=device, t3_model="v3") if snapshot else ChatterboxMultilingualTTS.from_pretrained(device=device, t3_model="v3")
        _MODELS["chatterbox"] = model
    torch.manual_seed(int(request.get("seed") or 0))
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(int(request.get("seed") or 0))
    reference = request["voice"].get("reference_audio") or None
    audio = model.generate(request["text"], language_id=request["language"].split("-", 1)[0], audio_prompt_path=reference, exaggeration=0.5, cfg_weight=0.5)
    if isinstance(audio, torch.Tensor):
        audio = audio.squeeze().detach().cpu().numpy()
    sf.write(request["output"], np.asarray(audio).squeeze(), int(getattr(model, "sr", 24000)), subtype="PCM_16")


def _piper(request: dict) -> None:
    from piper import PiperVoice
    from piper.config import SynthesisConfig

    model_path = Path(request["voice"]["reference_audio"])
    cache_key = "piper:" + str(model_path)
    model = _MODELS.get(cache_key)
    if model is None:
        model = PiperVoice.load(str(model_path), config_path=str(model_path.with_suffix(model_path.suffix + ".json")))
        _MODELS[cache_key] = model
    technical_id = str(request["voice"].get("technical_voice_id") or "")
    speaker_id = int(technical_id.rsplit(":", 1)[1]) if ":" in technical_id and technical_id.rsplit(":", 1)[1].isdigit() else None
    with wave.open(request["output"], "wb") as wav:
        model.synthesize_wav(request["text"], wav, syn_config=SynthesisConfig(speaker_id=speaker_id))


def _f5tts(request: dict) -> None:
    import soundfile as sf
    from f5_tts.api import F5TTS

    model = _MODELS.get("f5tts")
    if model is None:
        root = Path(request["model_dir"]) / "german"
        model = F5TTS(
            model="F5TTS_Base",
            ckpt_file=str(root / "model_f5tts_german.safetensors"),
            vocab_file=str(root / "vocab.txt"),
            device="cuda",
            hf_cache_dir=str(Path(request["model_dir"]) / "hf-cache"),
        )
        _MODELS["f5tts"] = model
    reference = request["voice"].get("reference_audio")
    reference_text = request["voice"].get("reference_text") or "Willkommen bei Tool Recap. Diese Stimme wurde von künstlicher Intelligenz erzeugt."
    audio, sample_rate, _ = model.infer(
        ref_file=reference,
        ref_text=reference_text,
        gen_text=request["text"],
        seed=int(request.get("seed") or 0),
        nfe_step=32,
        speed=1.0,
        show_info=lambda *_args, **_kwargs: None,
    )
    sf.write(request["output"], audio, sample_rate, subtype="PCM_16")


def _kitten(request: dict) -> None:
    import json as json_module
    import soundfile as sf
    from kittentts.onnx_model import KittenTTS_1_Onnx

    model = _MODELS.get("kitten")
    if model is None:
        root = Path(request["model_dir"])
        config = json_module.loads((root / "config.json").read_text(encoding="utf-8"))
        model = KittenTTS_1_Onnx(
            model_path=str(root / config["model_file"]),
            voices_path=str(root / config["voices"]),
            speed_priors=config.get("speed_priors", {}),
            voice_aliases=config.get("voice_aliases", {}),
        )
        _MODELS["kitten"] = model
    audio = model.generate(
        request["text"],
        voice=request["voice"]["technical_voice_id"],
        speed=1.0,
        clean_text=True,
    )
    sf.write(request["output"], audio, 24000, subtype="PCM_16")


def _voicestudio_generation_kwargs(request: dict) -> dict:
    language = {"en-US": "English", "en-GB": "English", "de-DE": "German"}.get(request["language"], "English")
    kwargs = {
        "text": request["text"],
        "language": language,
        "num_step": 32,
        "guidance_scale": 2.0,
        "speed": 1.0,
        "denoise": True,
        "postprocess_output": True,
    }
    reference_audio = str(request["voice"].get("reference_audio") or "")
    reference_text = str(request["voice"].get("reference_text") or "")
    if reference_audio and reference_text:
        kwargs["ref_audio"] = reference_audio
        kwargs["ref_text"] = reference_text
    else:
        kwargs["instruct"] = request["voice"].get("voice_instruction") or None
    return kwargs


def _voicestudio(request: dict) -> None:
    config = request.get("engine_config") or {}
    project = Path(config["external_project"])
    cache = Path(config["external_cache"])
    snapshot = Path(config["model_snapshot"])
    if str(project) not in sys.path:
        sys.path.insert(0, str(project))
    os.environ["HF_HOME"] = str(cache)
    os.environ["HF_HUB_CACHE"] = str(cache)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"

    import soundfile as sf
    import torch
    from omnivoice.models.omnivoice import OmniVoice

    model = _MODELS.get("voicestudio")
    if model is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype = torch.float16 if device == "cuda" else torch.float32
        model = OmniVoice.from_pretrained(str(snapshot), device_map=device, dtype=dtype)
        _MODELS["voicestudio"] = model
    seed = int(request.get("seed") or 0)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    audio = model.generate(**_voicestudio_generation_kwargs(request))[0]
    if isinstance(audio, torch.Tensor):
        audio = audio.squeeze().detach().cpu().numpy()
    sf.write(request["output"], audio, int(model.sampling_rate), subtype="PCM_16")


HANDLERS = {"voicestudio": _voicestudio}


def _execute(engine: str, request: dict) -> dict:
    try:
        with contextlib.redirect_stdout(sys.stderr):
            HANDLERS[engine](request)
        output = Path(request["output"])
        if not output.is_file() or output.stat().st_size < 128:
            raise RuntimeError("Engine không tạo được WAV hợp lệ.")
        return {"ok": True, "output": str(output), "size": output.stat().st_size}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True, choices=sorted(HANDLERS))
    parser.add_argument("--request")
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()
    if args.serve:
        for line in sys.stdin:
            if line.strip() == "__quit__":
                break
            try:
                request = json.loads(line)
                response = _execute(args.engine, request)
            except Exception as exc:
                response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            print(json.dumps(response, ensure_ascii=False), flush=True)
        return 0
    if not args.request:
        raise RuntimeError("Thiếu --request.")
    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    response = _execute(args.engine, request)
    if not response["ok"]:
        raise RuntimeError(response["error"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
