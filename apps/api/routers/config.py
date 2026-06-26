from fastapi import APIRouter, HTTPException
from ..schemas import ReconnectConfigRequest, ReconnectConfigResponse

config_router = APIRouter(prefix="/api/v1/config", tags=["config"])

_config_store = {"max_attempts": 3, "interval_sec": 10}

@config_router.post("/reconnect", response_model=ReconnectConfigResponse)
async def update_reconnect_config(req: ReconnectConfigRequest):
    if req.max_attempts <= 0 or req.interval_sec <= 0:
        raise HTTPException(status_code=400, detail="Values must be positive")
    _config_store["max_attempts"] = req.max_attempts
    _config_store["interval_sec"] = req.interval_sec
    return ReconnectConfigResponse(**_config_store)

@config_router.get("/reconnect", response_model=ReconnectConfigResponse)
async def get_reconnect_config():
    return ReconnectConfigResponse(**_config_store)