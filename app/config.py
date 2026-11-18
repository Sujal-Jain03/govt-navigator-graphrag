import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "DATA" / "notifications"
PROCESSED_DIR = BASE_DIR / "processed_data"
EXTRACTIONS_DIR = BASE_DIR / "processed_data" / "extractions"

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_BASE_URL = "https://api.groq.com/openai/v1"

ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "")
ZHIPU_MODEL = os.getenv("ZHIPU_MODEL", "glm-4.7-flash")
ZHIPU_BASE_URL = "https://api.z.ai/api/paas/v4"

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "openai/gpt-oss-20b")
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")