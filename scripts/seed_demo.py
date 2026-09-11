from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.main import build_services  # noqa: E402


def build_assets_if_missing() -> None:
    required = ROOT / "demo_data" / "shopflow" / "raw" / "records.json"
    if not required.exists():
        subprocess.run([sys.executable, str(ROOT / "scripts" / "build_demo_assets.py")], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the complete fictional ShopFlow demo")
    parser.add_argument(
        "--reset", action="store_true", help="Clear application-owned demo data first"
    )
    args = parser.parse_args()
    build_assets_if_missing()
    settings = get_settings()
    services = build_services(settings)
    try:
        readiness = {
            "MongoDB": services.mongo.health(),
            "Qdrant": services.qdrant.health(),
            "Neo4j": services.graph.health(),
            "Mem0": services.memory.health(),
        }
        unavailable = [name for name, ready in readiness.items() if not ready]
        if unavailable:
            raise RuntimeError(f"Unavailable dependencies: {', '.join(unavailable)}")
        records_path = settings.DEMO_ROOT / "raw" / "records.json"
        records = json.loads(records_path.read_text(encoding="utf-8"))
        if args.reset:
            for collection in (
                "customers",
                "orders",
                "payments",
                "tickets",
                "investigation_runs",
                "reviews",
                "ingested_sources",
            ):
                services.mongo.db[collection].delete_many({})
        services.mongo.ensure_indexes()
        seeded = {
            "customers": services.mongo.upsert_many(
                "customers", "customer_id", records["customers"]
            ),
            "orders": services.mongo.upsert_many("orders", "order_id", records["orders"]),
            "payments": services.mongo.upsert_many("payments", "payment_id", records["payments"]),
            "tickets": services.mongo.upsert_many("tickets", "ticket_id", records["tickets"]),
        }
        ingestion = services.ingestion.run(reset=args.reset)
        memories = [
            (
                "CUST-001 previously experienced a payment callback synchronization failure. "
                "Order reconciliation restored the order.",
                "CUST-001",
                "PAYMENT",
                "TKT-0901",
                "PaymentService",
            ),
            (
                "For unknown-payment reports, ShopFlow support requires immediate human escalation.",
                "organization",
                "SECURITY",
                "TKT-0905",
                "PaymentService",
            ),
            (
                "The support lead prefers rollback or reconciliation before suggesting infrastructure scaling.",
                "organization",
                "SUPPORT_PREFERENCE",
                "ORG-PREF-001",
                None,
            ),
            (
                "Approved payment callback resolutions favor verified reconciliation after capture.",
                "category:PAYMENT",
                "PAYMENT",
                "TKT-0901",
                "PaymentService",
            ),
        ]
        added = sum(
            services.memory.seed_approved(text, scope, category, ticket, service)
            for text, scope, category, ticket, service in memories
        )
        print(
            "Seed complete: "
            + ", ".join(f"{name}={count}" for name, count in seeded.items())
            + f"; evidence={ingestion['indexed']}; new_memories={added}."
        )
    finally:
        services.mongo.close()
        services.graph.close()


if __name__ == "__main__":
    main()
