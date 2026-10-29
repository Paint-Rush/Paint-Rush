from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.auth import router as auth_router
from app.database import Base, engine
from app.websocket import router as websocket_router
from app.websocket import stop_timers


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    yield
    await stop_timers()


app = FastAPI(title="Paint Rush", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(websocket_router)
