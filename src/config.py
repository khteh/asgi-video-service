"""Environment-driven application settings.

A single immutable Settings object is built once (from_env) and threaded
through the app factory into every layer that needs configuration, rather
than each module reaching into os.environ itself.
"""
from __future__ import annotations

import os, logging, json, sys, secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from logging.handlers import TimedRotatingFileHandler
from dotenv import load_dotenv

load_dotenv()

def _int(settings: dict, name: str, default: int) -> int:
    try:
        return int(settings[name]) if name in settings else int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default

def _float(settings: dict, name: str, default: float) -> float:
    try:
        return float(settings[name]) if name in settings else float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default

class ConfigSingleton(type): # Inherit from "type" in order to gain access to method __call__
    __registry = {}
    def __call__(cls, *args, **kwargs):
        registry = type(cls).__registry
        if cls not in registry:
              registry[cls] = (super().__call__(*args, **kwargs), args, kwargs)
        elif registry[cls][1] != args or registry(cls)[2] != kwargs:
              raise TypeError(f"Class already initialized with different arguments!")
        return registry[cls][0]

@dataclass(frozen=True)
class Settings:
    environment:str = None
    LOGLEVEL:str = None
    TESTING = False
    SECRET_KEY:str = None
    JWT_SECRET_KEY:str = None

    output_dir: Path = Path("output")

    # "simulated" (default, offline) or "ai" (real provider, pluggable).
    default_generation_provider: str = "simulated"

    # Hard requirements: <=90s, 4K, 60fps.
    max_video_seconds: float = 90.0
    video_width: int = 3840
    video_height: int = 2160
    video_fps: int = 60
    # "auto" probes ffmpeg for an NVIDIA NVENC encoder and uses it if
    # present, falling back to libx264 otherwise. "on"/"off" force it.
    nvenc_mode: str = "auto"

    # edge-tts voice for the simulated provider's narration.
    tts_voice: str = "en-US-AriaNeural"

    # Generic/vendor-agnostic real AI video-generation provider.
    ai_video_api_key: Optional[str] = None
    ai_video_base_url: str = "https://api.example-ai-video-provider.com/v1"
    ai_video_preflight_path: str = "/health"
    ai_video_timeout_seconds: float = 10.0

    # Optional LLM-based query validator refinement.
    llm_validation_api_key: Optional[str] = None
    llm_validation_base_url: str = "https://api.anthropic.com/v1/messages"
    llm_validation_model: str = "claude-3-5-haiku-latest"

    worker_concurrency: int = 3

    # Structural bounds on the raw query string (src/validation/rule_based.py)
    # - enforced inclusively: a query of exactly query_min_length or exactly
    # query_max_length characters is accepted.
    query_min_length: int = 5
    query_max_length: int = 1024

    @classmethod
    def from_env(cls) -> "Settings":
        with open('/etc/asgi-video-service_config.json', 'r') as f:
            config = json.load(f)
        return cls(
            environment = config["ENVIRONMENT"],
            LOGLEVEL = config['LOGLEVEL'],
            SECRET_KEY = config["SECRET_KEY"] or "you-will-never-guess",
            JWT_SECRET_KEY = config["JWT_SECRET_KEY"] if "JWT_SECRET_KEY" in config and len(config["JWT_SECRET_KEY"]) >= 64 else secrets.token_hex(64),
            output_dir = Path(config["OUTPUT_DIR"] or "output"),
            default_generation_provider = config["GENERATION_PROVIDER"] or "simulated",
            max_video_seconds =_float(config, "MAX_VIDEO_SECONDS", 90.0),
            video_width = _int(config, "VIDEO_WIDTH", 3840),
            video_height = _int(config, "VIDEO_HEIGHT", 2160),
            video_fps = _int(config, "VIDEO_FPS", 60),
            nvenc_mode = config["NVENC_MODE"],
            tts_voice = config["TTS_VOICE"],
            ai_video_api_key = os.environ.get("AI_VIDEO_API_KEY") or None,
            ai_video_base_url = config["AI_VIDEO_BASE_URL"] if "AI_VIDEO_BASE_URL" in config else None,
            ai_video_preflight_path = config["AI_VIDEO_PREFLIGHT_PATH"] if "AI_VIDEO_PREFLIGHT_PATH" in config else None,
            ai_video_timeout_seconds = _float(config, "AI_VIDEO_TIMEOUT_SECONDS", 10.0),
            llm_validation_api_key=(
                os.environ.get("LLM_VALIDATION_API_KEY")
                or os.environ.get("ANTHROPIC_API_KEY")
                or None
            ),
            llm_validation_base_url = config["LLM_VALIDATION_BASE_URL"] if "LLM_VALIDATION_BASE_URL" in config else "https://api.anthropic.com/v1/messages",
            llm_validation_model = config["LLM_VALIDATION_MODEL"] if "LLM_VALIDATION_MODEL" in config else "claude-3-5-haiku-latest",
            worker_concurrency =_int(config, "WORKER_CONCURRENCY", 3),
            query_min_length =_int(config, "QUERY_MIN_LENGTH", 5),
            query_max_length =_int(config, "QUERY_MAX_LENGTH", 1024)
        )
    """
    https://docs.python.org/3/library/logging.html
    The level parameter now accepts a string representation of the level such as ‘INFO’ as an alternative to the integer constants such as INFO.
    """
    logging.getLogger("httpx").setLevel(logging.WARNING)
    """
    https://realpython.com/python-modulo-string-formatting/#fine-tune-your-output-with-conversion-flags
    -	Justification of values that are shorter than the specified field width
    The Hyphen-Minus Flag (-)
    When a formatted value is shorter than the specified field width, it’s usually right-justified in the field. The hyphen-minus (-) flag causes the value to be left-justified in the specified field instead.
    """
    if environment == "development":
        logging.basicConfig(filename='/var/log/asgi-video-service/log', filemode='w', format='%(asctime)s %(levelname)-8s %(message)s', level=LOGLEVEL, datefmt='%Y-%m-%d %H:%M:%S')
    else:
        logging.basicConfig(handlers=[
            TimedRotatingFileHandler(filename='/var/log/asgi-video-service/log', when='d', interval=1, backupCount=3),
            logging.StreamHandler(sys.stdout)
        ], format='%(asctime)s %(levelname)-8s %(message)s', level=LOGLEVEL, datefmt='%Y-%m-%d %H:%M:%S')
    