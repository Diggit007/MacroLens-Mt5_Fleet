from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr, Field, model_validator
from typing import Optional
from pathlib import Path

class Settings(BaseSettings):
    # Security
    META_API_TOKEN: SecretStr = Field(..., description="MetaApi Cloud Token")
    OPENAI_API_KEY: Optional[SecretStr] = None  # Legacy, kept for backward compat
    NVIDIA_API_KEY: Optional[SecretStr] = None  # For Kimi K2.5
    DEEPSEEK_API_KEY: Optional[SecretStr] = None  # For DeepSeek V3
    GLM_API_KEY: Optional[SecretStr] = None  # For GLM 4.7 (Zhipu AI / Z.ai)
    
    # Model Configuration
    LLM_MODEL: str = "deepseek-chat"  # deepseek-chat, glm-4.7, moonshotai/kimi-k2.5
    AI_PROVIDER: str = "deepseek"  # deepseek, glm, nvidia
    ALLOW_MOCK_AUTH: bool = False
    USE_MOCK_AI: bool = False  # Disabled for Production
    
    # Paths
    LOCAL_MT5_PATH: Optional[Path] = None
    
    # Redis (Phase 7)
    REDIS_URL: str = "redis://localhost:6379/0"
    USE_REDIS: bool = False

    # Event Trading (Phase 11)
    EVENT_TRADING_MODE: str = "SIGNAL_ONLY"  # SIGNAL_ONLY, STRADDLE, DIRECTIONAL
    
    # App Config
    APP_BASE_URL: Optional[str] = "http://localhost:3000"
    META_API_ACCOUNT_ID: Optional[str] = None # Added for Event Trader compatibility
    DEFAULT_ACCOUNT_ID: Optional[str] = None  # Added to prevent Agent Crash (Analysis Requests)

    # V2 Config
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @model_validator(mode='after')
    def set_default_account(self):
        if not self.DEFAULT_ACCOUNT_ID and self.META_API_ACCOUNT_ID:
            self.DEFAULT_ACCOUNT_ID = self.META_API_ACCOUNT_ID
        return self

settings = Settings()
