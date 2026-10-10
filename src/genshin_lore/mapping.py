"""Verified 7.0 RC field mapping used by the selected 7.0 canaries.

The upstream release is R47482070 while the public mapping checkpoint is
R47194594.  The parser therefore records this mapping version and validates
the selected release with native-ID/title/dialog canaries before importing.
"""

from __future__ import annotations

from typing import Any


MAPPING_VERSION = "anime-game-data-7.0-rc-r47194594-validated-on-r47482070"

QUEST_MAP: dict[str, str] = {
    "OIFGMOHKPOI": "id",
    "OANENPOPCFO": "descTextMapHash",
    "LKAHEACOLML": "titleTextMapHash",
    "BEADANLODNC": "chapterId",
    "EBNBLBEIFFJ": "subQuests",
    "OJACLOOEAMG": "talks",
    "KCGAKLCHDCC": "subId",
    "EMNMIOBCCLL": "order",
    "EPGEOMCHIJF": "questId",
    "BMFEMALEAIO": "npcId",
    "FCBOEAHDNOL": "beginCond",
    "OAENEGDKPNB": "configId",
    "OOIJCIKMBOJ": "groupId",
    "FAJAGGKJICO": "activityId",
    "ANBEKNMDKCH": "finishCond",
    "OPDGHDAADJC": "param",
    "LIKEIGNEHNP": "count",
    "DPMLKCPEIJD": "CUSTOM_paramStr",
}

TALK_MAP: dict[str, str] = {
    "OIFGMOHKPOI": "id",
    "IOKNFDJFGDH": "talkId",
    "PFALHAKIILD": "dialogList",
    "KMLAFCBMFEI": "nextDialogs",
    "LFGCLNLPAPB": "talkRole",
    "OACNIBLFFDI": "talkContentTextMapHash",
    "ACCOJJPKFCN": "talkRoleNameTextMapHash",
    # The following two names are validated against TalkExcel for 700814.
    "MCIAGDCECPL": "initDialog",
}

# Quest talk references use this key for the raw perform-config path.  It is
# deliberately not inferred from a spelling convention: the 7008 fixture and
# TalkExcel rows are compared before accepting it.
QUEST_TALK_MAP: dict[str, str] = {
    "OIFGMOHKPOI": "talkId",
    "EDIMJFOBEKG": "performCfg",
    "MCIAGDCECPL": "initDialog",
    "BMFEMALEAIO": "npcId",
    "EPGEOMCHIJF": "questId",
}


def deobfuscate(value: Any, mapping: dict[str, str]) -> Any:
    """Recursively rename only keys supported by a versioned mapping.

    Unknown fields are retained verbatim.  A collision is rejected because
    silently selecting one obfuscated field would corrupt provenance.
    """
    if isinstance(value, list):
        return [deobfuscate(item, mapping) for item in value]
    if not isinstance(value, dict):
        return value
    result: dict[str, Any] = {}
    for key, item in value.items():
        target = mapping.get(key, key)
        if target in result and target != key:
            raise ValueError(f"deobfuscation key collision: {key!r} -> {target!r}")
        result[target] = deobfuscate(item, mapping)
    return result
