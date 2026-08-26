from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: str = "development"
    database_url: str = "postgresql+psycopg://metrics:metrics@postgres:5432/metrics"
    redis_url: str = "redis://redis:6379/0"
    clickhouse_host: str = "clickhouse"
    clickhouse_port: int = 8123
    clickhouse_database: str = "analytics"
    clickhouse_user: str = "agent_readonly"
    clickhouse_password: SecretStr = Field(default=SecretStr(""), repr=False)

    jwt_secret: SecretStr = Field(default=SecretStr("change-me-in-production"), repr=False)
    jwt_algorithm: str = "HS256"
    jwt_ttl_minutes: int = 480

    deepseek_api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    deepseek_api_base: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"

    dashscope_api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    dashscope_api_base: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    dashscope_embedding_model: str = "text-embedding-v4"
    dashscope_embedding_dimensions: int = 1024

    max_query_rows: int = 1000
    max_query_seconds: int = 30
    max_repair_attempts: int = 2


@lru_cache
def get_settings() -> Settings:
    return Settings()

