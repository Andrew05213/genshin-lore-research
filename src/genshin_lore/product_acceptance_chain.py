"""Fail-closed inheritance for current-product acceptance seals."""
from __future__ import annotations
import hashlib
from pathlib import Path

def sha(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(4*1024*1024),b''): h.update(block)
    return h.hexdigest()

def inherit_acceptance(root: Path, bundles: list[dict], replaced_paths: set[str]) -> dict:
    """Validate inherited bytes first, then omit only explicitly replaced paths."""
    inherited={}
    for bundle in bundles:
        for path,seal in bundle.get('acceptance',{}).items():
            digest=seal.get('sha256')
            if not isinstance(digest,str): raise ValueError('invalid inherited acceptance seal '+path)
            if path in inherited and inherited[path]['sha256']!=digest:
                raise ValueError('conflicting inherited acceptance seal '+path)
            inherited[path]={'sha256':digest}
    for path,seal in inherited.items():
        p=root/path
        if not p.is_file() or sha(p)!=seal['sha256']:
            raise ValueError('historical acceptance drift '+path)
    return {p:s for p,s in inherited.items() if p not in replaced_paths}

def effective_acceptance(root: Path, bundle: dict, load_bundle) -> dict:
    """Resolve the historical chain oldest-first; newer seals may replace authority files."""
    out={}
    previous=bundle.get('historical_previous_bundle')
    if previous:
        out.update(effective_acceptance(root,load_bundle(previous),load_bundle))
    out.update({p:{'sha256':s['sha256']} for p,s in bundle.get('acceptance',{}).items()})
    return out

def validate_acceptance_chain(root: Path, bundle: dict, load_bundle) -> dict:
    seals=effective_acceptance(root,bundle,load_bundle)
    for path,seal in seals.items():
        p=root/path
        if not p.is_file() or sha(p)!=seal['sha256']:
            raise ValueError('historical acceptance drift '+path)
    return seals

def inherit_effective_acceptance(root: Path, bundle: dict, load_bundle, replaced_paths: set[str]) -> dict:
    """Validate every retained historical seal; only declared current authority may change."""
    seals=effective_acceptance(root,bundle,load_bundle)
    retained={p:s for p,s in seals.items() if p not in replaced_paths}
    for path,seal in retained.items():
        p=root/path
        if not p.is_file() or sha(p)!=seal['sha256']:
            raise ValueError('historical acceptance drift '+path)
    return retained
