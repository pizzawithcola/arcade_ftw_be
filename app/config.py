from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Arcade FTW Backend"
    api_key: str = "dev-key"
    cors_origins: str = ""

    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "arcade_ftw"
    db_user: str = "arcade_ftw"
    db_password: str = "arcade_ftw"

    @property
    def database_url(self) -> str:
        """Async DSN. Only the password comes from a secret; the rest is plain env."""
        return (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


settings = Settings()


def cors_origin_list() -> list[str]:
    return [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]
