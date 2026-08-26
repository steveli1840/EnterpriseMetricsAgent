CREATE DATABASE IF NOT EXISTS raw_olist;
-- statement
CREATE DATABASE IF NOT EXISTS analytics;
-- statement
CREATE TABLE IF NOT EXISTS raw_olist.customers
(
  customer_id String, customer_unique_id String, customer_zip_code_prefix UInt32,
  customer_city String, customer_state LowCardinality(String)
) ENGINE = MergeTree ORDER BY customer_id;
-- statement
CREATE TABLE IF NOT EXISTS raw_olist.orders
(
  order_id String, customer_id String, order_status LowCardinality(String),
  order_purchase_timestamp DateTime, order_approved_at Nullable(DateTime),
  order_delivered_carrier_date Nullable(DateTime), order_delivered_customer_date Nullable(DateTime),
  order_estimated_delivery_date Nullable(DateTime)
) ENGINE = MergeTree ORDER BY (order_purchase_timestamp, order_id);
-- statement
CREATE TABLE IF NOT EXISTS raw_olist.order_items
(
  order_id String, order_item_id UInt16, product_id String, seller_id String,
  shipping_limit_date DateTime, price Decimal(14,2), freight_value Decimal(14,2)
) ENGINE = MergeTree ORDER BY (order_id, order_item_id);
-- statement
CREATE TABLE IF NOT EXISTS raw_olist.products
(
  product_id String, product_category_name Nullable(String), product_name_lenght Nullable(UInt16),
  product_description_lenght Nullable(UInt32), product_photos_qty Nullable(UInt16),
  product_weight_g Nullable(UInt32), product_length_cm Nullable(UInt16),
  product_height_cm Nullable(UInt16), product_width_cm Nullable(UInt16)
) ENGINE = MergeTree ORDER BY product_id;
-- statement
CREATE TABLE IF NOT EXISTS raw_olist.sellers
(
  seller_id String, seller_zip_code_prefix UInt32, seller_city String,
  seller_state LowCardinality(String)
) ENGINE = MergeTree ORDER BY seller_id;
-- statement
CREATE TABLE IF NOT EXISTS raw_olist.payments
(
  order_id String, payment_sequential UInt16, payment_type LowCardinality(String),
  payment_installments UInt16, payment_value Decimal(14,2)
) ENGINE = MergeTree ORDER BY (order_id, payment_sequential);
-- statement
CREATE TABLE IF NOT EXISTS raw_olist.reviews
(
  review_id String, order_id String, review_score UInt8, review_comment_title Nullable(String),
  review_comment_message Nullable(String), review_creation_date DateTime,
  review_answer_timestamp DateTime
) ENGINE = MergeTree ORDER BY (order_id, review_id);
-- statement
CREATE TABLE IF NOT EXISTS raw_olist.geolocation
(
  geolocation_zip_code_prefix UInt32, geolocation_lat Float64, geolocation_lng Float64,
  geolocation_city String, geolocation_state LowCardinality(String)
) ENGINE = MergeTree ORDER BY geolocation_zip_code_prefix;
-- statement
CREATE TABLE IF NOT EXISTS raw_olist.category_translation
(
  product_category_name String, product_category_name_english String
) ENGINE = MergeTree ORDER BY product_category_name;
-- statement
CREATE OR REPLACE VIEW analytics.fct_order_items AS
SELECT
  oi.order_id AS order_id,
  oi.order_item_id AS order_item_id,
  oi.product_id AS product_id,
  oi.seller_id AS seller_id,
  oi.price AS price,
  oi.freight_value AS freight_value,
  o.order_status AS order_status,
  o.order_purchase_timestamp AS order_purchase_at,
  toStartOfMonth(o.order_purchase_timestamp) AS month,
  c.customer_unique_id AS customer_unique_id,
  c.customer_state AS customer_state,
  s.seller_state AS seller_state,
  coalesce(t.product_category_name_english, p.product_category_name, 'unknown') AS product_category
FROM raw_olist.order_items AS oi
INNER JOIN raw_olist.orders AS o USING (order_id)
INNER JOIN raw_olist.customers AS c USING (customer_id)
LEFT JOIN raw_olist.products AS p USING (product_id)
LEFT JOIN raw_olist.category_translation AS t USING (product_category_name)
LEFT JOIN raw_olist.sellers AS s USING (seller_id);
-- statement
CREATE OR REPLACE VIEW analytics.fct_reviews AS
SELECT
  r.review_id AS review_id,
  r.order_id AS order_id,
  r.review_score AS review_score,
  r.review_comment_title AS review_comment_title,
  r.review_comment_message AS review_comment_message,
  r.review_creation_date AS review_creation_date,
  r.review_answer_timestamp AS review_answer_timestamp,
  o.order_purchase_timestamp AS order_purchase_at,
  r.review_creation_date AS review_created_at,
  c.customer_state AS customer_state
FROM raw_olist.reviews AS r
INNER JOIN raw_olist.orders AS o USING (order_id)
INNER JOIN raw_olist.customers AS c USING (customer_id);
