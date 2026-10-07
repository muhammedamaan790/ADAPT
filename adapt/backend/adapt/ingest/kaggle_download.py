"""Download the public datasets into data/raw/<key>/ (spec §1).

Needs Kaggle credentials: either %USERPROFILE%\\.kaggle\\kaggle.json or KAGGLE_USERNAME + KAGGLE_KEY.
Usage:  uv run python -m adapt.ingest.kaggle_download --raw-dir data/raw
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from adapt.ingest.sources import ALL_DATASETS


def download(raw_dir: Path, only: set[str] | None = None) -> None:
    from kaggle.api.kaggle_api_extended import KaggleApi  # imported lazily: it reads credentials on import

    api = KaggleApi()
    api.authenticate()
    for ds in ALL_DATASETS:
        if only and ds.key not in only:
            continue
        target = raw_dir / ds.key
        target.mkdir(parents=True, exist_ok=True)
        print(f"downloading {ds.slug} -> {target}")
        api.dataset_download_files(ds.slug, path=str(target), unzip=True, quiet=False)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    ap.add_argument("--only", nargs="*", help="dataset keys: thelook global_ads kag_facebook")
    args = ap.parse_args(argv)
    download(args.raw_dir, set(args.only) if args.only else None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
