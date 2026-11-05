"""Strict 7.1 Fetter Voice physical decoder; no logical Character identity."""
from dataclasses import dataclass


LUT_PATH = "BinOutput/Voice/Lut/Lut.json"
LUT_CATEGORY = "NLGBEEIGDJP"
LUT_SLOT = "BMPENHIILJG"
LUT_ITEM_FILE = "PFMLOBHKGJO"
ITEM_CATEGORY = "NLGBEEIGDJP"
ITEM_SLOT = "BMPENHIILJG"
ITEM_OBJECT_KEY = "OKAHIJCNNFM"
ITEM_VARIANTS = "GLDBLKKLJHP"
AUDIO_PATH = "CFIKOCLOKGH"
AUDIO_SELECTOR = "CCCECEDMIEI"
AUDIO_ORDER_VALUE = "EEJEGIEMJBL"


@dataclass(frozen=True)
class VoiceLutRecord:
    source_key: str
    pointer: str
    slot: str
    item_path: str


@dataclass(frozen=True)
class VoiceItemRecord:
    source_path: str
    source_key: str
    pointer: str
    slot: str
    variant_count: int
    variants_status: str


@dataclass(frozen=True)
class AudioVariant:
    source_path: str
    source_key: str
    ordinal: int
    pointer: str
    audio_path: str
    selector: str
    order_value: float


def decode_fetter_lut(key: str, row: dict, *, source_version="7.1") -> VoiceLutRecord | None:
    category, slot_field, file_field = {
        "7.1": (LUT_CATEGORY, LUT_SLOT, LUT_ITEM_FILE),
        "7.2": ("gameTrigger", "gameTriggerArgs", "fileID"),
    }[source_version]
    if type(key) is not str or type(row) is not dict:
        raise ValueError("7.1 Voice LUT object shape")
    if row.get(category) != "Fetter":
        return None
    if any(old in row for old in ("PKPBCKFKHEE", "MHDDLMLPIBB", "DFEELKOFAKE")):
        raise ValueError("7.0 Voice LUT fields in 7.1 decoder")
    slot, file_id = row.get(slot_field), row.get(file_field)
    if type(slot) is not int or type(file_id) is not int:
        raise ValueError("7.1 Fetter Voice LUT slot/file locator shape")
    return VoiceLutRecord(key, "/" + key, str(slot), f"BinOutput/Voice/Items/{file_id}.json")


def decode_fetter_item(path: str, key: str, row: dict, *, source_version="7.1") -> tuple[VoiceItemRecord, tuple[AudioVariant, ...]]:
    category, slot, object_key, variant_field, audio_path, selector, order = {
        "7.1": (ITEM_CATEGORY, ITEM_SLOT, ITEM_OBJECT_KEY, ITEM_VARIANTS, AUDIO_PATH, AUDIO_SELECTOR, AUDIO_ORDER_VALUE),
        "7.2": ("gameTrigger", "gameTriggerArgs", "guid", "sourceNames", "sourceFileName", "avatarName", "rate"),
    }[source_version]
    if not path.startswith("BinOutput/Voice/Items/") or not path.endswith(".json") or type(key) is not str or type(row) is not dict:
        raise ValueError("7.1 Voice Items source shape")
    if any(old in row for old in ("PKPBCKFKHEE", "MHDDLMLPIBB", "BOICINMJKCP", "AIFHPAAHJOC")):
        raise ValueError("7.0 Voice Items fields in 7.1 decoder")
    if row.get(category) != "Fetter" or row.get(object_key) != key or type(row.get(slot)) is not int:
        raise ValueError("7.1 Fetter Voice Items category/key/slot shape")
    variants = row.get(variant_field)
    status = "present" if variant_field in row else "missing"
    if status == "missing":
        variants = []
    elif type(variants) is not list:
        raise ValueError("7.1 Fetter Voice variant array shape")
    audio = []
    for ordinal, variant in enumerate(variants):
        if type(variant) is not dict or type(variant.get(audio_path)) is not str or type(variant.get(selector)) is not str or type(variant.get(order)) not in (int, float):
            raise ValueError(f"Fetter Voice audio variant shape: {path}#/{key}/{variant_field}/{ordinal}")
        audio.append(AudioVariant(path, key, ordinal, f"/{key}/{variant_field}/{ordinal}", variant[audio_path], variant[selector], float(variant[order])))
    return VoiceItemRecord(path, key, "/" + key, str(row[slot]), len(audio), status), tuple(audio)
