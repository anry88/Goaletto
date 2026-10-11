# Application services

Implemented boundaries:

- analytics.py: closed, versioned journey envelope, activity allowlist and UTC windows;
  validation only, not an event collector.
- persons.py: create_person inside a caller-owned PostgreSQL transaction, stable
  creation key, immutable face reservation, exhaustion error. Server execution and
  concurrent allocation have not been tested while DB checks are disabled by the user.
- world.py/world_portraits.py: prepare a 5x10x20 world with unused age-compatible
  whole faces, 450 fixtures and held jobs; initialize_world writes in one caller-owned
  transaction with immutable receipt. world_cli defaults to offline preview.

Planned gameplay use cases: claim_club, submit_match_command,
process_match_tick, rollover_season, settle_auction, apply_training, post_ledger_entry.

Bot handlers and worker handlers must call the same use cases. Transactions and
authorization belong here. Domain remains pure; no analytics/network/database imports.
