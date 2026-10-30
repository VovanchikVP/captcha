"""Схемы запросов и ответов API."""

from pydantic import BaseModel, Field


class AnswerIn(BaseModel):
    answer: str = Field(min_length=1, max_length=255)


class PushKeys(BaseModel):
    p256dh: str = Field(min_length=1, max_length=255)
    auth: str = Field(min_length=1, max_length=255)


class SubscriptionIn(BaseModel):
    """То, что отдаёт PushSubscription.toJSON() в браузере."""

    endpoint: str = Field(min_length=1, max_length=2048)
    keys: PushKeys


class EndpointIn(BaseModel):
    endpoint: str = Field(min_length=1, max_length=2048)
