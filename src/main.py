import pymupdf
import pandas as pd
import re
import os

from src.models import InvoiceItem, OrderDetails
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------

def extract_text(pdf_path: str) -> str:
    """Extract text from every page of an invoice PDF in document order."""
    with pymupdf.open(pdf_path) as doc:
        all_text = [page.get_text() for page in doc]

    # Keep a page boundary so later cleanup cannot consume text from the next page.
    return "\f".join(all_text)


# ---------------------------------------------------------------------------
# Invoice cleanup
# ---------------------------------------------------------------------------

def _extract_invoice_total(text: str) -> str | None:
    """
    Extract the invoice total from the original PDF text.

    Martin's invoices can place a 'Number of PCS.' value between the
    'Invoice Total ($)' label and the actual dollar amount. The regex
    therefore allows an optional numeric value before the currency amount.
    """
    match = re.search(
        r"Invoice Total\s*\(\$\)\s*(?:[\d.,]+\s*)?\$([\d,]+\.\d{2})",
        text,
        flags=re.IGNORECASE,
    )

    return match.group(1) if match else None


def refine_data(raw_text: str) -> str:
    """
    Remove repeated invoice boilerplate and normalize invoice text.

    Cleanup is performed page-by-page so a PACA disclaimer that begins on
    page 1 cannot accidentally consume valid invoice items from page 2.
    """

    invoice_total = _extract_invoice_total(raw_text)

    pages = raw_text.split("\f")
    cleaned_pages = []

    headers_to_remove = [
        "Customer No.",
        "Customer Purchase Order",
        "Salesperson",
        "Truck/Route",
        "Order Date",
        "Terms",
        "Billing",
        "Units",
        "Qty.",
        "Ordered",
        "UOM",
        "Shipped",
        "Net 14 days",
        "Description",
        "Delivery Date",
        "Invoice No.",
        "Extended",
        "Amount",
        "Line",
        "Item",
        "Number",
    ]

    for page in pages:
        text = page

        def keep_buyer_name(match):
            block = match.group(0)
            buyer_match = re.search(
                r"SHIPPED TO:\s*\n?\s*([^\n\r]+)",
                block,
                flags=re.IGNORECASE,
            )
            return (
                f"\n{buyer_match.group(1).strip()}\n"
                if buyer_match else "\n"
            )

        text = re.sub(
            r"SOLD TO:.*?INVOICE",
            keep_buyer_name,
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )

        for header in headers_to_remove:
            text = re.sub(
                re.escape(header),
                "",
                text,
                flags=re.IGNORECASE,
            )

        text = re.sub(
            r"300 FORSYTH HALL DR STE A.*?REMITTANCE ADDRESS:",
            "",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )

        # Remove PACA footer content only within this page.
        text = re.sub(
            r"The perishable agricultural commodities listed on this invoice.*$",
            "",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )

        # Page 2 can begin in the middle of the PACA disclaimer.
        text = re.sub(
            r"and any receivables or proceeds from the sale of these commodities.*$",
            "",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )

        # The total is captured before cleanup and restored afterward.
        text = re.sub(
            r"Number of PCS\..*$",
            "",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )

        text = re.sub(r"Original Invoice\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"Continued on Page 2\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"Total WGT\.\s*", "", text, flags=re.IGNORECASE)

        cleaned_pages.append(text)

    text = "\n".join(cleaned_pages)

    if invoice_total:
        text += f"\nInvoice Total: ${invoice_total}\n"

    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Item parsing
# ---------------------------------------------------------------------------

_SIGNED_INTEGER = re.compile(r"^-?\d+$")
_DECIMAL = re.compile(r"^-?\d+\.\d+$")


def parse_items(text: str) -> list[InvoiceItem]:
    """
    Parse line items from cleaned invoice text.

    Martin's PDF text layout produces ten values per item:

        qty_shipped
        unit_price
        qty_ordered
        qty_shipped
        item_number
        uom
        line_number
        description
        placeholder
        extended_amount

    Credit/adjustment lines use the same structure but contain negative
    quantities and/or extended amounts. Negative integers are therefore
    valid and must not be rejected.
    """

    lines = [line.strip() for line in text.splitlines() if line.strip()]

    items: list[InvoiceItem] = []
    i = 0

    while i + 9 < len(lines):

        # The first value is the shipped quantity and the second is the
        # unit price. This pattern identifies the beginning of an item.
        if (
            _SIGNED_INTEGER.fullmatch(lines[i])
            and _DECIMAL.fullmatch(lines[i + 1])
            and _SIGNED_INTEGER.fullmatch(lines[i + 2])
            and _SIGNED_INTEGER.fullmatch(lines[i + 3])
        ):
            try:
                item = InvoiceItem(
                    item_number=lines[i + 4],
                    description=lines[i + 7],
                    uom=lines[i + 5],
                    qty_shipped=lines[i + 3],
                    unit_price=lines[i + 1],
                    extended_amount=lines[i + 9],
                )

                items.append(item)
                i += 10
                continue

            except (ValidationError, IndexError, ValueError):
                pass

        i += 1

    return items


