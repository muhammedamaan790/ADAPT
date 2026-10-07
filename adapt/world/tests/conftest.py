"""Shared world fixtures: a dense synthetic backbone and a Global Ads sample (no real data needed in CI)."""

import shutil
from pathlib import Path

import pytest

from world.testing import make_backbone, make_global_ads, seed_fixture_world


@pytest.fixture(scope="session")
def backbone_dir(tmp_path_factory) -> Path:
    return make_backbone(tmp_path_factory.mktemp("backbone"))


@pytest.fixture(scope="session")
def global_ads_csv(tmp_path_factory) -> Path:
    return make_global_ads(tmp_path_factory.mktemp("ga") / "global_ads.csv")


@pytest.fixture(scope="session")
def seeded_world_dir(backbone_dir, global_ads_csv, tmp_path_factory) -> Path:
    """A seeded fixture world (truth + history + baseline). Copy it before mutating (see world_copy)."""
    return seed_fixture_world(tmp_path_factory.mktemp("seeded"), backbone_dir, global_ads_csv)


@pytest.fixture
def world_copy(seeded_world_dir, tmp_path) -> Path:
    dst = tmp_path / "world"
    shutil.copytree(seeded_world_dir, dst)
    return dst
