from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    inventory_service_url: str = "http://localhost:8002"
    payment_service_url: str = "http://localhost:8003"


settings = Settings()
