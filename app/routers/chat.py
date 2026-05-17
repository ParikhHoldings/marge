"""
Chat router — POST /chat
"""

from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.routers.assistant import AssistantChatRequest, assistant_chat

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    pastor_name: str = "Pastor"
    mode: Literal["demo", "live"] = "live"


class ChatResponse(BaseModel):
    reply: str
    action: str
    mode: Literal["demo", "live"]
    saved: bool


@router.post("/", response_model=ChatResponse, summary="Chat with Marge")
def chat_with_marge(
    request: ChatRequest,
    x_marge_account_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    response = assistant_chat(
        AssistantChatRequest(message=request.message, mode=request.mode),
        x_marge_account_token=x_marge_account_token,
        db=db,
    )
    return ChatResponse(
        reply=response.reply,
        action=response.intent,
        mode=response.mode,
        saved=response.saved,
    )
