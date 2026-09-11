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

def _int(name: str, default: int) -> int:
    val = os.environ.get(name)
    return int(val) if val not in (None, "") else default


def _float(name: str, default: float) -> float:
    val = os.environ.get(name)
    return float(val) if val not in (None, "") else default

class ConfigSingleton(type): # Inherit from "type" in order to gain access to method __call__
    __registry = {}
    def __call__(cls, *args, **kwargs):
        registry = type(cls).__registry
        if cls not in registry:
              registry[cls] = (super().__call__(*args, **kwargs), args, kwargs)
        elif registry[cls][1] != args or registry(cls)[2] != kwargs:
              raise TypeError(f"Class already initialized with different arguments!")
        return registry[cls][0]

#@dataclass(frozen=True)
class Settings(metaclass=ConfigSingleton):
    LOGLEVEL:str = None
    TESTING = False
    SECRET_KEY:str = None
    JWT_SECRET_KEY:str = None
    def __new__(cls, *args, **kwargs):
        return super().__new__(cls)
    def __init__(self, environment="Development"):
        with open('/etc/stem-video-service_config.json', 'r') as f:
            config = json.load(f)
        self.LOGLEVEL = config['LOGLEVEL']
        self.SECRET_KEY = config["SECRET_KEY"] or "you-will-never-guess"
        if "JWT_SECRET_KEY" in config and len(config["JWT_SECRET_KEY"]) >= 64:
            self.JWT_SECRET_KEY = config["JWT_SECRET_KEY"]
        else:
            self.JWT_SECRET_KEY = secrets.token_hex(64) # SHA512 requirement

        self.output_dir: Path = Path("output")

        # "simulated" (default, offline) or "ai" (real provider, pluggable).
        self.default_generation_provider: str = "simulated"

        # Hard requirements: <=90s, 4K, 60fps.
        self.max_video_seconds: float = 90.0
        self.video_width: int = 3840
        self.video_height: int = 2160
        self.video_fps: int = 60
        # "auto" probes ffmpeg for an NVIDIA NVENC encoder and uses it if
        # present, falling back to libx264 otherwise. "on"/"off" force it.
        self.nvenc_mode: str = "auto"

        # edge-tts voice for the simulated provider's narration.
        self.tts_voice: str = "en-US-AriaNeural"

        # Generic/vendor-agnostic real AI video-generation provider.
        self.ai_video_api_key: Optional[str] = None
        self.ai_video_base_url: str = "https://api.example-ai-video-provider.com/v1"
        self.ai_video_preflight_path: str = "/health"
        self.ai_video_timeout_seconds: float = 10.0

        # Optional LLM-based query validator refinement.
        self.llm_validation_api_key: Optional[str] = None
        self.llm_validation_base_url: str = "https://api.anthropic.com/v1/messages"
        self.llm_validation_model: str = "claude-3-5-haiku-latest"

        self.worker_concurrency: int = _int("WORKER_CONCURRENCY", 3)
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
        if config["ENVIRONMENT"] == "development":
            logging.basicConfig(filename='/var/log/stem-video-generation/log', filemode='w', format='%(asctime)s %(levelname)-8s %(message)s', level=self.LOGLEVEL, datefmt='%Y-%m-%d %H:%M:%S')
        else:
            logging.basicConfig(handlers=[
                TimedRotatingFileHandler(filename='/var/log/stem-video-generation/log', when='d', interval=1, backupCount=3),
                logging.StreamHandler(sys.stdout)
            ], format='%(asctime)s %(levelname)-8s %(message)s', level=self.LOGLEVEL, datefmt='%Y-%m-%d %H:%M:%S')
settings = Settings()