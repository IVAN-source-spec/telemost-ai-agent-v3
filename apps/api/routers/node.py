from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from ..dependencies import get_bot_selector
from ..node_config import get_global_bot_id, get_node_id, get_node_name
from ..security import require_node_api_token

node_router = APIRouter(prefix="/api/v1/node", tags=["node"])


@node_router.get("/status")
async def get_node_status(
    _auth=Depends(require_node_api_token),
    bot_selector=Depends(get_bot_selector),
):
    bots = []
    if hasattr(bot_selector, "list_bots"):
        bots = await bot_selector.list_bots()

    node_id = get_node_id()
    node_name = get_node_name()
    enriched_bots = []
    for bot in bots:
        bot_id = bot.get("bot_id")
        enriched_bots.append(
            {
                **bot,
                "node_id": node_id,
                "node_name": node_name,
                "global_bot_id": get_global_bot_id(bot_id) if bot_id else None,
            }
        )

    busy = sum(1 for bot in enriched_bots if bot.get("status") == "busy")
    idle = sum(1 for bot in enriched_bots if bot.get("status") == "idle")

    return {
        "node_id": node_id,
        "node_name": node_name,
        "status": "online",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "bots_total": len(enriched_bots),
        "bots_idle": idle,
        "bots_busy": busy,
        "bots": enriched_bots,
    }
