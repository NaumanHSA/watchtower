from fastapi import APIRouter

router = APIRouter(tags=["health"])

@router.get("/")
async def index():
    return "App is up and running..."

@router.get("/health")
async def health():
    return {"status": "ok"}
