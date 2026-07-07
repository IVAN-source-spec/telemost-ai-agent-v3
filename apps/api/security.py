import os

from fastapi import Header, HTTPException


async def require_node_api_token(authorization: str | None = Header(default=None)) -> None:
    require_token = os.getenv("BOT_NODE_REQUIRE_API_TOKEN", "0").lower() in (
        "1",
        "true",
        "yes",
    )
    if not require_token:
        return

    expected_token = os.getenv("BOT_NODE_API_TOKEN", "").strip()
    if not expected_token:
        raise HTTPException(status_code=500, detail="BOT_NODE_API_TOKEN is not configured")

    expected_header = f"Bearer {expected_token}"
    if authorization != expected_header:
        raise HTTPException(status_code=401, detail="Invalid or missing bot-node API token")
