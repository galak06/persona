"""Recipe-publisher pipeline phases.

Each phase is a thin vertical slice over the recipe DB with structured logging
(`lib.observability.logger`) and an end-of-phase validation gate
(`pipeline.checkpoint`). Phase 1 is `seasonal_selection`.
"""
