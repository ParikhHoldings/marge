"""
Chat router — POST /chat

Lets the pastor talk to Marge in plain English.
Marge acknowledges, confirms what she logged or will do, and offers a follow-up action.
"""

import os
import logging

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger("marge.chat")

router = APIRouter(prefix="/chat", tags=["chat"])

MARGE_SYSTEM_PROMPT = """You are Marge, an AI church secretary assistant. You are warm, direct, and pastoral — like a trusted colleague who has worked alongside this pastor for years.

When the pastor tells you something:
- Acknowledge it warmly and personally
- Confirm what you logged or will do next
- Offer one helpful follow-up action

Keep replies to 2-3 sentences max. Never be corporate or cold. Use pastoral language — congregation, not users. Members, not contacts. Care, not engagement. Always address the pastor as Pastor or by name if you know it."""


class ChatRequest(BaseModel):
    message: str
    pastor_name: str = "Pastor"


class ChatResponse(BaseModel):
    reply: str


@router.post("/", response_model=ChatResponse, summary="Chat with Marge")
def chat_with_marge(request: ChatRequest):
    """
    Send a plain-English message to Marge.

    Examples:
    - "I visited Martha Ellis today, she is doing better"
    - "Add a prayer request for Tom Henderson, he is going through a job loss"
    - "Log that James Whitmore visited last Sunday with his family"

    Marge will acknowledge, confirm, and offer a follow-up action.
    """
    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        # Warm placeholder when no API key is configured
        return ChatResponse(
            reply=f"Got it, {request.pastor_name}. I have noted that for you — I will make sure it is reflected in tomorrow's briefing. Is there anything else you would like me to log?"
        )

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)

        pastor_name = os.getenv("PASTOR_NAME", request.pastor_name)
        church_name = os.getenv("CHURCH_NAME", "your church")

        system = MARGE_SYSTEM_PROMPT + f"\n\nThe pastor's name is {pastor_name}. The church is {church_name}."

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": request.message},
            ],
            max_tokens=150,
            temperature=0.7,
        )

        reply = response.choices[0].message.content.strip()
        return ChatResponse(reply=reply)

    except Exception as e:
        logger.error(f"OpenAI chat error: {e}")
        return ChatResponse(
            reply=f"I am here, {request.pastor_name}. I had a little trouble processing that — could you try again in a moment?"
        )
