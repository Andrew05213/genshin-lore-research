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


def decode_fetter_lut(key: str, row: dict) -> VoiceLutRecord | None:
    if type(key) is not str or type(row) is not dict:
        raise ValueError("7.1 Voice LUT object shape")
    if row.get(LUT_CATEGORY) != "Fetter":
        return None
    if any(old in row for old in ("PKPBCKFKHEE", "MHDDLMLPIBB", "DFEELKOFAKE")):
        raise ValueError("7.0 Voice LUT fields in 7.1 decoder")
    slot, file_id = row.get(LUT_SLOT), row.get(LUT_ITEM_FILE)
    if type(slot) is not int or type(file_id) is not int:
        raise ValueError("7.1 Fetter Voice LUT slot/file locator shape")
    return VoiceLutRecord(key, "/" + key, str(slot), f"BinOutput/Voice/Items/{file_id}.json")


def decode_fetter_item(path: str, key: str, row: dict) -> tuple[VoiceItemRecord, tuple[AudioVariant, ...]]:
    if not path.startswith("BinOutput/Voice/Items/") or not path.endswith(".json") or type(key) is not str or type(row) is not dict:
        raise ValueError("7.1 Voice Items source shape")
    if any(old in row for old in ("PKPBCKFKHEE", "MHDDLMLPIBB", "BOICINMJKCP", "AIFHPAAHJOC")):
        raise ValueError("7.0 Voice Items fields in 7.1 decoder")
    if row.get(ITEM_CATEGORY) != "Fetter" or row.get(ITEM_OBJECT_KEY) != key or type(row.get(ITEM_SLOT)) is not int:
        raise ValueError("7.1 Fetter Voice Items category/key/slot shape")
    variants = row.get(ITEM_VARIANTS)
    status = "present" if ITEM_VARIANTS in row else "missing"
    if status == "missing":
        variants = []
    elif type(variants) is not list:
        raise ValueError("7.1 Fetter Voice variant array shape")
    audio = []
    for ordinal, variant in enumerate(variants):
        if type(variant) is not dict or type(variant.get(AUDIO_PATH)) is not str or type(variant.get(AUDIO_SELECTOR)) is not str or type(variant.get(AUDIO_ORDER_VALUE)) not in (int, float):
            raise ValueError(f"7.1 Fetter Voice audio variant shape: {path}#/{key}/{ITEM_VARIANTS}/{ordinal}")
        audio.append(AudioVariant(path, key, ordinal, f"/{key}/{ITEM_VARIANTS}/{ordinal}", variant[AUDIO_PATH], variant[AUDIO_SELECTOR], float(variant[AUDIO_ORDER_VALUE])))
    return VoiceItemRecord(path, key, "/" + key, str(row[ITEM_SLOT]), len(audio), status), tuple(audio)
