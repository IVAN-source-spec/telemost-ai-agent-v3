from fastapi import APIRouter, Depends, HTTPException
from ..schemas import CreateMeetingRequest, TaskResponse, TaskStatusResponse
from ..services import create_meeting_task
from ..dependencies import get_queue_publisher, get_bot_selector, get_metadata_store
from ..task_store import create_task, get_task
from ..session_utils import generate_session_id

meetings_router = APIRouter(prefix="/api/v1/meetings", tags=["meetings"])


@meetings_router.post("/", response_model=TaskResponse)
async def create_meeting(
        req: CreateMeetingRequest,
        queue_publisher=Depends(get_queue_publisher),
        bot_selector=Depends(get_bot_selector),
        metadata_store=Depends(get_metadata_store),
):
    if not req.session_id or req.session_id == "auto" or req.session_id == "string":
        req.session_id = generate_session_id()
        print(f"[API] Auto-generated session_id: {req.session_id}")
    try:
        response = await create_meeting_task(req, bot_selector, queue_publisher, metadata_store)
        create_task(response.task_id, status="queued")
        return response
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@meetings_router.get("/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str):
    data = get_task(task_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskStatusResponse(
        task_id=task_id,
        status=data["status"],
        result=data.get("result"),
        created_at=data["created_at"],
    )
