-- Batch correction: restore the write-once INSERT guard omitted by migration 0017.
-- This migration is additive. It does not alter Schema 17 rows or historical DDL.
-- UPDATE/DELETE guards remain owned by 0017; these guards close INSERT OR REPLACE.

CREATE TRIGGER trg_cook_recipe_archive_row_occurrence_immutable_insert_existing
BEFORE INSERT ON cook_recipe_archive_row_occurrence
WHEN EXISTS (SELECT 1 FROM cook_recipe_archive_row_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'cook_recipe_archive_row_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_cook_recipe_archive_raw_field_occurrence_immutable_insert_existing
BEFORE INSERT ON cook_recipe_archive_raw_field_occurrence
WHEN EXISTS (SELECT 1 FROM cook_recipe_archive_raw_field_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'cook_recipe_archive_raw_field_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_cook_recipe_archive_relationship_occurrence_immutable_insert_existing
BEFORE INSERT ON cook_recipe_archive_relationship_occurrence
WHEN EXISTS (SELECT 1 FROM cook_recipe_archive_relationship_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'cook_recipe_archive_relationship_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_cook_recipe_archive_relationship_candidate_occurrence_immutable_insert_existing
BEFORE INSERT ON cook_recipe_archive_relationship_candidate_occurrence
WHEN EXISTS (SELECT 1 FROM cook_recipe_archive_relationship_candidate_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'cook_recipe_archive_relationship_candidate_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_cook_recipe_archive_text_owner_occurrence_immutable_insert_existing
BEFORE INSERT ON cook_recipe_archive_text_owner_occurrence
WHEN EXISTS (SELECT 1 FROM cook_recipe_archive_text_owner_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'cook_recipe_archive_text_owner_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_cook_recipe_archive_text_resolution_occurrence_immutable_insert_existing
BEFORE INSERT ON cook_recipe_archive_text_resolution_occurrence
WHEN EXISTS (SELECT 1 FROM cook_recipe_archive_text_resolution_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'cook_recipe_archive_text_resolution_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_cook_recipe_archive_text_candidate_occurrence_immutable_insert_existing
BEFORE INSERT ON cook_recipe_archive_text_candidate_occurrence
WHEN EXISTS (SELECT 1 FROM cook_recipe_archive_text_candidate_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'cook_recipe_archive_text_candidate_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_cook_recipe_archive_opaque_occurrence_immutable_insert_existing
BEFORE INSERT ON cook_recipe_archive_opaque_occurrence
WHEN EXISTS (SELECT 1 FROM cook_recipe_archive_opaque_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'cook_recipe_archive_opaque_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_cook_recipe_archive_asset_occurrence_immutable_insert_existing
BEFORE INSERT ON cook_recipe_archive_asset_occurrence
WHEN EXISTS (SELECT 1 FROM cook_recipe_archive_asset_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'cook_recipe_archive_asset_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_cook_bonus_archive_row_occurrence_immutable_insert_existing
BEFORE INSERT ON cook_bonus_archive_row_occurrence
WHEN EXISTS (SELECT 1 FROM cook_bonus_archive_row_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'cook_bonus_archive_row_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_cook_bonus_archive_raw_field_occurrence_immutable_insert_existing
BEFORE INSERT ON cook_bonus_archive_raw_field_occurrence
WHEN EXISTS (SELECT 1 FROM cook_bonus_archive_raw_field_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'cook_bonus_archive_raw_field_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_cook_bonus_archive_relationship_occurrence_immutable_insert_existing
BEFORE INSERT ON cook_bonus_archive_relationship_occurrence
WHEN EXISTS (SELECT 1 FROM cook_bonus_archive_relationship_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'cook_bonus_archive_relationship_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_cook_bonus_archive_relationship_candidate_occurrence_immutable_insert_existing
BEFORE INSERT ON cook_bonus_archive_relationship_candidate_occurrence
WHEN EXISTS (SELECT 1 FROM cook_bonus_archive_relationship_candidate_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'cook_bonus_archive_relationship_candidate_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_cook_bonus_archive_opaque_occurrence_immutable_insert_existing
BEFORE INSERT ON cook_bonus_archive_opaque_occurrence
WHEN EXISTS (SELECT 1 FROM cook_bonus_archive_opaque_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'cook_bonus_archive_opaque_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_homeworld_furniture_archive_row_occurrence_immutable_insert_existing
BEFORE INSERT ON homeworld_furniture_archive_row_occurrence
WHEN EXISTS (SELECT 1 FROM homeworld_furniture_archive_row_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'homeworld_furniture_archive_row_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_homeworld_furniture_archive_raw_field_occurrence_immutable_insert_existing
BEFORE INSERT ON homeworld_furniture_archive_raw_field_occurrence
WHEN EXISTS (SELECT 1 FROM homeworld_furniture_archive_raw_field_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'homeworld_furniture_archive_raw_field_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_homeworld_furniture_archive_relationship_occurrence_immutable_insert_existing
BEFORE INSERT ON homeworld_furniture_archive_relationship_occurrence
WHEN EXISTS (SELECT 1 FROM homeworld_furniture_archive_relationship_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'homeworld_furniture_archive_relationship_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_homeworld_furniture_archive_relationship_candidate_occurrence_immutable_insert_existing
BEFORE INSERT ON homeworld_furniture_archive_relationship_candidate_occurrence
WHEN EXISTS (SELECT 1 FROM homeworld_furniture_archive_relationship_candidate_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'homeworld_furniture_archive_relationship_candidate_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_homeworld_furniture_archive_text_owner_occurrence_immutable_insert_existing
BEFORE INSERT ON homeworld_furniture_archive_text_owner_occurrence
WHEN EXISTS (SELECT 1 FROM homeworld_furniture_archive_text_owner_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'homeworld_furniture_archive_text_owner_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_homeworld_furniture_archive_text_resolution_occurrence_immutable_insert_existing
BEFORE INSERT ON homeworld_furniture_archive_text_resolution_occurrence
WHEN EXISTS (SELECT 1 FROM homeworld_furniture_archive_text_resolution_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'homeworld_furniture_archive_text_resolution_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_homeworld_furniture_archive_text_candidate_occurrence_immutable_insert_existing
BEFORE INSERT ON homeworld_furniture_archive_text_candidate_occurrence
WHEN EXISTS (SELECT 1 FROM homeworld_furniture_archive_text_candidate_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'homeworld_furniture_archive_text_candidate_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_homeworld_furniture_archive_opaque_occurrence_immutable_insert_existing
BEFORE INSERT ON homeworld_furniture_archive_opaque_occurrence
WHEN EXISTS (SELECT 1 FROM homeworld_furniture_archive_opaque_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'homeworld_furniture_archive_opaque_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_homeworld_furniture_archive_asset_occurrence_immutable_insert_existing
BEFORE INSERT ON homeworld_furniture_archive_asset_occurrence
WHEN EXISTS (SELECT 1 FROM homeworld_furniture_archive_asset_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'homeworld_furniture_archive_asset_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_furniture_make_archive_row_occurrence_immutable_insert_existing
BEFORE INSERT ON furniture_make_archive_row_occurrence
WHEN EXISTS (SELECT 1 FROM furniture_make_archive_row_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'furniture_make_archive_row_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_furniture_make_archive_raw_field_occurrence_immutable_insert_existing
BEFORE INSERT ON furniture_make_archive_raw_field_occurrence
WHEN EXISTS (SELECT 1 FROM furniture_make_archive_raw_field_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'furniture_make_archive_raw_field_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_furniture_make_archive_relationship_occurrence_immutable_insert_existing
BEFORE INSERT ON furniture_make_archive_relationship_occurrence
WHEN EXISTS (SELECT 1 FROM furniture_make_archive_relationship_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'furniture_make_archive_relationship_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_furniture_make_archive_relationship_candidate_occurrence_immutable_insert_existing
BEFORE INSERT ON furniture_make_archive_relationship_candidate_occurrence
WHEN EXISTS (SELECT 1 FROM furniture_make_archive_relationship_candidate_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'furniture_make_archive_relationship_candidate_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_furniture_make_archive_opaque_occurrence_immutable_insert_existing
BEFORE INSERT ON furniture_make_archive_opaque_occurrence
WHEN EXISTS (SELECT 1 FROM furniture_make_archive_opaque_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'furniture_make_archive_opaque_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_furniture_suite_archive_row_occurrence_immutable_insert_existing
BEFORE INSERT ON furniture_suite_archive_row_occurrence
WHEN EXISTS (SELECT 1 FROM furniture_suite_archive_row_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'furniture_suite_archive_row_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_furniture_suite_archive_raw_field_occurrence_immutable_insert_existing
BEFORE INSERT ON furniture_suite_archive_raw_field_occurrence
WHEN EXISTS (SELECT 1 FROM furniture_suite_archive_raw_field_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'furniture_suite_archive_raw_field_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_furniture_suite_archive_relationship_occurrence_immutable_insert_existing
BEFORE INSERT ON furniture_suite_archive_relationship_occurrence
WHEN EXISTS (SELECT 1 FROM furniture_suite_archive_relationship_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'furniture_suite_archive_relationship_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_furniture_suite_archive_relationship_candidate_occurrence_immutable_insert_existing
BEFORE INSERT ON furniture_suite_archive_relationship_candidate_occurrence
WHEN EXISTS (SELECT 1 FROM furniture_suite_archive_relationship_candidate_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'furniture_suite_archive_relationship_candidate_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_furniture_suite_archive_text_owner_occurrence_immutable_insert_existing
BEFORE INSERT ON furniture_suite_archive_text_owner_occurrence
WHEN EXISTS (SELECT 1 FROM furniture_suite_archive_text_owner_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'furniture_suite_archive_text_owner_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_furniture_suite_archive_text_resolution_occurrence_immutable_insert_existing
BEFORE INSERT ON furniture_suite_archive_text_resolution_occurrence
WHEN EXISTS (SELECT 1 FROM furniture_suite_archive_text_resolution_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'furniture_suite_archive_text_resolution_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_furniture_suite_archive_text_candidate_occurrence_immutable_insert_existing
BEFORE INSERT ON furniture_suite_archive_text_candidate_occurrence
WHEN EXISTS (SELECT 1 FROM furniture_suite_archive_text_candidate_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'furniture_suite_archive_text_candidate_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_furniture_suite_archive_opaque_occurrence_immutable_insert_existing
BEFORE INSERT ON furniture_suite_archive_opaque_occurrence
WHEN EXISTS (SELECT 1 FROM furniture_suite_archive_opaque_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'furniture_suite_archive_opaque_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_furniture_suite_archive_asset_occurrence_immutable_insert_existing
BEFORE INSERT ON furniture_suite_archive_asset_occurrence
WHEN EXISTS (SELECT 1 FROM furniture_suite_archive_asset_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'furniture_suite_archive_asset_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_homeworld_furniture_type_archive_row_occurrence_immutable_insert_existing
BEFORE INSERT ON homeworld_furniture_type_archive_row_occurrence
WHEN EXISTS (SELECT 1 FROM homeworld_furniture_type_archive_row_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'homeworld_furniture_type_archive_row_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_homeworld_furniture_type_archive_raw_field_occurrence_immutable_insert_existing
BEFORE INSERT ON homeworld_furniture_type_archive_raw_field_occurrence
WHEN EXISTS (SELECT 1 FROM homeworld_furniture_type_archive_raw_field_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'homeworld_furniture_type_archive_raw_field_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_homeworld_furniture_type_archive_text_owner_occurrence_immutable_insert_existing
BEFORE INSERT ON homeworld_furniture_type_archive_text_owner_occurrence
WHEN EXISTS (SELECT 1 FROM homeworld_furniture_type_archive_text_owner_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'homeworld_furniture_type_archive_text_owner_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_homeworld_furniture_type_archive_text_resolution_occurrence_immutable_insert_existing
BEFORE INSERT ON homeworld_furniture_type_archive_text_resolution_occurrence
WHEN EXISTS (SELECT 1 FROM homeworld_furniture_type_archive_text_resolution_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'homeworld_furniture_type_archive_text_resolution_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_homeworld_furniture_type_archive_text_candidate_occurrence_immutable_insert_existing
BEFORE INSERT ON homeworld_furniture_type_archive_text_candidate_occurrence
WHEN EXISTS (SELECT 1 FROM homeworld_furniture_type_archive_text_candidate_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'homeworld_furniture_type_archive_text_candidate_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_homeworld_furniture_type_archive_opaque_occurrence_immutable_insert_existing
BEFORE INSERT ON homeworld_furniture_type_archive_opaque_occurrence
WHEN EXISTS (SELECT 1 FROM homeworld_furniture_type_archive_opaque_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'homeworld_furniture_type_archive_opaque_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_homeworld_furniture_type_archive_asset_occurrence_immutable_insert_existing
BEFORE INSERT ON homeworld_furniture_type_archive_asset_occurrence
WHEN EXISTS (SELECT 1 FROM homeworld_furniture_type_archive_asset_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'homeworld_furniture_type_archive_asset_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_homeworld_bgm_archive_row_occurrence_immutable_insert_existing
BEFORE INSERT ON homeworld_bgm_archive_row_occurrence
WHEN EXISTS (SELECT 1 FROM homeworld_bgm_archive_row_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'homeworld_bgm_archive_row_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_homeworld_bgm_archive_raw_field_occurrence_immutable_insert_existing
BEFORE INSERT ON homeworld_bgm_archive_raw_field_occurrence
WHEN EXISTS (SELECT 1 FROM homeworld_bgm_archive_raw_field_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'homeworld_bgm_archive_raw_field_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_homeworld_bgm_archive_text_owner_occurrence_immutable_insert_existing
BEFORE INSERT ON homeworld_bgm_archive_text_owner_occurrence
WHEN EXISTS (SELECT 1 FROM homeworld_bgm_archive_text_owner_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'homeworld_bgm_archive_text_owner_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_homeworld_bgm_archive_text_resolution_occurrence_immutable_insert_existing
BEFORE INSERT ON homeworld_bgm_archive_text_resolution_occurrence
WHEN EXISTS (SELECT 1 FROM homeworld_bgm_archive_text_resolution_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'homeworld_bgm_archive_text_resolution_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_homeworld_bgm_archive_text_candidate_occurrence_immutable_insert_existing
BEFORE INSERT ON homeworld_bgm_archive_text_candidate_occurrence
WHEN EXISTS (SELECT 1 FROM homeworld_bgm_archive_text_candidate_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'homeworld_bgm_archive_text_candidate_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_homeworld_bgm_archive_opaque_occurrence_immutable_insert_existing
BEFORE INSERT ON homeworld_bgm_archive_opaque_occurrence
WHEN EXISTS (SELECT 1 FROM homeworld_bgm_archive_opaque_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'homeworld_bgm_archive_opaque_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_homeworld_bgm_archive_asset_occurrence_immutable_insert_existing
BEFORE INSERT ON homeworld_bgm_archive_asset_occurrence
WHEN EXISTS (SELECT 1 FROM homeworld_bgm_archive_asset_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'homeworld_bgm_archive_asset_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_profile_picture_archive_row_occurrence_immutable_insert_existing
BEFORE INSERT ON profile_picture_archive_row_occurrence
WHEN EXISTS (SELECT 1 FROM profile_picture_archive_row_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'profile_picture_archive_row_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_profile_picture_archive_raw_field_occurrence_immutable_insert_existing
BEFORE INSERT ON profile_picture_archive_raw_field_occurrence
WHEN EXISTS (SELECT 1 FROM profile_picture_archive_raw_field_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'profile_picture_archive_raw_field_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_profile_picture_archive_relationship_occurrence_immutable_insert_existing
BEFORE INSERT ON profile_picture_archive_relationship_occurrence
WHEN EXISTS (SELECT 1 FROM profile_picture_archive_relationship_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'profile_picture_archive_relationship_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_profile_picture_archive_relationship_candidate_occurrence_immutable_insert_existing
BEFORE INSERT ON profile_picture_archive_relationship_candidate_occurrence
WHEN EXISTS (SELECT 1 FROM profile_picture_archive_relationship_candidate_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'profile_picture_archive_relationship_candidate_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_profile_picture_archive_text_owner_occurrence_immutable_insert_existing
BEFORE INSERT ON profile_picture_archive_text_owner_occurrence
WHEN EXISTS (SELECT 1 FROM profile_picture_archive_text_owner_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'profile_picture_archive_text_owner_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_profile_picture_archive_text_resolution_occurrence_immutable_insert_existing
BEFORE INSERT ON profile_picture_archive_text_resolution_occurrence
WHEN EXISTS (SELECT 1 FROM profile_picture_archive_text_resolution_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'profile_picture_archive_text_resolution_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_profile_picture_archive_text_candidate_occurrence_immutable_insert_existing
BEFORE INSERT ON profile_picture_archive_text_candidate_occurrence
WHEN EXISTS (SELECT 1 FROM profile_picture_archive_text_candidate_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'profile_picture_archive_text_candidate_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_profile_picture_archive_opaque_occurrence_immutable_insert_existing
BEFORE INSERT ON profile_picture_archive_opaque_occurrence
WHEN EXISTS (SELECT 1 FROM profile_picture_archive_opaque_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'profile_picture_archive_opaque_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_profile_picture_archive_asset_occurrence_immutable_insert_existing
BEFORE INSERT ON profile_picture_archive_asset_occurrence
WHEN EXISTS (SELECT 1 FROM profile_picture_archive_asset_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'profile_picture_archive_asset_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_profile_frame_archive_row_occurrence_immutable_insert_existing
BEFORE INSERT ON profile_frame_archive_row_occurrence
WHEN EXISTS (SELECT 1 FROM profile_frame_archive_row_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'profile_frame_archive_row_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_profile_frame_archive_raw_field_occurrence_immutable_insert_existing
BEFORE INSERT ON profile_frame_archive_raw_field_occurrence
WHEN EXISTS (SELECT 1 FROM profile_frame_archive_raw_field_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'profile_frame_archive_raw_field_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_profile_frame_archive_text_owner_occurrence_immutable_insert_existing
BEFORE INSERT ON profile_frame_archive_text_owner_occurrence
WHEN EXISTS (SELECT 1 FROM profile_frame_archive_text_owner_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'profile_frame_archive_text_owner_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_profile_frame_archive_text_resolution_occurrence_immutable_insert_existing
BEFORE INSERT ON profile_frame_archive_text_resolution_occurrence
WHEN EXISTS (SELECT 1 FROM profile_frame_archive_text_resolution_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'profile_frame_archive_text_resolution_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_profile_frame_archive_text_candidate_occurrence_immutable_insert_existing
BEFORE INSERT ON profile_frame_archive_text_candidate_occurrence
WHEN EXISTS (SELECT 1 FROM profile_frame_archive_text_candidate_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'profile_frame_archive_text_candidate_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_profile_frame_archive_opaque_occurrence_immutable_insert_existing
BEFORE INSERT ON profile_frame_archive_opaque_occurrence
WHEN EXISTS (SELECT 1 FROM profile_frame_archive_opaque_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'profile_frame_archive_opaque_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_profile_frame_archive_asset_occurrence_immutable_insert_existing
BEFORE INSERT ON profile_frame_archive_asset_occurrence
WHEN EXISTS (SELECT 1 FROM profile_frame_archive_asset_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'profile_frame_archive_asset_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_gadget_title_archive_row_occurrence_immutable_insert_existing
BEFORE INSERT ON gadget_title_archive_row_occurrence
WHEN EXISTS (SELECT 1 FROM gadget_title_archive_row_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'gadget_title_archive_row_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_gadget_title_archive_raw_field_occurrence_immutable_insert_existing
BEFORE INSERT ON gadget_title_archive_raw_field_occurrence
WHEN EXISTS (SELECT 1 FROM gadget_title_archive_raw_field_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'gadget_title_archive_raw_field_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_gadget_title_archive_relationship_occurrence_immutable_insert_existing
BEFORE INSERT ON gadget_title_archive_relationship_occurrence
WHEN EXISTS (SELECT 1 FROM gadget_title_archive_relationship_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'gadget_title_archive_relationship_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_gadget_title_archive_relationship_candidate_occurrence_immutable_insert_existing
BEFORE INSERT ON gadget_title_archive_relationship_candidate_occurrence
WHEN EXISTS (SELECT 1 FROM gadget_title_archive_relationship_candidate_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'gadget_title_archive_relationship_candidate_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_gadget_title_archive_text_owner_occurrence_immutable_insert_existing
BEFORE INSERT ON gadget_title_archive_text_owner_occurrence
WHEN EXISTS (SELECT 1 FROM gadget_title_archive_text_owner_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'gadget_title_archive_text_owner_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_gadget_title_archive_text_resolution_occurrence_immutable_insert_existing
BEFORE INSERT ON gadget_title_archive_text_resolution_occurrence
WHEN EXISTS (SELECT 1 FROM gadget_title_archive_text_resolution_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'gadget_title_archive_text_resolution_occurrence evidence is immutable');
END;

CREATE TRIGGER trg_gadget_title_archive_text_candidate_occurrence_immutable_insert_existing
BEFORE INSERT ON gadget_title_archive_text_candidate_occurrence
WHEN EXISTS (SELECT 1 FROM gadget_title_archive_text_candidate_occurrence WHERE occurrence_id = NEW.occurrence_id)
BEGIN
    SELECT RAISE(ABORT, 'gadget_title_archive_text_candidate_occurrence evidence is immutable');
END;

