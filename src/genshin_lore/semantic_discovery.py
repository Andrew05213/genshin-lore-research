"""Derived semantic candidate recall. Never an evidence or relation authority."""
from __future__ import annotations

import hashlib
import sqlite3
import threading
import numpy as np
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any


class SemanticUnavailable(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    # Hash the same case-sensitive relative-name order on every platform;
    # WindowsPath's native comparisons otherwise move mixed-case filenames.
    for child in sorted(
        (item for item in path.rglob("*") if item.is_file()),
        key=lambda item: item.relative_to(path).as_posix(),
    ):
        digest.update(child.relative_to(path).as_posix().encode() + b"\0")
        digest.update(bytes.fromhex(sha256_file(child)))
    return digest.hexdigest()


def _fingerprint(path: Path) -> tuple[int, int, int, int]:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def _tree_fingerprint(path: Path) -> tuple[tuple[str, int, int, int, int], ...]:
    output = []
    for child in sorted(item for item in path.rglob("*") if item.is_file()):
        stat = child.stat()
        output.append((str(child.relative_to(path)), stat.st_dev, stat.st_ino,
                       stat.st_size, stat.st_mtime_ns))
    return tuple(output)


class SemanticDiscovery:
    def __init__(
        self,
        config: dict[str, Any],
        bindings: dict[str, Any],
        corpus_ref: str,
        research_index_sha256: str,
    ):
        supported_contracts = {
            "semantic-discovery-overlay-v1",
            "semantic-hybrid-discovery-overlay-v1",
        }
        if (
            config.get("contract") not in supported_contracts
            or config.get("corpus_ref") != corpus_ref
            or config.get("research_index_sha256") != research_index_sha256
        ):
            raise SemanticUnavailable("semantic overlay corpus binding mismatch")
        if config.get("contract") == "semantic-hybrid-discovery-overlay-v1":
            try:
                dependency_match = (
                    version("hnswlib") == config["index_implementation"]["version"]
                    and version("fastembed")
                    == config["embedding_implementation"]["version"]
                )
            except (KeyError, PackageNotFoundError) as exc:
                raise SemanticUnavailable(
                    "semantic runtime dependency contract unavailable") from exc
            if not dependency_match:
                raise SemanticUnavailable("semantic runtime dependency version mismatch")

        paths = bindings.get("semantic_discovery", {})
        self.meta_path = Path(paths.get("metadata", ""))
        self.hnsw_path = Path(paths.get("hnsw", ""))
        self.cache = Path(paths.get("model_cache", ""))
        for path, key in ((self.meta_path, "metadata"), (self.hnsw_path, "hnsw")):
            expected = config["artifacts"][key]
            if (
                not path.is_file()
                or path.stat().st_size != expected["size_bytes"]
                or sha256_file(path) != expected["sha256"]
            ):
                raise SemanticUnavailable(f"semantic {key} identity mismatch")
        if (
            not self.cache.is_dir()
            or tree_sha256(self.cache) != config["model"]["cache_tree_sha256"]
        ):
            raise SemanticUnavailable("semantic model identity mismatch")
        self.model_path = (self.cache / config["model"]["path_relative"]).resolve()
        if (self.cache.resolve() not in self.model_path.parents
                or not self.model_path.is_dir()
                or tree_sha256(self.model_path) != config["model"]["tree_sha256"]):
            raise SemanticUnavailable("semantic model files identity mismatch")
        if any(
            Path(str(self.meta_path) + suffix).exists()
            for suffix in ("-journal", "-wal", "-shm")
        ):
            raise SemanticUnavailable("semantic metadata sidecar present")

        self.db = sqlite3.connect(
            self.meta_path.resolve().as_uri()+"?mode=ro&immutable=1",
            uri=True,
            check_same_thread=False,
        )
        self.db.row_factory = sqlite3.Row
        self.db.execute("pragma query_only=on")
        release = self.db.execute(
            "select * from semantic_release where id=1"
        ).fetchone()
        if config["contract"] == "semantic-hybrid-discovery-overlay-v1":
            expected_release = (
                config["contract"], config["contract_config_sha256"], corpus_ref,
                research_index_sha256, config["lexicon_sha256"],
                config["model"]["id"], config["model"]["revision"],
                config["model"]["tree_sha256"], config["dimension"], config["metric"],
                config["representation_version"], config["candidate_extractor"],
                config["selection"], config["h2_fusion_version"],
                config["unique_candidate_count"], config["physical_occurrence_count"],
            )
            actual_release = tuple(release[key] for key in (
                "contract", "contract_sha256", "corpus_ref", "research_index_sha256",
                "lexicon_sha256", "model_id", "model_revision", "model_tree_sha256",
                "dimension", "metric", "representation_version", "candidate_extractor",
                "selection", "h2_fusion_version", "unique_candidate_count",
                "physical_occurrence_count",
            )) if release is not None else None
        else:
            expected_release = (
                corpus_ref, research_index_sha256, config["model"]["id"],
                config["model"]["revision"], config["model"]["tree_sha256"],
                config["dimension"], config["metric"], config["preprocessor"],
                config["candidate_extractor"], config["selection"],
                config["unique_candidate_count"], config["physical_occurrence_count"],
            )
            actual_release = tuple(release[key] for key in (
                "corpus_ref", "research_index_sha256", "model_id", "model_revision",
                "model_tree_sha256", "dimension", "metric", "preprocessor",
                "candidate_extractor", "selection", "unique_candidate_count",
                "physical_occurrence_count",
            )) if release is not None else None
        if actual_release != expected_release:
            self.db.close()
            raise SemanticUnavailable("semantic release mismatch")

        try:
            import hnswlib
        except ImportError as exc:
            self.db.close()
            raise SemanticUnavailable("semantic runtime dependencies unavailable") from exc
        try:
            self.index = hnswlib.Index(space=config["metric"], dim=config["dimension"])
            self.index.load_index(
                str(self.hnsw_path), max_elements=config["unique_candidate_count"]
            )
        except Exception as exc:
            self.db.close()
            raise SemanticUnavailable("semantic HNSW load failed") from exc
        if self.index.get_current_count() != config["unique_candidate_count"]:
            self.db.close()
            raise SemanticUnavailable("semantic HNSW count mismatch")
        self.index.set_ef(config.get("query_ef", 200))
        self.config = config
        self._model = None
        self._lock = threading.RLock()
        self._fingerprints = {
            "metadata": _fingerprint(self.meta_path),
            "hnsw": _fingerprint(self.hnsw_path),
        }
        self._model_cache_fingerprint = _tree_fingerprint(self.cache)

    def close(self) -> None:
        self.db.close()

    def check_stable(self) -> None:
        if any(
            Path(str(self.meta_path) + suffix).exists()
            for suffix in ("-journal", "-wal", "-shm")
        ):
            raise SemanticUnavailable("semantic metadata sidecar appeared")
        try:
            drifted = (
                _fingerprint(self.meta_path) != self._fingerprints["metadata"]
                or _fingerprint(self.hnsw_path) != self._fingerprints["hnsw"]
                or _tree_fingerprint(self.cache) != self._model_cache_fingerprint
            )
        except OSError as exc:
            raise SemanticUnavailable("semantic overlay binding disappeared") from exc
        if drifted:
            raise SemanticUnavailable("semantic overlay binding drift")

    def _embed(self, query: str):
        if self._model is None:
            try:
                from fastembed import TextEmbedding
            except ImportError as exc:
                raise SemanticUnavailable(
                    "semantic runtime dependencies unavailable"
                ) from exc
            self._model = TextEmbedding(
                model_name=self.config["model"]["id"],
                cache_dir=str(self.cache),
                threads=self.config["model"].get("threads", 2),
                specific_model_path=str(self.model_path),
                local_files_only=True,
            )
        vector = np.asarray(next(iter(self._model.query_embed([query]))), dtype=np.float32)
        norm = float(np.linalg.norm(vector))
        if not np.all(np.isfinite(vector)) or norm <= 0:
            raise SemanticUnavailable("semantic query embedding is not finite")
        return vector / norm

    def query_vector(self, query: str):
        """Return one qualified, normalized query vector for a whole search call."""
        with self._lock:
            self.check_stable()
            return self._embed(query)

    def candidates_from_vector(self, vector, k: int | None = None) -> list[dict[str, Any]]:
        requested = self.config.get("query_candidate_k", 1000) if k is None else int(k)
        requested = max(1, min(requested, self.config["unique_candidate_count"]))
        with self._lock:
            self.check_stable()
            labels, distances = self.index.knn_query(vector, k=requested)
            output = []
            for label, distance in zip(labels[0], distances[0]):
                rows = self.db.execute(
                    "select research_text_id from semantic_occurrence "
                    "where vector_id=? order by research_text_id",
                    (int(label),),
                )
                score = 1.0 - float(distance)
                output.extend(
                    {
                        "research_text_id": int(row[0]),
                        "semantic_score": score,
                        "vector_id": int(label),
                    }
                    for row in rows
                )
        return output

    def scores_from_vector(self, vector, research_text_ids: list[int]) -> dict[int, float]:
        """Cosine-score exact physical candidates without changing their set."""
        wanted = sorted(set(int(value) for value in research_text_ids))
        if not wanted:
            return {}
        mapping: dict[int, int] = {}
        with self._lock:
            self.check_stable()
            for start in range(0, len(wanted), 800):
                chunk = wanted[start:start + 800]
                rows = self.db.execute(
                    "select research_text_id,vector_id from semantic_occurrence "
                    "where research_text_id in (" + ",".join("?" * len(chunk)) + ")",
                    chunk,
                )
                mapping.update((int(row[0]), int(row[1])) for row in rows)
            labels = sorted(set(mapping.values()))
            if not labels:
                return {}
            vectors = np.asarray(self.index.get_items(labels), dtype=np.float32)
            norms = np.linalg.norm(vectors, axis=1)
            if not np.all(np.isfinite(vectors)) or np.any(norms <= 0):
                raise SemanticUnavailable("stored semantic vector is not finite")
            scores_by_label = {
                label: float(np.dot(vectors[index] / norms[index], vector))
                for index, label in enumerate(labels)
            }
        return {text_id: scores_by_label[label] for text_id, label in mapping.items()}

    def candidates(self, query: str, k: int | None = None) -> list[dict[str, Any]]:
        return self.candidates_from_vector(self.query_vector(query), k)

    def coverage(self) -> dict[str, Any]:
        return {
            "contract": self.config["contract"],
            "candidate_retrieval_complete": False,
            "indexed_locale": "CHS",
            "selection": self.config["selection"],
            "candidate_extractor": self.config["candidate_extractor"],
            "representation_version": self.config.get("representation_version"),
            "h2_fusion_version": self.config.get("h2_fusion_version"),
            "lexicon_sha256": self.config.get("lexicon_sha256"),
            "unique_candidates": self.config["unique_candidate_count"],
            "physical_occurrences": self.config["physical_occurrence_count"],
            "query_candidate_k": self.config.get("query_candidate_k", 1000),
            "model": self.config["model"]["id"],
            "metric": self.config["metric"],
            "semantic_indexed_scopes": self.config["semantic_indexed_scopes"],
            "semantic_unsupported_scopes": self.config["semantic_unsupported_scopes"],
            "semantic_unavailable_scopes": [],
            "derived_index_is_evidence_truth": False,
        }
