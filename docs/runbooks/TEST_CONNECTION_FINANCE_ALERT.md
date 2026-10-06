# Deploy and activate the Test Connection Finance alert

1. Merge the reviewed PR through required CI. Build the selected `origin/main`
   once and follow the normal immutable-digest staging acceptance and
   production authorization process. This PR does not deploy production.
2. Rehearse the real predecessor-to-645 migration on a disposable PostgreSQL
   test database, and run the repository integration lane against exact heads.
   Existing purposes stay NULL. Allow a short migration lock budget and retry
   after rollback if the DDL cannot acquire its lock; do not rewrite history.
3. In staging, confirm Service Extensions exposes Purpose, rejects broad Test
   Connection scope, and preserves the existing separate apply approver.
4. Check that the intended Finance team's members have active, Party-bound
   staff accounts and email addresses. Do not substitute every billing user or
   an unrelated group. Configure through the existing service-team owner.
5. Open Automation Center → Workflows → New workflow. Select Financial Access,
   trigger **Test Connection created**, all customers (or the approved selected
   customer scope), and **Test Connections created in the preceding 7 days**
   **greater than** **5**. Choose **Notify Finance of repeated Test Connections**
   and the exact Finance team. Save the draft and activate after review.
6. With an approved staging test customer, create five classified requests
   through the staff form. Applying them is unnecessary for the creation-count
   test. Confirm five skipped workflow decisions and no Finance notice. Create
   the sixth; confirm one successful action and one in-app/email pair per
   intended active Finance member. The workflow must not change service access.
7. Repeat with another customer, an outage-compensation request, and records
   immediately inside/outside the seven-day boundary in the disposable test
   lane. Verify counts are isolated and the event snapshot remains unchanged
   on delayed/repeated dispatch. Do not alter production timestamps for testing.
8. Inspect Automation Center execution history, personal staff inbox rows,
   queued email records, and `notification_deliveries`. A queued/successful
   workflow action is not proof of receipt: inspect provider outcome and have
   the intended test recipients confirm delivery. Check failure/redrive without
   duplicating notices. Record workflow/version, event, review, notification,
   and delivery references in acceptance evidence.
9. After authorized production deployment, configure and publish the approved
   rule. Production synthetic records and test emails require a designated
   test account and agreed Finance recipients. Do not activate real customer
   services merely to test alerts. Never run pytest in production.

Historical reasons resembling “test connection” do not enter the count by
inference. Any historical classification/backfill is a separate bounded,
reviewed migration; deployment sends no retroactive alerts.

Rollback: pause the workflow through Automation Center to stop future alerts.
Keep committed review evidence and source events. Migration downgrade refuses
classified or review data; correct forward rather than erasing Finance history.
