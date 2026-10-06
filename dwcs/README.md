# Dana White's Contender Series (DWCS) research lane

DWCS is intentionally isolated from UFC official/live performance accounting.

## Rules
- Promotion key: `DWCS`
- Event type: `contender_series`
- Separate event, fight, odds, method-signal, and performance ledgers.
- UFC method records are never incremented by DWCS results.
- Existing UFC methods may be evaluated here only as `transfer_test` / shadow signals.
- New DWCS-native methods remain `shadow_candidate` until validated and manually promoted.
- Preserve point-in-time inputs and odds; do not backfill a price as though it were known pre-fight.
- Store contract outcomes separately from fight outcomes.

## Initial research focus
1. Age / age gap
2. Reach / height
3. Pro experience and undefeated status
4. Regional-promotion and opponent-quality context
5. Finish rate and first-round finish rate
6. Striking / wrestling / takedown-defense evidence where available
7. Layoff and recent form
8. Market price and line movement
9. Existing UFC method transfer tests
10. DWCS-specific method discovery

The first seeded prospective card is Season 10, Week 9 on 2026-10-06.
