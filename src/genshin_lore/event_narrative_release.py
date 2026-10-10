"""Exact verifier-proof binding for Event narrative packaging and release."""
from pathlib import Path
from .event_narrative_source import sha

PROOF_FIELDS=('snapshot_id','scope_sha256','implementation','parser_build_id','run_id','contract','counts')
DETERMINISM_FIELDS=('status','byte_exact','logical_exact','artifact_a','artifact_b','run_id','parser_build_id','logical_seals')

def require_exact_verifier_proof(artifact,build,proof):
 artifact=Path(artifact)
 if proof.get('status')!='pass' or not proof.get('identity_independently_reconstructed'):
  raise ValueError('Event verifier proof status gate')
 for field in ('actual_bytes_git_blob_match','full_business_column_equality','no_extra_rows'):
  if proof.get(field) is not True:
   raise ValueError('Event verifier proof boolean gate: '+field)
 exact={'size_bytes':artifact.stat().st_size,'sha256':sha(artifact)}
 if proof.get('verified_artifact')!=exact or build.get('artifact')!=exact:
  raise ValueError('Event verifier exact artifact proof mismatch')
 for field in PROOF_FIELDS:
  if field not in proof or proof[field]!=build.get(field):
   raise ValueError('Event verifier exact proof mismatch: '+field)
 if set(proof.get('logical_seals',{}))!=set(build.get('counts',{})):
  raise ValueError('Event verifier logical seals missing')
 return True

def require_exact_determinism_proof(build,proof,determinism):
 """Bind one determinism report to the exact build and verifier proof."""
 missing=[field for field in DETERMINISM_FIELDS if field not in determinism]
 if missing:raise ValueError('Event determinism proof missing: '+','.join(missing))
 if determinism['status']!='pass' or determinism['byte_exact'] is not True or determinism['logical_exact'] is not True:
  raise ValueError('Event determinism status gate')
 artifact=build.get('artifact')
 if determinism['artifact_a']!=artifact or determinism['artifact_b']!=artifact:
  raise ValueError('Event determinism A/B artifact identity mismatch')
 if determinism['run_id']!=build.get('run_id') or determinism['parser_build_id']!=build.get('parser_build_id'):
  raise ValueError('Event determinism identity mismatch')
 if determinism['logical_seals']!=proof.get('logical_seals'):
  raise ValueError('Event determinism logical seals mismatch')
 return True
