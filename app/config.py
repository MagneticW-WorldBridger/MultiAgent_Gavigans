from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_expiration_minutes: int = 60 * 24  # 24 hours
    google_api_key: str
    default_model: str = "gemini-2.0-flash"

    model_config = {"env_file": ".env"}


settings = Settings()
