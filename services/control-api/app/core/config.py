from pydantic_settings import BaseSettings
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    postgres_db: str = "failurelab"
    postgres_user: str = "failurelab"
    postgres_password: str = "development-only-change-me"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    order_service_url: str = "http://localhost:8001"
    inventory_service_url: str = "http://localhost:8002"
    payment_service_url: str = "http://localhost:8003"

    @property
    def database_url(self) -> str:
        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        ).render_as_string(hide_password=False)


settings = Settings()
