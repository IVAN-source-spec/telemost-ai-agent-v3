import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright
from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.auth.qr_auth import AUTH_QR_URL
from core.auth.session_portability import create_session_artifact, write_session_artifact

DEFAULT_OWNER_REPO = "IVAN-source-spec/telemost-ai-agent-v3"
DEFAULT_STORAGE_STATE_PATH = "auth_state.json"
DEFAULT_ARTIFACT_PATH = "auth_state.artifact.json"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Authorize in Yandex Passport, save Playwright storage_state, "
            "and sign a portable session artifact."
        )
    )
    parser.add_argument(
        "--session-id",
        default=os.getenv("TELEMOST_SESSION_ID", "telemost-bot"),
        help="Stable id for this authorized browser session.",
    )
    parser.add_argument(
        "--owner-repo",
        default=os.getenv("TELEMOST_OWNER_REPO", DEFAULT_OWNER_REPO),
        help="Repository scope for the signed session artifact, in owner/repo form.",
    )
    parser.add_argument(
        "--storage-state",
        default=os.getenv("TELEMOST_AUTH_STATE_PATH", DEFAULT_STORAGE_STATE_PATH),
        help="Where to save Playwright storage_state JSON.",
    )
    parser.add_argument(
        "--artifact-path",
        default=os.getenv("TELEMOST_SESSION_ARTIFACT_PATH", DEFAULT_ARTIFACT_PATH),
        help="Where to save the signed portable session artifact JSON.",
    )
    parser.add_argument(
        "--ttl-seconds",
        type=int,
        default=int(os.getenv("TELEMOST_SESSION_TTL_SECONDS", "300")),
        help="How long the signed artifact is valid.",
    )
    return parser


async def save_auth_state(args: argparse.Namespace) -> None:
    secret_key = os.getenv("TELEMOST_SESSION_SECRET_KEY")
    if not secret_key:
        raise RuntimeError("TELEMOST_SESSION_SECRET_KEY is required")

    storage_state_path = Path(args.storage_state)
    artifact_path = Path(args.artifact_path)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        print(f"[Auth] Open Yandex QR auth: {AUTH_QR_URL}")
        await page.goto(AUTH_QR_URL)
        input("[Auth] Scan QR / finish login in the browser, then press Enter...")

        await page.goto("https://telemost.yandex.ru/")
        await page.wait_for_load_state("networkidle")

        storage_state_path.parent.mkdir(parents=True, exist_ok=True)
        await context.storage_state(path=str(storage_state_path))
        await browser.close()

    issued_at = _utc_now_iso()
    artifact = create_session_artifact(
        session_id=args.session_id,
        owner_repo=args.owner_repo,
        issued_at=issued_at,
        secret_key=secret_key,
        ttl_seconds=args.ttl_seconds,
    )
    write_session_artifact(artifact_path, artifact)

    print(f"[Auth] Saved browser storage state: {storage_state_path}")
    print(f"[Auth] Saved signed session artifact: {artifact_path}")
    print(f"[Auth] Artifact expires at: {artifact['expires_at']}")


def main() -> int:
    args = build_parser().parse_args()
    asyncio.run(save_auth_state(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
