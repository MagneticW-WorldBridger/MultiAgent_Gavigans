from fastapi import APIRouter, HTTPException, status

from app.auth.schemas import AuthResponse, LoginRequest, RegisterRequest
from app.auth.utils import create_access_token, hash_password, verify_password
from app.db import db

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest):
    existing = await db.user.find_unique(where={"email": body.email})
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = await db.user.create(
        data={
            "email": body.email,
            "passwordHash": hash_password(body.password),
            "name": body.name,
        }
    )
    token = create_access_token(user.id)
    return AuthResponse(access_token=token, user_id=user.id, name=user.name)


@router.post("/login", response_model=AuthResponse)
async def login(body: LoginRequest):
    user = await db.user.find_unique(where={"email": body.email})
    if not user or not verify_password(body.password, user.passwordHash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    token = create_access_token(user.id)
    return AuthResponse(access_token=token, user_id=user.id, name=user.name)
