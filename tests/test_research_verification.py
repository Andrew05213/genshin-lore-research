"""Independent rowset completeness negatives, not builder projection tests."""
import ast
from pathlib import Path
import sqlite3

import pytest

from scripts.validators.research_dataset import verify_projection_counts

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE adapter_coverage(source_key,projection_kind,source_table,indexed_count)')
    for name in ('research_container', 'research_text', 'research_relation', 'research_candidate'):
        db.execute(f'CREATE TABLE {name}(id,source_key,source_table,pk_column,pk_value,relation_id)')
    for kind, table in [('container', 'research_container'), ('text', 'research_text'),
                        ('relation', 'research_relation'), ('candidate', 'research_candidate')]:
        db.execute('INSERT INTO adapter_coverage VALUES(?,?,?,?)', ('fixture', kind, kind, 2))
        for n in (1, 2):
            db.execute(f'INSERT INTO {table} VALUES(?,?,?,?,?,?)', (n, 'fixture', kind, 'id', str(n), n))
    return db


def test_all_declared_rowsets_present():
    db = fixture()
    verify_projection_counts(db)
    db.close()


@pytest.mark.parametrize('table', ['research_container', 'research_text', 'research_relation', 'research_candidate'])
def test_omitted_index_row_cannot_hide_behind_unchanged_coverage(table):
    db = fixture()
    db.execute(f'DELETE FROM {table} WHERE id=2')
    with pytest.raises(ValueError, match='rowset differs'):
        verify_projection_counts(db)
    db.close()


def test_duplicate_source_row_is_not_completeness():
    db = fixture()
    db.execute("UPDATE research_text SET pk_value='1' WHERE id=2")
    with pytest.raises(ValueError, match='duplicate exact'):
        verify_projection_counts(db)
    db.close()


def test_dangling_reference_cannot_disappear_in_relation_join():
    db = fixture()
    db.execute('CREATE TABLE declared_parent(id INTEGER PRIMARY KEY)')
    db.execute('CREATE TABLE declared_child(parent_id REFERENCES declared_parent(id))')
    db.execute('INSERT INTO declared_child VALUES(999)')
    with pytest.raises(ValueError, match='dangling physical references'):
        verify_projection_counts(db)
    db.close()


def test_unreported_projection_rejected():
    db = fixture()
    db.execute("DELETE FROM adapter_coverage WHERE projection_kind='text'")
    with pytest.raises(ValueError, match='lacks coverage'):
        verify_projection_counts(db)
    db.close()


@pytest.mark.parametrize('name', ['research_index', 'research_dataset', 'semantic'])
def test_current_research_verifiers_have_no_optimization_sensitive_assert(name):
    tree = ast.parse((ROOT/'scripts/validators'/f'{name}.py').read_text())
    assert not any(isinstance(node, ast.Assert) for node in ast.walk(tree))


def test_complete_oracle_does_not_import_builder_projection_helpers():
    tree = ast.parse((ROOT/'scripts/validators/research_dataset.py').read_text())
    imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not any('builder' in (name or '') or 'candidate_adapters' in (name or '')
                   or 'candidate_relations' in (name or '') for name in imports)
