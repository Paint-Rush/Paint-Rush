import os
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from pwdlib import PasswordHash
from pydantic import AfterValidator, BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User

SECRET_KEY = os.environ["JWT_SECRET_KEY"]
ALGORITHM = "HS256"
TOKEN_EXPIRE = timedelta(hours=24)

password_hash = PasswordHash.recommended()
DUMMY_HASH = password_hash.hash("dummy-password")

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
DB = Annotated[Session, Depends(get_db)]


class Credentials(BaseModel):
    username: Annotated[str, Field(pattern=r"^[A-Za-z0-9_]{3,20}$"), AfterValidator(str.lower)]
    password: Annotated[str, Field(min_length=8, max_length=32)]


class UserOut(BaseModel):
    id: int
    username: str


class Token(BaseModel):
    access_token: str
    token_type: str


# 회원가아아아아입 라우터
@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=UserOut)
def register(body: Credentials, db: DB):
    user = User(username=body.username, password_hash=password_hash.hash(body.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already exists")
    return {"id": user.id, "username": user.username}


#로그이이이인 라우터
@router.post("/login", response_model=Token)
def login(body: Credentials, db: DB):
    user = db.scalar(select(User).where(User.username == body.username))
    valid = password_hash.verify(body.password, user.password_hash if user else DUMMY_HASH)
    if not user or not valid:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    expire = datetime.now(timezone.utc) + TOKEN_EXPIRE
    token = jwt.encode({"sub": str(user.id), "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)
    return {"access_token": token, "token_type": "bearer"}
