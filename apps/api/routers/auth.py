from fastapi import APIRouter
from ..schemas import QrResponse
from core.auth.qr_auth import AUTH_QR_URL
import qrcode
import io
import base64

auth_router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@auth_router.post("/qr", response_model=QrResponse)
async def generate_qr():
    qr = qrcode.QRCode(box_size=10, border=4)
    qr.add_data(AUTH_QR_URL)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffered = io.BytesIO()
    img.save(buffered, format="PNG")
    qr_base64 = base64.b64encode(buffered.getvalue()).decode()
    return QrResponse(url=AUTH_QR_URL, qr_code=qr_base64)


@auth_router.post("/qr/start", response_model=QrResponse)
async def start_qr_auth():
    return await generate_qr()
