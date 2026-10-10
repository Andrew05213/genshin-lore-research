class LoreDatabaseError(Exception):
    """Base error for deterministic ingestion/retrieval failures."""


class ManifestError(LoreDatabaseError):
    """The source manifest is malformed or does not identify a snapshot."""


class SourceMissingError(LoreDatabaseError):
    """A required source file is absent from all configured roots."""


class SchemaCanaryError(LoreDatabaseError):
    """A required 7.0 structure did not match its verified canary."""


class TextMapConflictError(LoreDatabaseError):
    """A TextMap hash has different values in merged shards."""

