from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from controller_helpers import MONGO_URI, make_settings
from pymongo import MongoClient

from watchtower_controller.app import create_app
from watchtower_controller.settings import Settings


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
async def app(settings: Settings):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        yield app
    MongoClient(MONGO_URI).drop_database(settings.mongo_db)


@pytest.fixture
async def client(app) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c
