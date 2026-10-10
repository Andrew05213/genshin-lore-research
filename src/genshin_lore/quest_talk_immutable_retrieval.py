"""Explicit immutable v6 runs; reuse the accepted literal retrieval contract."""
from .quest_talk_completeness_retrieval import QuestTalkCompleteProvider
class QuestTalkImmutableProvider(QuestTalkCompleteProvider):
    family_contract_version='quest-talk-evidence-v6'
    runtime_contract='quest-talk-immutable-runtime-v1'
    mapping_version='physical-immutable-v1'
    product_accepted=False

    def __init__(self,artifact_path,runtime_manifest_path):
        import json
        from pathlib import Path
        accepted=json.loads(Path(runtime_manifest_path).read_text())['product_accepted']
        if type(accepted) is not bool:raise ValueError('product acceptance must be explicit boolean')
        self.product_accepted=accepted
        super().__init__(artifact_path,runtime_manifest_path)
