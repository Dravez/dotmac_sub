# Atomic invoice issuance settlement

Status: implemented owner contract

Owner: `financial.account_credit_applications`, composed by
`financial.invoices`

## Invariant

An invoice issued while exact payment-backed account credit is sufficient must
leave the transaction with matching active payment allocations and a zero
balance due. Issuance may not introduce its receivable debit and then use that
same debit to decide that the previously sufficient credit is unavailable.

## Command flow

Before changing a draft to issued, the invoice owner locks the customer account
and asks the account-credit owner for a typed funding reservation. The
reservation fingerprints the draft identity, currency, amount, selected native
payments, and amount reserved from each payment. After issuance, the
account-credit owner rechecks the locked invoice and every selected settlement,
then stages the allocations, paired ledger evidence, customer-subledger posting,
invoice finalization, audit evidence, and durable payment observation in the
same transaction.

If full funding is required and the reservation is short, issuance leaves the
credit untouched. If any reserved source changes before application, the owner
raises a domain error and the transaction rolls back. Allocation idempotency is
derived from the payment and invoice identities.

## Drift and repair

The existing account-credit invariant scan continues to identify eligible open
invoices with unused credit. Historical invoices that already crossed the
faulty boundary require a separately reviewed reconciliation; the forward path
does not infer or rewrite historical payment provenance.

## Validation

`tests/test_account_credit_deposits.py` reproduces a prepaid account whose exact
funding previously disappeared from the allocator after the invoice's own debit
posted. It requires the issued invoice to become paid with one exact allocation
and zero remaining reusable credit.

