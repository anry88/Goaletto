# Application services

Planned use cases: claim_club, seed_league_set, submit_match_command, process_match_tick,
rollover_season, settle_auction, apply_training, post_ledger_entry.

These are backlog items, not implemented services. Bot handlers and worker handlers
must call the same use cases. Transactions and authorization are enforced here.
