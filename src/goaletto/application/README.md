# Application services

Implemented boundaries:

- analytics.py: closed, versioned journey envelope, activity allowlist and UTC windows;
  validation only, not an event collector.
- persons.py: create_person inside a caller-owned PostgreSQL transaction, stable
  creation key, immutable face reservation, exhaustion error. Server execution and
  concurrent allocation have not been tested while DB checks are disabled by the user.

Planned gameplay use cases: claim_club, seed_league_set, submit_match_command,
process_match_tick, rollover_season, settle_auction, apply_training, post_ledger_entry.

Bot handlers and worker handlers must call the same use cases. Transactions and
authorization belong here. Domain remains pure; no analytics/network/database imports.
