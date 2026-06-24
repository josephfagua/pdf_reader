import pymupdf
import pandas as pd
import re
import os
from dotenv import load_dotenv
from models import InvoiceItem, OrderDetails
from pydantic import ValidationError

load_dotenv()

def extract_text(pdf_path: str) -> str:
    doc = pymupdf.open(pdf_path)

    all_text = []

    for page in doc:
        all_text.append(page.get_text())

    return "n".join(all_text)

def refine_data(raw_text: str) -> str:
    text = raw_text

    # ------------------------------------------------------------------
    # Replace SOLD TO / SHIPPED TO section with only buyer name
    # ------------------------------------------------------------------
    def keep_buyer_name(match):
        block = match.group(0)

        buyer_match = re.search(
            r"SHIPPED TO:\s*\n?\s*([^\n\r]+)",
            block,
            flags=re.IGNORECASE
        )

        if buyer_match:
            return f"\n{buyer_match.group(1).strip()}\n"

        return "\n"

    text = re.sub(
        r"SOLD TO:.*?INVOICE",
        keep_buyer_name,
        text,
        flags=re.DOTALL | re.IGNORECASE
    )

    # ------------------------------------------------------------------
    # Remove static invoice column headers but keep actual values
    # ------------------------------------------------------------------

    headers_to_remove = [
        "Customer No.",
        "Customer Purchase Order Salesperson Truck/Route",
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
        "Number"
    ]

    for header in headers_to_remove:
        text = re.sub(
            re.escape(header),
            "",
            text,
            flags=re.IGNORECASE
        )

    # ------------------------------------------------------------------
    # Remove Martin's Distribution address/remittance block
    # ------------------------------------------------------------------
    text = re.sub(
        r"300 FORSYTH HALL DR STE A.*?REMITTANCE ADDRESS:",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE
    )

    # ------------------------------------------------------------------
    # Remove PACA disclaimer/footer (but keep the Invoice Total)
    # ------------------------------------------------------------------
    def keep_total(match):
        block = match.group(0)

        total_match = re.search(
            r"Invoice Total \(\$\)\s*[\d.,]+\s*\$([\d,]+\.\d{2})",
            block,
            flags=re.IGNORECASE
        )

        if total_match:
            return f"\nInvoice Total: ${total_match.group(1)}\n"

        return "\n"

    text = re.sub(
        r"The perishable agricultural commodities listed on this invoice.*?Total WGT\.",
        keep_total,
        text,
        flags=re.DOTALL | re.IGNORECASE
    )

    # ------------------------------------------------------------------
    # Clean up blank lines
    # ------------------------------------------------------------------
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    text = text.strip()

    return text


from models import InvoiceItem, OrderDetails

def parse_items(text: str) -> list[InvoiceItem]:
    """Parse line items out of cleaned invoice text into validated InvoiceItem models."""

    lines = [line.strip() for line in text.splitlines() if line.strip()]

    items = []
    i = 0

    while i < len(lines):

        if (
            i + 9 < len(lines)
            and re.match(r"^\d+$", lines[i])
            and re.match(r"^\d+\.\d+$", lines[i + 1])
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

            except (ValidationError, IndexError):
                pass

        i += 1

    return items


def extract_order_details(text: str) -> OrderDetails:
    """Pull invoice-level metadata from cleaned invoice text into a validated OrderDetails model."""

    lines = [line.strip() for line in text.splitlines() if line.strip()]

    customer_name = lines[0] if lines else None

    dates = re.findall(r"\d{2}/\d{2}/\d{2}", text)

    invoice_match = re.search(r"\b\d{6}[A-Z]?\b", text)

    customer_number = None
    salesperson = None

    for idx, line in enumerate(lines):
        if re.fullmatch(r"\d{1,5}", line):
            customer_number = line
            if idx + 1 < len(lines):
                salesperson = lines[idx + 1]
            break

    total_match = re.search(
        r"Invoice Total:\s*\$([\d,]+\.\d{2})",
        text,
        flags=re.IGNORECASE
    )

    return OrderDetails(
        customer_number=customer_number,
        customer_name=customer_name,
        customer_purchase_order=salesperson,
        delivery_date=dates[1] if len(dates) > 1 else None,
        invoice_number=invoice_match.group(0) if invoice_match else None,
        total_cost=total_match.group(1) if total_match else None,
    )

def export_items_csv(order_data: dict, output_folder: str = "pdf_output") -> str:
    """Build a flat CSV (order details + 0,0 placeholders + items, no header row)."""

    items_as_dicts = [item.model_dump() for item in order_data["items"]]
    df = pd.DataFrame(items_as_dicts)

    details = order_data["order_details"].model_dump()
    for key in reversed(list(details.keys())):
        df.insert(0, key, details[key])

    df.insert(len(details), "col0", 0)
    df.insert(len(details) + 1, "col1", 0)

    customer_name = details.get("customer_name") or "UNKNOWN"
    delivery_date = details.get("delivery_date") or "UNKNOWN"

    safe_name = re.sub(r"[^A-Za-z0-9]+", "_", customer_name).strip("_")
    safe_date = delivery_date.replace("/", "-")

    filename = f"{safe_name}_{safe_date}.csv"

    os.makedirs(output_folder, exist_ok=True)
    output_path = os.path.join(output_folder, filename)

    df.to_csv(output_path, header=False, index=False, float_format="%.2f")

    return output_path



if __name__ == '__main__':
    pdf_path = os.getenv("RAW_INVOICE_PDF")

    raw_text = extract_text(pdf_path)
    with open("pdf_output/test.txt","w", encoding="utf-8") as f:
        f.write(raw_text)

    cleaned_text = refine_data(raw_text)
    with open("output_cleaned.txt","w",encoding="utf-8") as f:
        f.write(cleaned_text)

    order_data = {
        "order_details": extract_order_details(cleaned_text),
        "items": parse_items(cleaned_text)
    }

    output_path = export_items_csv(order_data, "pdf_output/")
    print(f"Saved: {output_path}")
