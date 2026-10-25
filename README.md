# Tabital API

Flask API for Tabital Pay, a buy-now-pay-later service in Ghana. Customers buy from merchants and pay in instalments; Tabital underwrites the customer, collects payments through Paystack, and settles merchants.

The Angular frontend lives in the sibling folder `tabital_frontend`.

## Roles
- **Customer**: signs up, verifies identity (Smile ID), gets a credit limit, buys on a plan, repays.
- **Merchant**: signs up, passes business verification, sells through products, payment links/QR codes, or the **checkout API** for their own website, and receives settlements.
- **Admin** (`management` or `operations`): approves accounts and orders, reviews fraud, runs settlements. Writes under `/admin/*` are management-only unless listed in `app/services/access.py`.

## Getting started
```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then fill in the values
flask --app app:create_app db upgrade
python run.py                 # http://localhost:5000
```
Secrets come only from the environment. `SECRET_KEY` is required unless `FLASK_DEBUG=1`. Every setting is documented in `.env.example`.

Swagger UI is at `/swagger-ui` when `ENABLE_API_DOCS=1`; the spec is at `/swagger.json` and is generated from the registered routes.

## Tests
```bash
python -m pytest tests -q
```
Tests use a temporary SQLite database. The full suite takes a few minutes.

## Scheduled jobs
| Command | When | What |
| --- | --- | --- |
| `flask run-daily` | once a day | Late fees, days-past-due, reminders, autopay, settlement batches, and merchant webhook delivery |
| `flask deliver-webhooks` | every minute | Sends queued checkout API webhooks and retries failures |

## Checkout API (payment gateway)
Merchants with their own website can take Tabital payments without uploading inventory. Their server creates a checkout, sends the customer to the Tabital-hosted checkout page, and gets signed webhooks as the order moves. Customers and merchants must both be registered, approved Tabital users, and every purchase goes through the normal KYC, underwriting, pricing, down-payment and approval flow.

- Merchants set it up under **Sell → Checkout API** in their dashboard: storefront URL, API keys, webhook.
- Admins can switch it off or on per merchant from the admin merchant overview (management only, with a reason).
- **POS tills** use the same API with `medium: "pos"` and the customer's phone number: Tabital messages the customer the checkout link and sends the till an `order.paid` webhook when the down payment is collected.
- Full flow, endpoints, webhook signing and limits: [`docs/gateway.md`](docs/gateway.md).

Quick example:
```bash
curl -X POST http://localhost:5000/gateway/sessions \
  -H "Authorization: Bearer tb_sk_YOUR_SECRET_KEY" -H "Content-Type: application/json" \
  -d '{"reference":"ORDER-1001","amount":1200,
       "success_url":"https://shop.example.com/thanks","cancel_url":"https://shop.example.com/cart"}'
# redirect the customer to the returned checkout_url
```
Secret keys belong on the merchant's server only. For local development set `GATEWAY_ALLOW_INSECURE_URLS=1` to allow `http://` URLs.

## Project layout
```
app/
  models/       SQLAlchemy models
  resources/    Flask-RESTful endpoints (checked for role inline), registered in routes.py
  services/     business logic: plan_engine, risk, ledger, settlements, paystack, gateway, webhooks...
  docs.py       Swagger spec builder and @api_doc
migrations/     Alembic (check `flask db heads`; there should be one)
docs/           per-feature notes
tests/
```

## Conventions
- The server owns all pricing; clients never send amounts for products, only choices (plan, quantity).
- The ledger is append-only. Services don't commit the ledger; the resource commits.
- Admin responses are PII-masked; revealing a field is logged.
- A new route is documented automatically in Swagger; add `@api_doc` above `@auth_required` for detail. `tests/test_api_docs.py` checks the route count.

## More docs
[`docs/paystack.md`](docs/paystack.md) · [`docs/settlements.md`](docs/settlements.md) · [`docs/servicing.md`](docs/servicing.md) · [`docs/identity_fraud.md`](docs/identity_fraud.md) · [`docs/deferment.md`](docs/deferment.md) · [`docs/deploy_checklist.md`](docs/deploy_checklist.md) · [`docs/remaining_items.md`](docs/remaining_items.md)
