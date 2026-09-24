from __future__ import annotations

from pydantic import BaseModel, Field


class OrderCreate(BaseModel):
    firstName: str = Field(min_length=1)
    lastName: str = Field(min_length=1)
    email: str = Field(min_length=1)
    phone: str = Field(min_length=1)
    company: str = Field(min_length=1)
    challenge: str = Field(min_length=1)


class OrderCreated(BaseModel):
    order_id: str
    order_number: str
    status: str
    checkout_url: str
    checkout_token: str
    transaction_id: str


class OrderStatus(BaseModel):
    order_id: str
    order_number: str
    status: str
    paid: bool
    delivery_status: str | None