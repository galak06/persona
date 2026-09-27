"""Cheap classifier decisions (Jev via OpenRouter) in front of the LLM drafter.

Slice 1 covers the IG/FB post-relevance gate. Modules, one job each:

* ``jev_types``     -- question/answer dataclasses and response parsing
* ``jev_client``    -- the HTTP call (never raises, returns None on failure)
* ``modes``         -- the off/shadow/enforce gate-mode vocabulary
* ``post_gate``     -- the three post questions and the enforce thresholds
* ``decisions_db``  -- the ``jev_decisions`` log table
* ``gate_budget``   -- per-run call cap, wall-clock budget, circuit breaker
* ``engager_gate``  -- the pipeline collaborator the engagers inject
* ``gate_factory``  -- builds that collaborator from the brand row
"""
