import os
import shutil
import pandas as pd


AUDIT_USER_ID = 1

def _by_listing(frame, key):
    if frame is None or len(frame) == 0 or "source_listing_idx" not in frame.columns:
        return {}
    out = {}
    for _, r in frame.iterrows():
        out.setdefault(int(r["source_listing_idx"]), []).append(r[key])
    return out

def _resolve_int_fk(lookup, internal_id):
    if internal_id is None or (isinstance(internal_id, float) and pd.isna(internal_id)):
        return None
    try:
        return lookup.get(internal_id) or lookup.get(int(internal_id))
    except (TypeError, ValueError):
        return None

def _src_row_by(frame, key, internal_id):
    if frame is None or len(frame) == 0 or key not in frame.columns:
        return None
    sel = frame[frame[key] == internal_id]
    if len(sel) == 0:
        return None
    return sel.iloc[0]

AUDIT_TAIL = ["created_date", "modified_date", "created_by", "modified_by"]

INT_COLS = {
    "colors": ["created_by", "modified_by"],
    "letterings": ["created_by", "modified_by"],
    "shapes": ["created_by", "modified_by"],
    "post_offices": ["created_by", "modified_by"],
    "post_office_regions": ["created_by", "modified_by"],
    "markings": ["created_by", "modified_by"],
    "dates_seen": ["created_by", "modified_by"],
    "citations": ["created_by", "modified_by"],
}

def _cast_int_columns(frame, int_cols):
    """Cast each named column to pandas nullable Int64 in-place on a copy."""
    out = frame.copy()
    for c in int_cols:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce").astype("Int64")
    return out