# ---------------------------------------------------------------------------
# Order details
# ---------------------------------------------------------------------------

def _extract_customer_po(lines: list[str], customer_number: str | None) -> str | None:
    """
    Extract a Customer PO without mistaking the salesperson number for it.

    Current supported customer PO forms are:
        - Verbal
        - VO followed by six digits

    If the value immediately following the customer number is neither form,
    it is treated as missing. This is important for Taco Bamba invoices
    where the Customer PO column can be blank and the next value belongs to
    the salesperson field.
    """
    if not customer_number:
        return None

    try:
        customer_index = lines.index(customer_number)
    except ValueError:
        return None

    if customer_index + 1 >= len(lines):
        return None

    candidate = lines[customer_index + 1].strip()

    if re.fullmatch(r"(?i)verbal", candidate):
        return "Verbal"

    if re.fullmatch(r"VO\d{6}", candidate, flags=re.IGNORECASE):
        return candidate.upper()

    return None


def extract_order_details(text: str) -> OrderDetails:
    """Extract invoice-level metadata into a validated OrderDetails model."""

    lines = [line.strip() for line in text.splitlines() if line.strip()]

    customer_name = lines[0] if lines else None

    dates = re.findall(r"\d{2}/\d{2}/\d{2}", text)

    invoice_match = re.search(r"\b\d{6}[A-Z]?\b", text)

    customer_number = None

    for line in lines:
        if re.fullmatch(r"\d{1,5}", line):
            customer_number = line
            break

    customer_purchase_order = _extract_customer_po(lines, customer_number)

    total_match = re.search(
        r"Invoice Total:\s*\$([\d,]+\.\d{2})",
        text,
        flags=re.IGNORECASE,
    )

    return OrderDetails(
        customer_number=customer_number,
        customer_name=customer_name,
        customer_purchase_order=customer_purchase_order,
        delivery_date=dates[1] if len(dates) > 1 else None,
        invoice_number=invoice_match.group(0) if invoice_match else None,
        total_cost=total_match.group(1) if total_match else None,
    )


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

def export_items_csv(
    order_data: dict,
    output_folder: str = "pdf_output",
    po_exception_approved: bool = False,
) -> str:
    """
    Build the required flat CSV.

    The filename includes customer, delivery date, and invoice number so
    multiple invoices from the same customer on the same day cannot
    overwrite one another.
    """

    items_as_dicts = [item.model_dump() for item in order_data["items"]]
    df = pd.DataFrame(items_as_dicts)

    details = order_data["order_details"].model_dump()

    # A legitimate office-created second-delivery exception must still
    # produce a client-compatible Customer PO value in the outbound CSV.
    # The source invoice data is not changed; only the exported value is.
    if po_exception_approved and not details.get("customer_purchase_order"):
        details["customer_purchase_order"] = "Verbal"

    for key in reversed(list(details.keys())):
        df.insert(0, key, details[key])

    df.insert(len(details), "col0", 0)
    df.insert(len(details) + 1, "col1", 0)

    customer_name = details.get("customer_name") or "UNKNOWN"
    delivery_date = details.get("delivery_date") or "UNKNOWN"
    invoice_number = details.get("invoice_number") or "UNKNOWN"

    safe_name = re.sub(r"[^A-Za-z0-9]+", "_", customer_name).strip("_")
    safe_date = re.sub(r"[^A-Za-z0-9-]+", "-", delivery_date)
    safe_invoice = re.sub(r"[^A-Za-z0-9]+", "_", invoice_number).strip("_")

    filename = f"{safe_name}_{safe_date}_{safe_invoice}.csv"

    os.makedirs(output_folder, exist_ok=True)
    output_path = os.path.join(output_folder, filename)

    df.to_csv(
        output_path,
        header=False,
        index=False,
        float_format="%.2f",
    )

    return output_path
