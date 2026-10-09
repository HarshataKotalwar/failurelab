import httpx


def get_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(3.0, connect=1.0))
