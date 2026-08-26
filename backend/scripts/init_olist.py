import hashlib
import os
from pathlib import Path

import clickhouse_connect
import kagglehub


FILES = {
    "customers": "olist_customers_dataset.csv",
    "orders": "olist_orders_dataset.csv",
    "order_items": "olist_order_items_dataset.csv",
    "products": "olist_products_dataset.csv",
    "sellers": "olist_sellers_dataset.csv",
    "payments": "olist_order_payments_dataset.csv",
    "reviews": "olist_order_reviews_dataset.csv",
    "geolocation": "olist_geolocation_dataset.csv",
    "category_translation": "product_category_name_translation.csv",
}


def locate_dataset() -> Path:
    imported = Path("/data/import")
    if all((imported / filename).exists() for filename in FILES.values()):
        return imported
    return Path(
        kagglehub.dataset_download(
            os.getenv("KAGGLE_DATASET", "olistbr/brazilian-ecommerce"),
            output_dir="/data/cache/olist",
        )
    )


def manifest(root: Path) -> dict[str, str]:
    return {
        name: hashlib.sha256((root / filename).read_bytes()).hexdigest()
        for name, filename in FILES.items()
    }


def main():
    root = locate_dataset()
    missing = [filename for filename in FILES.values() if not (root / filename).exists()]
    if missing:
        raise RuntimeError(f"Olist dataset is incomplete: {', '.join(missing)}")

    client = clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "clickhouse"),
        username="default",
        password=os.getenv("CLICKHOUSE_ADMIN_PASSWORD", "admin-demo"),
    )
    sql_root = Path(__file__).parents[2] / "data" / "clickhouse"
    for sql_path in sorted(sql_root.glob("*.sql")):
        for statement in sql_path.read_text().split("-- statement"):
            if statement.strip():
                client.command(statement.strip())

    for table, filename in FILES.items():
        client.command(f"TRUNCATE TABLE raw_olist.{table}")
        with (root / filename).open("rb") as csv_file:
            client.raw_insert(
                f"raw_olist.{table}",
                insert_block=csv_file,
                fmt="CSVWithNames",
                settings={
                    "input_format_allow_errors_num": 50,
                    "input_format_csv_empty_as_default": 1,
                },
            )
    print({"dataset": "olist", "files": manifest(root), "status": "loaded"})


if __name__ == "__main__":
    main()
