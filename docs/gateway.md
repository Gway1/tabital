# Merchant payment gateway (hosted checkout)

For merchants with their own website. Customers pay with a Tabital plan, so the customer and the
merchant must both be registered, approved Tabital users. Every purchase runs through the same
checks as `/customer/purchase` (`place_order` in `resources/customer_purchase.py`): KYC, fraud,
employment verification, underwriting, limit, server-side pricing, Paystack down payment, admin
approval, delivery and settlement.

## Setup (merchant)
1. Set the storefront URL: `PUT /merchant/gateway/settings {"storefront_url": "https://shop.example.com"}`.
   Redirect URLs must be on this origin.
2. Create a secret key: `POST /merchant/gateway/keys` (shown once; stored as a SHA-256 hash).
3. Optional webhook: `PUT /merchant/gateway/settings {"webhook_url": "https://..."}` returns a signing secret once.

## Flow
1. Merchant server: `POST /gateway/sessions` with `Authorization: Bearer tb_sk_...`
   `{reference, amount, items[], description, success_url, cancel_url}` -> `{checkout_url, ...}`.
   Repeating a `reference` returns the same open session. Sessions expire after `GATEWAY_SESSION_MINUTES`.
2. Send the customer to `checkout_url` (frontend route `/customer/checkout/<token>`; signed-out customers are sent to log in first and come back).
3. Frontend: `GET /customer/gateway/sessions/<token>` then
   `POST /customer/gateway/sessions/<token>/purchase {accept_terms, number_of_installments, delivery_address}`;
   the response carries `authorization_url` for the Paystack down payment.
4. Redirect to `success_url` / `cancel_url`; rely on the webhook or `GET /gateway/orders/<reference>`, not the redirect.

## POS (till) checkout
Same endpoint with `medium: "pos"` (default `"ecommerce"`). A POS has no customer browser, so Tabital reaches the customer instead.
1. Till server: `POST /gateway/sessions {medium: "pos", reference, amount, customer_phone, channels?, terminal_id?, items?}`.
   `success_url` / `cancel_url` are optional. `channels` is any of `sms`, `email`, `in_app` (default `sms` + `in_app`).
2. Tabital looks the customer up by phone (any of `024...`, `+23324...`). Not registered: `404 customer_not_found`; not approved or KYC-verified: `409 customer_not_eligible`. No session or message is created in either case. Lookups are rate limited per merchant (60 an hour).
3. The checkout link goes out through the message outbox (one message per channel per session). The response's `notified` shows `sent`, `queued`, `skipped` (e.g. no email on file) per channel.
4. The customer opens the link, logs in, picks a plan and pays the down payment in the hosted checkout. Only that customer can open or pay the session (403 for anyone else). POS sessions last `GATEWAY_POS_SESSION_MINUTES` (15).
5. The `order.paid` webhook tells the till to accept the sale. `GET /gateway/sessions/<id>` also works for tills that can't receive webhooks. Unpaid sessions become `expired` and send `session.expired`.
- `POST /gateway/sessions/<id>/cancel` cancels an unpaid session; `POST /gateway/sessions/<id>/resend {channels}` sends the link again (3 messages per channel at most).
- `order.paid` means the down payment was collected. Tabital admin approval still follows (`order.approved`); a merchant who wants to wait for approval before releasing goods should act on `order.approved` instead.

## Webhooks
Events: `order.created`, `order.paid` (down payment collected; a POS accepts the sale on this), `order.pending` (same moment, awaiting admin approval), `order.approved`, `order.rejected`, `order.delivered`, and `session.expired` (a POS checkout ran out of time unpaid). Order events carry `medium` and, for POS, `terminal_id` and a masked `customer_phone`.
Header `X-Tabital-Signature: t=<unix>,v1=<hex HMAC-SHA256(secret, "<t>.<raw body>")>`; reject old timestamps.
Queued with the change itself and sent by `flask deliver-webhooks` (also run by `flask run-daily`);
schedule it every minute. Failures retry with backoff, 8 attempts. Respond 2xx to acknowledge.

## Notes
- Amount limits: system settings `gateway_min_amount` (1) and `gateway_max_amount` (50000).
- Webhook URLs must be https and public (re-checked on every send). `GATEWAY_ALLOW_INSECURE_URLS=1` is for local development only.
- Admins (management level) switch a merchant's gateway off/on with `PUT /admin/merchants/<id>/gateway {"enabled": false, "reason": "..."}`; the change is logged in `setting_changes`. Sessions can't be created or paid while it is off.
