"""Strict 7.1 Character primary-row field decoder; not an evidence successor.

This consumes only source-authored fields. Opaque fields remain in the lexical raw
record and are never mapped by name similarity to 7.0 fields.
"""
from __future__ import annotations

from dataclasses import dataclass


REQUIRED_INTEGER_FIELDS = {
    "AvatarExcelConfigData.json": ("id", "nameTextMapHash", "descTextMapHash"),
    "FetterInfoExcelConfigData.json": ("avatarId", "fetterId", "avatarConstellationAfterTextMapHash", "avatarConstellationBeforTextMapHash", "avatarDetailTextMapHash", "avatarNativeTextMapHash", "avatarTitleTextMapHash", "avatarVisionAfterTextMapHash", "avatarVisionBeforTextMapHash", "cvChineseTextMapHash", "cvEnglishTextMapHash", "cvJapaneseTextMapHash", "cvKoreanTextMapHash"),
    "FetterStoryExcelConfigData.json": ("avatarId", "fetterId", "storyContext2TextMapHash", "storyContextTextMapHash", "storyTitle2TextMapHash", "storyTitleLockedTextMapHash", "storyTitleTextMapHash"),
    "FettersExcelConfigData.json": ("avatarId", "fetterId", "voiceFileTextTextMapHash", "voiceTitleLockedTextMapHash", "voiceTitleTextMapHash"),
    "AvatarCostumeExcelConfigData.json": ("skinId", "characterId", "nameTextMapHash", "descTextMapHash"),
    "ProfilePictureExcelConfigData.json": ("id", "nameTextMapHash", "unlockDescTextMapHash", "priority"),
    "AvatarRenameExcelConfigData.json": ("renameID", "relatedAvatarID"),
}
TEXT_HASH_FIELDS = {
    "AvatarExcelConfigData.json": ("nameTextMapHash", "descTextMapHash"),
    "FetterInfoExcelConfigData.json": tuple(field for field in REQUIRED_INTEGER_FIELDS["FetterInfoExcelConfigData.json"] if field.endswith("TextMapHash")),
    "FetterStoryExcelConfigData.json": tuple(field for field in REQUIRED_INTEGER_FIELDS["FetterStoryExcelConfigData.json"] if field.endswith("TextMapHash")),
    "FettersExcelConfigData.json": tuple(field for field in REQUIRED_INTEGER_FIELDS["FettersExcelConfigData.json"] if field.endswith("TextMapHash")),
    "AvatarCostumeExcelConfigData.json": ("nameTextMapHash", "descTextMapHash"),
    "ProfilePictureExcelConfigData.json": ("nameTextMapHash", "unlockDescTextMapHash"),
}
CONDITION_FIELDS = {
    "FetterInfoExcelConfigData.json": ("finishConds",),
    "FetterStoryExcelConfigData.json": ("finishConds", "openConds"),
    "FettersExcelConfigData.json": ("openConds",),
}
PROFILE_DISCRIMINATORS = frozenset({
    "PROFILE_PICTURE_UNLOCK_BY_AVATAR",
    "PROFILE_PICTURE_UNLOCK_BY_COSTUME",
    "PROFILE_PICTURE_UNLOCK_BY_PARENT_QUEST",
    "PROFILE_PICTURE_UNLOCK_BY_ITEM",
    "PROFILE_PICTURE_UNLOCK_BY_DEFAULT",
})


@dataclass(frozen=True)
class DecodedPrimaryRow:
    source_path: str
    ordinal: int
    coordinate: str
    source_fields: tuple[tuple[str, object], ...]
    raw_json: str


def decode_primary_row(source_path: str, ordinal: int, row: dict, raw_json: str) -> DecodedPrimaryRow:
    basename = source_path.rsplit("/", 1)[-1]
    if basename not in REQUIRED_INTEGER_FIELDS or source_path != "ExcelBinOutput/" + basename:
        raise ValueError("unsupported 7.1 Character source path")
    if type(ordinal) is not int or ordinal < 0 or type(row) is not dict or type(raw_json) is not str:
        raise ValueError("invalid 7.1 Character physical row")
    fields = []
    for name in REQUIRED_INTEGER_FIELDS[basename]:
        value = row.get(name)
        if type(value) is not int:
            raise ValueError(f"{source_path}#/{ordinal}/{name}: required integer field missing or changed")
        fields.append((name, value))
    for name in TEXT_HASH_FIELDS.get(basename, ()):
        if row[name] <= 0:
            raise ValueError(f"{source_path}#/{ordinal}/{name}: TextMap locator must be positive")
    for name in CONDITION_FIELDS.get(basename, ()):
        values = row.get(name)
        if type(values) is not list or any(type(value) is not dict for value in values):
            raise ValueError(f"{source_path}#/{ordinal}/{name}: condition array shape")
        fields.append((name, values))
    if basename in ("FetterStoryExcelConfigData.json", "FettersExcelConfigData.json"):
        tips = row.get("tips")
        if type(tips) is not list or any(type(value) is not int for value in tips):
            raise ValueError(f"{source_path}#/{ordinal}/tips: opaque integer array shape")
        fields.append(("tips", tips))
    if basename == "FettersExcelConfigData.json":
        voice_slot = row.get("voiceFile")
        if type(voice_slot) is not str or not voice_slot:
            raise ValueError(f"{source_path}#/{ordinal}/voiceFile: physical slot locator")
        fields.append(("voiceFile", voice_slot))
    if basename == "ProfilePictureExcelConfigData.json":
        if "AHJHBENMGFK" in row:
            raise ValueError("7.0 ProfilePicture discriminator in 7.1 decoder")
        kind = row.get("NLAPNBMECDF")
        if kind not in PROFILE_DISCRIMINATORS:
            raise ValueError(f"{source_path}#/{ordinal}/NLAPNBMECDF: unknown discriminator")
        value = row.get("unlockParam")
        if kind == "PROFILE_PICTURE_UNLOCK_BY_DEFAULT":
            if value is not None:
                raise ValueError("default unlockParam must be absent")
        elif type(value) is not int:
            raise ValueError("non-default unlockParam must be integer")
        fields.extend((("NLAPNBMECDF", kind), ("unlockParam", value)))
    elif basename == "AvatarRenameExcelConfigData.json":
        names = row.get("multiLangDefaultNameList")
        if type(names) is not list or any(type(item) is not dict or type(item.get("languageType")) is not str or type(item.get("planeText")) is not str for item in names):
            raise ValueError("7.1 AvatarRename multilingual source shape")
        fields.append(("multiLangDefaultNameList", names))
    return DecodedPrimaryRow(source_path, ordinal, f"/{ordinal}", tuple(fields), raw_json)
