import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import event_log
from app.cards import DECK_SIZE, list_cards
from app.characters import list_characters
from app.auth import router as auth_router
from app.database import Base, engine
from app.decks import router as deck_router
from app.websocket import router as websocket_router
from app.websocket import stop_timers


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    yield
    await stop_timers()
    await asyncio.to_thread(event_log.close)


app = FastAPI(title="Paint Rush", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(deck_router)
app.include_router(websocket_router)


@app.get("/characters")
def get_characters():
    return {"characters": list_characters()}


@app.get("/cards")
def get_cards():
    return {"deck_size": DECK_SIZE, "cards": list_cards()}
