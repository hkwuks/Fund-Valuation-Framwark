from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

_PROJECT_ROOT = Path(__file__).parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True, extra="ignore")
    APP_NAME: str = "智能理财Agent"
    APP_NAME: str = "智能理财Agent"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = True

    API_PREFIX: str = "/api"

    CORS_ORIGINS: list = ["http://localhost:3000", "http://127.0.0.1:3000"]

    DATA_DIR: str = str(_PROJECT_ROOT / "data")

    TUSHARE_TOKEN: Optional[str] = None

    FRED_API_KEY: Optional[str] = None

    MODEL_DIR: str = str(_PROJECT_ROOT / "data" / "backend" / "models")
    MODEL_MAX_AGE_DAYS: int = 7
    

settings = Settings()
