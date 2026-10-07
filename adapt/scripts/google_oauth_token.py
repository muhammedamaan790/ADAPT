"""One-time Google Ads OAuth: exchange the desktop client for a refresh token and store it in .env (spec §9.4).

Usage (from adapt/):  uv run python scripts/google_oauth_token.py [--no-browser]
A browser window opens (or, with --no-browser, the URL is printed); sign in with an account listed
as a test user on the OAuth consent screen and allow
"Manage your AdWords campaigns". The refresh token is written into .env and never printed.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
SCOPES = ["https://www.googleapis.com/auth/adwords"]


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$", line)
        if m:
            values[m[1]] = m[2]
    return values


def write_env_value(path: Path, key: str, value: str) -> None:
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(rf"^{key}=.*$", re.MULTILINE)
    line = f"{key}={value}"
    text = pattern.sub(lambda _: line, text) if pattern.search(text) else text.rstrip("\n") + f"\n{line}\n"
    path.write_text(text, encoding="utf-8")


def main() -> int:
    if not ENV_PATH.exists():
        print(f"missing {ENV_PATH}; copy .env.example to .env and fill GOOGLE_ADS_CLIENT_ID/SECRET first")
        return 1
    env = read_env(ENV_PATH)
    client_id, client_secret = env.get("GOOGLE_ADS_CLIENT_ID"), env.get("GOOGLE_ADS_CLIENT_SECRET")
    if not client_id or not client_secret:
        print("GOOGLE_ADS_CLIENT_ID and GOOGLE_ADS_CLIENT_SECRET must be set in .env")
        return 1
    flow = InstalledAppFlow.from_client_config(
        {"installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }},
        scopes=SCOPES,
    )
    # prompt=consent guarantees Google returns a refresh token even if this account consented before.
    # --no-browser prints the URL instead, to open in an Incognito window signed into only the test user
    # (the consent page can fail with a bare 400 when several Google accounts are signed in).
    open_browser = "--no-browser" not in sys.argv
    creds = flow.run_local_server(
        port=8765, prompt="consent", access_type="offline", open_browser=open_browser,
        authorization_prompt_message="Open this URL in your browser:\n{url}\n",
    )
    if not creds.refresh_token:
        print("Google returned no refresh token; revoke the app at myaccount.google.com/permissions and retry")
        return 1
    write_env_value(ENV_PATH, "GOOGLE_ADS_REFRESH_TOKEN", creds.refresh_token)
    print(f"refresh token saved to {ENV_PATH} (GOOGLE_ADS_REFRESH_TOKEN)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
