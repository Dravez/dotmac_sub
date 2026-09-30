# Reviewed payment-allocation reversal

Use this workflow when a succeeded customer payment was allocated to an
invoice that has since been voided, and the payment itself must remain valid.
This workflow reverses only the allocation. It does not refund, cancel, or
reverse the payment.

## Preview

Call `POST /api/billing/payment-allocation-reversals/preview` with:

```json
{"allocation_id": "<allocation-uuid>"}
```

Confirm that the preview names the expected void invoice, amount, payment,
and both original ledger entries. Record the returned fingerprint.

## Confirm

Call `POST /api/billing/payment-allocation-reversals/confirm` with the same
allocation, the exact fingerprint, a stable idempotency key, and the Finance
review reason:

```json
{
  "allocation_id": "<allocation-uuid>",
  "preview_fingerprint": "<64-lowercase-hex>",
  "idempotency_key": "finance-ticket-allocation-reversal-v1",
  "reason": "Finance-approved reversal of allocation to void invoice"
}
```

The command appends linked ledger reversals, marks only the allocation
inactive, and records audit evidence. A repeated request with the same key
replays the original result. Never update allocation or ledger rows directly.

## Verify

Confirm that the payment remains succeeded and active, the original allocation
remains present but inactive, the two reversal ledger links exist, and the
payment's available amount increased by exactly the allocation amount. Then
allocate the released payment through the normal preview/confirm flow.
