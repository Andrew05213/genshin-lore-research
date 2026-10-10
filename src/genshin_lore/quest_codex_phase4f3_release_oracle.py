"""Builder-independent exact business-tuple oracle for Phase 4F3."""
from __future__ import annotations

# Schema 13 stores canary and full runs in the same family-specific tables.  The
# tuple construction is deliberately shared by both release verifiers, while
# neither verifier imports or invokes either builder.
from .quest_codex_phase4f2_release_oracle import exact_occurrence_tables

__all__ = ["exact_occurrence_tables"]
