# Daily servicing, reminders, autopay and disputes

## The daily job

```
flask --app "app:create_app" run-daily
```

Run it once a day. On Render, add a **Cron Job** with the same environment variables as the web service. Schedule: `0 6 * * *`. That's 06:00 UTC, which is 06:00 in Accra.

Running it twice on the same day doesn't charge or send anything twice. Admins can also start a run from **Admin → Disputes → "Run daily servicing now"** (`POST /admin/servicing/run`).

Each run does five things:

1. **Late fees.** The first fee (10%) is charged the day after the due date. A second fee (10% of the still-overdue instalment) is charged at 31+ days past due. Both count toward the plan cap of 25% of the order.
2. **Delinquency.** Sets each plan's `days_past_due` and its `dpd_bucket` (current, 1–30, 31–60, 61–90, 90+). Plans more than 90 days past due become `defaulted` (charge-off). Collections continue after that.
3. **Collections stage (§8.5).** In order: reminders, call centre, employer contact, then bureau reporting. Legal recovery applies only to orders of GHS 10,000 or more.
4. **Reminders.** SMS and in-app messages at −3, −1, 0, +1, +6 and +31 days from the due date.
5. **Autopay.** Charges saved cards for customers who switched autopay on. It tries on the due date, then retries after 1 day and after 3 days.

Plans paused by an open dispute are skipped by all of the above.

## Settings (system_settings)

| Key | Default | Meaning |
|---|---|---|
| `late_fee_percentage` | 10 | First late fee, % of the instalment |
| `second_late_fee_percentage` | 10 | Second late fee, % of the instalment |
| `second_late_fee_after_days` | 31 | Days past due before the second fee |
| `late_fee_cap_percentage` | 25 | Maximum total late fees on a plan, % of the order's total payable |
| `charge_off_after_days` | 90 | Days past due before a plan is marked defaulted |
| `high_ticket_threshold` | 10000 | Minimum order size (GHS) for legal recovery |
| `reminder_schedule` | see `services/reminders.py` | JSON list of `{"offset": days, "template": name}` |
| `reminder_channels` | `["sms", "in_app"]` | Where reminders are sent |
| `autopay_retry_days` | `[0, 1, 3]` | Days after the due date on which autopay tries |
| `dispute_resolution_days` | 21 | Target days to resolve a dispute |

## SMS provider: mNotify

`SMS_PROVIDER=log` (the default) only writes messages to the server log. To send real SMS through **mNotify** (founder choice), set these environment variables:

| Variable | Value |
|---|---|
| `SMS_PROVIDER` | `mnotify` |
| `MNOTIFY_API_KEY` | API key from the mNotify dashboard. Never commit it. |
| `MNOTIFY_SENDER_ID` | Sender ID of 11 characters or fewer (default `TabitalPay`). It must be **registered and approved** in mNotify first. Unapproved sender IDs are rejected. |

Messages use mNotify's Quick SMS API (`POST https://api.mnotify.com/api/sms/quick`). The campaign `_id` is stored as `provider_message_id` in `message_outbox`. A failed send is recorded with its error and retried up to 3 times. Check sent and failed messages at `GET /admin/messages`.

### Alternative: Hubtel

Set `SMS_PROVIDER=hubtel` with `HUBTEL_CLIENT_ID`, `HUBTEL_CLIENT_SECRET` and `HUBTEL_SENDER_ID` (11 characters or fewer, approved in Hubtel; default `TabitalPay`). `HUBTEL_BASE_URL` defaults to `https://smsc.hubtel.com/v1`. Messages go to `POST {base}/messages/send` with Basic auth, and Hubtel's `messageId` is stored as `provider_message_id`. The same provider sends the `sms` and `whatsapp` reminder channels; there is no separate Hubtel WhatsApp integration.

Any other provider can be added by implementing `send(to, body)` in `app/services/sms.py` and registering it in `PROVIDERS`.

## Autopay

- A reusable card is saved automatically after a successful Paystack card payment. Only Paystack's authorization code is stored, never card details.
- Autopay stays **off** until the customer switches it on in **Make Payment → Saved cards & autopay**.
- Removing a card deactivates it at Paystack too.

## Disputes (buyer protection)

A customer reports a problem from the plan detail screen, which pauses the plan. An admin then resolves it in **Admin → Disputes** in one of two ways:

- **Merchant won:** payments resume, and unpaid due dates move forward by the number of days the plan was paused.
- **Customer won:** the remaining balance is written off in the ledger (`balance_written_off`) and the plan is cancelled. The amount the customer already paid is shown as `refund_amount`. Refund it by hand for now; merchant clawback comes with settlements in Phase 5.
