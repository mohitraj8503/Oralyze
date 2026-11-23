"""Configuration module for Oralyze."""
import os
from dotenv import load_dotenv

# Load .env if present
load_dotenv()


class Config:
    # Flag indicating whether to use mock offline services
    # Default to 1 (True) if IBM_API_KEY is missing or MOCK_MODE is set to '1' or 'true'
    _raw_mock = os.getenv("MOCK_MODE", "").strip().lower()
    _api_key = os.getenv("IBM_API_KEY", "").strip()

    if _raw_mock in ("0", "false", "no"):
        MOCK_MODE = False
    elif _raw_mock in ("1", "true", "yes"):
        MOCK_MODE = True
    else:
        # If not explicitly specified, auto-enable mock mode if API key is absent
        MOCK_MODE = not bool(_api_key)

    # IBM Cloud Common
    IBM_API_KEY = _api_key

    # watsonx.ai Granite
    WATSONX_URL = os.getenv("WATSONX_URL", "https://us-south.ml.cloud.ibm.com").rstrip("/")
    WATSONX_PROJECT_ID = os.getenv("WATSONX_PROJECT_ID", "").strip()
    GRANITE_MODEL_ID = os.getenv("GRANITE_MODEL_ID", "ibm/granite-3-3-8b-instruct").strip()

    # Watson Speech to Text (STT)
    STT_URL = os.getenv("STT_URL", "").rstrip("/")

    # Watson Text to Speech (TTS)
    TTS_URL = os.getenv("TTS_URL", "").rstrip("/")

    # Sarvam AI Voice Configuration
    SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "").strip()
    SARVAM_DAILY_CALL_LIMIT = int(os.getenv("SARVAM_DAILY_CALL_LIMIT", "300"))
    SARVAM_TTS_MODEL = os.getenv("SARVAM_TTS_MODEL", "bulbul:v3").strip()
    SARVAM_STT_MODEL = os.getenv("SARVAM_STT_MODEL", "saaras:v3").strip()
    SARVAM_TRANSLATE_MODEL = os.getenv("SARVAM_TRANSLATE_MODEL", "mayura:v1").strip()
    SARVAM_DEFAULT_SPEAKER = os.getenv("SARVAM_DEFAULT_SPEAKER", "shubh").strip()

    # Cloudant NoSQL Database
    CLOUDANT_URL = os.getenv("CLOUDANT_URL", "").rstrip("/")
    CLOUDANT_DB = os.getenv("CLOUDANT_DB", "oralyze_scorecards").strip()

    # App settings
    PORT = int(os.getenv("PORT", "5000"))
    MAX_QUESTIONS = int(os.getenv("MAX_QUESTIONS", "5"))

    @classmethod
    def missing_keys(cls) -> list[str]:
        """Returns list of required IBM keys that are missing for live mode."""
        missing = []
        if not cls.IBM_API_KEY:
            missing.append("IBM_API_KEY")
        if not cls.WATSONX_PROJECT_ID:
            missing.append("WATSONX_PROJECT_ID")
        if not cls.STT_URL:
            missing.append("STT_URL")
        if not cls.TTS_URL:
            missing.append("TTS_URL")
        return missing
