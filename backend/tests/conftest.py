import pytest


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    # The application is asyncio-only (asyncio.to_thread, asyncio.Queue, etc.).
    # Newer anyio versions parametrize tests over every installed backend, and
    # trio is present as a transitive streamlink dependency; pin the suite to
    # asyncio so tests are deterministic across environments.
    return "asyncio"
