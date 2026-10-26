from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.auth import router as auth_router
from app.database import Base, engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="Paint Rush", lifespan=lifespan)
app.include_router(auth_router)
