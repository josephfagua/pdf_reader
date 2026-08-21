"""
validation.py — Invoice-level business validation for MDIP.

Current rules:
- Taco Bamba: Customer PO must be "Verbal".
- Velvet Taco North Hills/Southend/Park Rd: Customer PO must match VO######.
- A blank PO or "Verbal" may be explicitly approved as an office-created
  second-delivery exception. The outbound CSV will use "Verbal".
"""

from dataclasses import dataclass
import re

from src.models import OrderDetails


VELVET_LOCATIONS = ("NORTH HILLS", "SOUTHEND", "PARK RD")
VALID_VELVET_PO = re.compile(r"^VO\d{6}$", re.IGNORECASE)


@dataclass(frozen=True)
class InvoiceValidationResult:
    invoice_path: str
    invoice_number: str | None
    customer_name: str | None
    client: str | None
    customer_po: str | None
    valid: bool
    can_approve_exception: bool
    message: str


def identify_client(customer_name: str | None) -> str | None:
    """Identify one of the currently supported client locations."""
    if not customer_name:
        return None

    normalized = " ".join(customer_name.upper().split())

    if "TACO BAMBA" in normalized:
        return "Taco Bamba"

    if "VELVET TACO" in normalized:
        for location in VELVET_LOCATIONS:
            if location in normalized:
                return f"Velvet Taco — {location.title()}"
        return "Velvet Taco — Unknown Location"

    return None


def validate_invoice(
    invoice_path: str,
    order_details: OrderDetails,
) -> InvoiceValidationResult:
    """Apply the client-specific Customer PO rule."""

    client = identify_client(order_details.customer_name)
    po = (order_details.customer_purchase_order or "").strip()
    invoice_number = order_details.invoice_number

    if client is None:
        return InvoiceValidationResult(
            invoice_path, invoice_number, order_details.customer_name,
            None, po or None, False, False,
            "Unsupported or unrecognized client/location.",
        )

    if client == "Taco Bamba":
        if po.lower() == "verbal":
            return InvoiceValidationResult(
                invoice_path, invoice_number, order_details.customer_name,
                client, "Verbal", True, False,
                "Customer PO is valid.",
            )

        if not po:
            return InvoiceValidationResult(
                invoice_path, invoice_number, order_details.customer_name,
                client, None, False, True,
                "Customer PO is blank. Taco Bamba requires 'Verbal', "
                "unless an office-created second-delivery exception is approved.",
            )

        return InvoiceValidationResult(
            invoice_path, invoice_number, order_details.customer_name,
            client, po, False, False,
            "Taco Bamba Customer PO must be 'Verbal'.",
        )

    if client.startswith("Velvet Taco"):
        if client.endswith("Unknown Location"):
            return InvoiceValidationResult(
                invoice_path, invoice_number, order_details.customer_name,
                client, po or None, False, False,
                "Velvet Taco location could not be identified.",
            )

        if VALID_VELVET_PO.fullmatch(po):
            return InvoiceValidationResult(
                invoice_path, invoice_number, order_details.customer_name,
                client, po.upper(), True, False,
                "Customer PO is valid.",
            )

        if not po or po.lower() == "verbal":
            return InvoiceValidationResult(
                invoice_path, invoice_number, order_details.customer_name,
                client, po or None, False, True,
                "Velvet Taco requires a Customer PO in the VO###### format. "
                "This invoice may qualify for the office-created "
                "second-delivery exception.",
            )

        return InvoiceValidationResult(
            invoice_path, invoice_number, order_details.customer_name,
            client, po, False, False,
            "Velvet Taco Customer PO must use the VO###### format.",
        )

    return InvoiceValidationResult(
        invoice_path, invoice_number, order_details.customer_name,
        client, po or None, False, False,
        "No validation rule is defined for this client.",
    )


def apply_exception(result: InvoiceValidationResult) -> InvoiceValidationResult:
    """Approve a blank/Verbal second-delivery exception."""
    if not result.can_approve_exception:
        raise ValueError("This invoice does not qualify for a PO exception.")

    return InvoiceValidationResult(
        invoice_path=result.invoice_path,
        invoice_number=result.invoice_number,
        customer_name=result.customer_name,
        client=result.client,
        customer_po=result.customer_po,
        valid=True,
        can_approve_exception=True,
        message="PO exception approved. CSV Customer PO will be populated with 'Verbal'.",
    )
