CREATE OR REPLACE VIEW analytics.fct_orders AS
SELECT
  o.order_id,
  o.order_status,
  o.order_purchase_timestamp AS order_purchase_at,
  o.order_delivered_customer_date AS order_delivered_customer_at,
  o.order_estimated_delivery_date AS order_estimated_delivery_at,
  toStartOfMonth(o.order_purchase_timestamp) AS month,
  c.customer_unique_id,
  c.customer_state
FROM raw_olist.orders o
INNER JOIN raw_olist.customers c USING (customer_id);
-- statement
CREATE OR REPLACE VIEW analytics.customer_order_summary AS
SELECT
  c.customer_unique_id,
  any(c.customer_state) AS customer_state,
  min(o.order_purchase_timestamp) AS first_order_at,
  toStartOfMonth(min(o.order_purchase_timestamp)) AS month,
  uniqExact(o.order_id) AS order_count
FROM raw_olist.orders o
INNER JOIN raw_olist.customers c USING (customer_id)
GROUP BY c.customer_unique_id;

