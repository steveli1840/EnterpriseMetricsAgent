# Olist business glossary

## Delivered revenue

Delivered revenue is the sum of item price and freight value for orders whose status is
`delivered`. It is not profit and must not be described as net income.

## Customer identity

`customer_id` identifies a customer record attached to one order. Use `customer_unique_id`
when measuring repeat customers across orders.

## Product category

The source category is Portuguese. Analytics views expose the supplied English translation
when available and use `unknown` otherwise.

## Metric aliases

- metric.delivered_revenue: 已交付收入, 收入, 销售收入, 销售额, delivered revenue
- metric.order_count: 订单数, 订单量, 订单最多, distinct order count, order count, orders
- metric.gmv: GMV, 成交总额, 商品金额
- metric.average_order_value: 客单价, 平均订单金额, AOV, average order value
- metric.freight_amount: 运费, 运费金额, freight amount
- metric.average_delivery_days: 平均交付天数, 交付时长, 交付天数, delivery days, 配送
- metric.canceled_orders: 取消订单数, 取消订单, 取消量, canceled order count
- metric.average_review_score: 平均评论分, 评论分, 满意度评分, review score
- metric.on_time_orders: 准时交付订单数, 准时交付, on time orders
- metric.repeat_customers: 复购客户数, 复购, repeat customers

## Dimension aliases

- dimension.customer_state: 州, 客户州, customer state, state
- dimension.product_category: 品类, 商品品类, 产品类别, 商品类别, product category, product categories, category, categories
- dimension.seller_state: 卖家州, seller state, 州卖家
- dimension.month: 每月, 按月, 哪个月, monthly, month
