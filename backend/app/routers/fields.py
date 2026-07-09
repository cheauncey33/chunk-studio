"""Field config CRUD — the metadata schema definition."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from .. import db
from ..models import FieldConfig

router = APIRouter(prefix="/fields", tags=["fields"])


@router.get("")
def list_fields():
    return db.get_field_configs()


@router.put("/{field_key}")
def upsert_field(field_key: str, body: FieldConfig):
    if body.field_key != field_key:
        raise HTTPException(400, "field_key mismatch")
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO field_config
               (field_key, display_name, extract_source, value_constraint,
                label_list, value_type, llm_description, order_index,
                storage_path, accepted_storage_path, scope, editable, filterable,
                indexable, visible)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(field_key) DO UPDATE SET
                 display_name=excluded.display_name,
                 extract_source=excluded.extract_source,
                 value_constraint=excluded.value_constraint,
                 label_list=excluded.label_list,
                 value_type=excluded.value_type,
                 llm_description=excluded.llm_description,
                 order_index=excluded.order_index,
                 storage_path=excluded.storage_path,
                 accepted_storage_path=excluded.accepted_storage_path,
                 scope=excluded.scope,
                 editable=excluded.editable,
                 filterable=excluded.filterable,
                 indexable=excluded.indexable,
                 visible=excluded.visible""",
            (
                body.field_key, body.display_name, body.extract_source,
                body.value_constraint, json.dumps(body.label_list, ensure_ascii=False),
                body.value_type, body.llm_description, body.order_index,
                body.storage_path, body.accepted_storage_path, body.scope,
                int(body.editable), int(body.filterable), int(body.indexable),
                int(body.visible),
            ),
        )
    return db.get_field_configs()


@router.delete("/{field_key}")
def delete_field(field_key: str):
    with db.transaction() as conn:
        cur = conn.execute("DELETE FROM field_config WHERE field_key=?", (field_key,))
        if cur.rowcount == 0:
            raise HTTPException(404, "field not found")
    return {"ok": True}
