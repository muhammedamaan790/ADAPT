"""Integration fixtures: a seeded fixture world served in-process, and an ADAPT workspace DB.

integration/ is the only test location (besides the evalharness package) that imports both `adapt` and `world`;
it drives ADAPT against the world over HTTP exactly as in production, via the world app's TestClient
(an httpx.Client), so no ports are opened.
"""

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from adapt.core.db import Database
from adapt.ingest.http import SourceHttp
from world.main import create_app
from world.testing import make_backbone, make_global_ads, seed_fixture_world


@pytest.fixture(scope="session")
def seeded_world(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("integration")
    bb = make_backbone(root / "backbone")
    ga = make_global_ads(root / "global_ads.csv")
    return seed_fixture_world(root / "world", bb, ga)


@pytest.fixture
def world(seeded_world, tmp_path):
    d = tmp_path / "world"
    shutil.copytree(seeded_world, d)
    with TestClient(create_app(world_dir=d)) as client:
        yield client


@pytest.fixture
def http(world) -> SourceHttp:
    return SourceHttp("http://testserver", client=world, sleep=lambda s: None)


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "workspace.duckdb")
    yield d
    d.close()
