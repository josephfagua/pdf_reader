from pydantic import BaseModel, field_validator
from typing import Optional


class InvoiceItem(BaseModel):
    item_number: str
    description: str
    uom: str
    qty_shipped: int
    unit_price: float
    extended_amount: float


class OrderDetails(BaseModel):
    customer_number: Optional[str] = None
    customer_name: Optional[str] = None
    customer_purchase_order: Optional[str] = None
    delivery_date: Optional[str] = None
    invoice_number: Optional[str] = None
    total_cost: Optional[float] = None

    @field_validator("total_cost", mode="before")
    @classmethod
    def strip_commas(cls, value):
        """Allow '3,614.00' style strings to coerce cleanly into a float."""
        if isinstance(value, str):
            return value.replace(",", "")
        return value