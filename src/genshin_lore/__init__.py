"""Evidence-first Genshin lore database (Phase 1 vertical slice)."""

from .db import Database
from .discovery import TalkDiscoveryIndex, load_talk_discovery_index_cache
from .pipeline import ingest_slice
from .quest_corpus_evidence import (
    PreflightCorpusCandidate,
    PreflightCorpusQuest,
    PreflightCorpusReference,
    PreflightQuestCorpusResult,
    PreflightTextMapShard,
    ingest_quest_corpus_evidence_bundle,
    preflight_quest_corpus_evidence_bundle,
)
from .quest_corpus_manifest import (
    CandidateTalkEntry,
    CorpusInvariants,
    QuestBundleReference,
    QuestCandidateBundleAudit,
    QuestCandidateBundleManifest,
    QuestCorpusManifest,
    QuestFileEntry,
    QuestTalkReference,
    SourceSnapshot,
    TextMapAssetManifest,
    TextMapEntry,
    TextMapManifestReference,
    VerifiedQuestCorpusManifestSet,
    load_and_verify_quest_corpus_manifest,
    load_quest_candidate_bundle_manifest,
    load_quest_corpus_manifest,
    load_textmap_asset_manifest,
)
from .quest_corpus_maintenance import (
    QuestCorpusVerificationError,
    verify_quest_corpus_evidence_db,
)
from .static_book1_maintenance import (
    StaticBook1VerificationError,
    verify_static_book1_evidence_db,
)
from .tutorial_phase4d2a_maintenance import (
    TutorialPhase4D2AVerificationError,
    verify_tutorial_phase4d2a_evidence_db,
)
from .tutorial_phase4d2b_evidence import (
    TutorialPhase4D2BEvidenceError,
    ingest_tutorial_phase4d2b_evidence_bundle,
)
from .tutorial_phase4d2b_maintenance import (
    TutorialPhase4D2BVerificationError,
    verify_tutorial_phase4d2b_evidence_db,
)
from .wave1_rebuild_maintenance import (
    Wave1RebuildVerificationError,
    verify_wave1_full_rebuild_db,
)

__all__ = [
    "CandidateTalkEntry",
    "CorpusInvariants",
    "Database",
    "PreflightCorpusCandidate",
    "PreflightCorpusQuest",
    "PreflightCorpusReference",
    "PreflightQuestCorpusResult",
    "PreflightTextMapShard",
    "QuestBundleReference",
    "QuestCandidateBundleAudit",
    "QuestCandidateBundleManifest",
    "QuestCorpusManifest",
    "QuestFileEntry",
    "QuestTalkReference",
    "SourceSnapshot",
    "TalkDiscoveryIndex",
    "TextMapAssetManifest",
    "TextMapEntry",
    "TextMapManifestReference",
    "VerifiedQuestCorpusManifestSet",
    "QuestCorpusVerificationError",
    "StaticBook1VerificationError",
    "TutorialPhase4D2AVerificationError",
    "TutorialPhase4D2BEvidenceError",
    "TutorialPhase4D2BVerificationError",
    "Wave1RebuildVerificationError",
    "ingest_quest_corpus_evidence_bundle",
    "ingest_slice",
    "ingest_tutorial_phase4d2b_evidence_bundle",
    "load_and_verify_quest_corpus_manifest",
    "load_quest_candidate_bundle_manifest",
    "load_quest_corpus_manifest",
    "load_talk_discovery_index_cache",
    "load_textmap_asset_manifest",
    "preflight_quest_corpus_evidence_bundle",
    "verify_quest_corpus_evidence_db",
    "verify_static_book1_evidence_db",
    "verify_tutorial_phase4d2a_evidence_db",
    "verify_tutorial_phase4d2b_evidence_db",
    "verify_wave1_full_rebuild_db",
]
