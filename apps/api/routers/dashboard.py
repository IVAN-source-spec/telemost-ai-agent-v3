from fastapi import APIRouter
from ..schemas import DashboardResponse
from apps.web.dashboard import build_dashboard_state

dashboard_router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@dashboard_router.get("/", response_model=DashboardResponse)
async def get_dashboard():
    state = build_dashboard_state(
        active_bots=1,
        active_meetings=2,
        blocked_tasks=0,
        reconnect_max_attempts=3,
        reconnect_interval_sec=10,
    )
    return DashboardResponse(
        summary=state["summary"],
        reconnect_policy=state["reconnect_policy"],
    )