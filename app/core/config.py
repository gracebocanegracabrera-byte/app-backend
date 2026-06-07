from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://localhost:5432/mvp"
    REDIS_URL: str = "redis://localhost:6379"

    SECRET_KEY: str = "change_me"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    OPENROUTER_API_KEY: str = ""
    OPENROUTER_REFERER: str = ""
    OPENROUTER_TITLE: str = ""

    MODEL_A1: str = "google/gemma-3-27b-it:free"
    MODEL_A2: str = "nvidia/llama-3.1-nemotron-70b-instruct:free"
    MODEL_A3: str = "meta-llama/llama-3.3-70b-instruct:free"
    MODEL_A4: str = "deepseek/deepseek-r1:free"
    MODEL_A5: str = "mistralai/mistral-7b-instruct:free"
    MODEL_FALLBACK: str = "meta-llama/llama-3.1-8b-instruct:free"

    FRONTEND_URL: str = "http://localhost:4200"

    model_config = SettingsConfigDict(
        env_file="../../.env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
