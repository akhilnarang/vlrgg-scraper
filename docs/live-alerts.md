# Broadcast tracker alerts

The live push cron and the video context endpoint log at ERROR level when the broadcast
tracker is running for a live match but its data is not reaching users. Those records are the
app's only automated signal for a stalled tracker: `sentry_sdk`'s default `LoggingIntegration`
turns each ERROR record into a Sentry event, and `RichHandler` writes it to stdout, which
systemd captures in the journal.

## Emitted alerts

| Alert | Source | Fires when |
|-------|--------|------------|
| `video tracker is running but has not covered live match {id} for {n} minutes; VLR is pushing it` | `app/cron/live_push.py::_report_video_gap` | The tracker resolved a VLR match but its score has not been delivered for 25 minutes while VLR keeps pushing the match. |
| `video tracker has asked for context for {n} minutes but its codes {codes} fit no VLR match` | `app/cron/live_push.py::_report_video_unresolved` | The tracker has asked `/api/v1/video/context?codes=...` every minute for 30 minutes and the scraper resolved none of the codes. |

Both are rate-limited to one record per hour while the condition lasts, through the NX keys
`valesports:tracker:fallback_alerted:{match_id}` and `valesports:tracker:unresolved_alerted`.
The unresolved alert only fires while `valesports:tracker:codes` (a 180-second liveness key set
on every codes request) exists: a tracker that stopped asking is systemd's to notice, not this
alert's. On 2026-10-14..16 the tracker asked for 98–284 minutes straight with zero resolutions
and no alert existed for it; the second row above closes that gap.

## Where the signals land

- **Sentry**: the production DSN in `~/vlrgg-scraper/.env` on server02 points at Sentry SaaS
  org `o1353375`, project id `6635298`. The app initialises the SDK in
  `app/core/observability.py::init_sentry`; `app/main.py` calls it at import. The systemd unit
  does not override `SENTRY_DSN`, so removing it from `.env` silences Sentry (the journal keeps
  the records).
- **Journal**: `journalctl --user -u vlrgg-scraper | grep "video tracker"` on server02.
- No email, Slack, PagerDuty, or SMS destination is configured in this repository. Those live
  in Sentry issue alert rules and org integrations outside the repo.

## Verified 2026-10-16

A synthetic run on server02 (release `618352f`, the deployment at the time) drove the real
`_report_video_gap` path and a plain ERROR record through the production `init_sentry()`
settings. The Sentry ingest endpoint accepted both envelopes with **HTTP 200**. That proves the
log → SDK → ingest path works from production. It does **not** prove that an issue alert rule
exists, or that any human receives a notification for a new issue; no Sentry auth token existed
on server02 or the development machine, so alert rules could not be read through the API.

## Check for alert rules (Sentry UI)

1. Open <https://o1353375.sentry.io> and select the project with id `6635298` (the DSN host
   `o1353375.ingest.sentry.io` identifies the org; Settings → Projects shows the id).
2. Open **Alerts** and read every rule: does any trigger on *new issues* (Sentry's "A new issue
   is created") and route to a destination a human reads (email, Slack, PagerDuty, or an
   integration)?
3. Also check **Settings → Notifications** for the users/team that should receive new-issue
   mail; that per-membership setting cannot be inspected from the repo.
4. Search the project's issues for the synthetic events of 2026-10-16 ~14:19–14:20 UTC
   (messages `...has not covered live match 999999...` and `vlrgg alert-path verification`, tag
   `verification=ops-alert-path-2026-10-16`). If they are absent despite the HTTP 200, an
   inbound filter, rate limit, or quota is dropping events and the alert cannot work until
   that is fixed.

## Create the rule if none exists

1. In the project, open **Alerts → Create Alert → Issue Alert**.
2. **When**: `A new issue is created` (its first occurrence; not "an issue is seen more than
   N times").
3. Optional condition: `The event's level equals error`. To scope the rule to this alert,
   filter on `The event's message contains "video tracker"`.
4. **Then**: `Send a notification to` your destination (email recipient, Slack channel,
   PagerDuty service). Pick a destination that wakes a human; email alone is weakest.
5. Name and save the rule, then prove it with a synthetic event from server02:

   ```sh
   cd ~/vlrgg-scraper && .venv/bin/python - <<'PY'
   import logging, sys, time
   sys.path.insert(0, "/home/ubuntu/vlrgg-scraper")
   from app.core import observability
   from sentry_sdk import get_client

   observability.init_sentry()
   logging.getLogger("app.cron.live_push").error(
       "vlrgg alert-path verification (synthetic; ignore) - %s", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
   )
   get_client().flush(timeout=20)
   PY
   ```

   The new issue should appear in the project and the destination should receive the
   notification within a minute or two.
