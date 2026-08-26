# MetricLens · Enterprise Metrics Agent

MetricLens is a governed, read-only data agent for enterprise analysts. It turns natural-language
questions into validated ClickHouse or BigQuery SQL and returns the result together with metric,
schema, filter, and execution evidence.

## Start locally

Requirements: Docker Desktop with at least 8 GB of memory. Internet access is needed for the
first Docker image build, but the Olist dataset should already be downloaded locally.

```bash
cp .env.example .env
```

Add **newly rotated** DeepSeek and Alibaba Model Studio credentials to `.env`. Never reuse keys
that have been pasted into chat, committed, or logged.

Download the Kaggle Olist archive from
[Brazilian E-Commerce Public Dataset by Olist](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)
and extract the CSV files into `data/import/`. The local folder should contain these files:

```text
olist_customers_dataset.csv
olist_geolocation_dataset.csv
olist_order_items_dataset.csv
olist_order_payments_dataset.csv
olist_order_reviews_dataset.csv
olist_orders_dataset.csv
olist_products_dataset.csv
olist_sellers_dataset.csv
product_category_name_translation.csv
```

You can verify the local import folder with:

```bash
ls data/import/*.csv
```

```bash
docker compose up --build
```

Open [http://localhost:3000](http://localhost:3000). Demo users:

- Analyst: `analyst` / `analyst-demo`
- Admin: `admin` / `admin-demo`

The first start imports the local Olist CSV files into ClickHouse, so it can take several minutes.
API docs are available at [http://localhost:8000/docs](http://localhost:8000/docs).
ClickHouse is exposed on [http://localhost:18123](http://localhost:18123) by default to avoid
conflicts with an existing local ClickHouse-compatible service. Override it with
`CLICKHOUSE_HTTP_PORT` if needed.

## Example questions

- `2018 年 1 月各州已交付收入是多少？`
- `各商品品类的订单数是多少？`
- `各州平均评论分是多少？`
- `已交付收入采用什么口径？`

Every successful response contains the final SQL and an Evidence Rail showing the metric version,
time range, schema source, SQL policy, query ID, row count, and runtime. Admin users can inspect
and configure warehouse connections from the data-source governance page; the active connection is
stored in the control plane and used by the query gateway at runtime.

## Dataset

The default dataset is the [Brazilian E-Commerce Public Dataset by Olist](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce),
containing about 100,000 anonymized real-world orders. The dataset is licensed separately under
[CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/). Raw data is not committed
to this repository; keep the extracted CSV files under `data/import/` for local development.

## Development checks

```bash
PYTHONPATH=backend pytest backend/tests
cd frontend && npm install && npm test && npm run build
docker compose config
```

Reset local state:

```bash
docker compose down -v
```

Architecture and trust boundaries are documented in [docs/HLD.md](docs/HLD.md).
