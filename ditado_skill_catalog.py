"""Versioned public skill library. User profiles own their copies and overrides."""
import copy
import json
from pathlib import Path

from ditado_harness import OUTPUT_MODES, fold


def validate_catalog(skills):
    if not isinstance(skills, list) or len(skills) > 30:
        raise ValueError("O catálogo deve conter uma lista de até 30 skills.")
    result, ids, names = [], set(), set()
    for raw in skills:
        if not isinstance(raw, dict):
            raise ValueError("Skill inválida no catálogo.")
        entry = copy.deepcopy(raw)
        for field, limit in (("id", 120), ("name", 80), ("description", 500), ("instructions", 4000)):
            value = entry.get(field)
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ValueError(f"Campo {field} inválido no catálogo.")
        for field, limit in (("triggers", 12), ("examples", 8)):
            values = entry.get(field)
            if (not isinstance(values, list) or len(values) > limit
                    or any(not isinstance(v, str) or not v.strip() for v in values)):
                raise ValueError(f"Campo {field} inválido no catálogo.")
        if entry.get("kind") not in {"primary", "modifier"}:
            raise ValueError("Tipo de skill inválido.")
        if entry.get("output_mode") not in OUTPUT_MODES:
            raise ValueError("Formato de skill inválido.")
        if "enabled" in entry and not isinstance(entry["enabled"], bool):
            raise ValueError("Estado da skill inválido.")
        if entry["id"] in ids or fold(entry["name"]) in names:
            raise ValueError("O catálogo contém IDs ou nomes duplicados.")
        ids.add(entry["id"])
        names.add(fold(entry["name"]))
        result.append(entry)
    return result


def load_builtin_skills():
    payload = json.loads(Path(__file__).with_name("builtin_skills.json").read_text(encoding="utf-8"))
    if payload.get("version") != 1:
        raise ValueError("Versão da biblioteca de skills não suportada.")
    return validate_catalog(payload.get("skills"))


def merge_skills(existing, incoming, now, *, replace_ids=(), only_missing=False):
    """Build the entire result before writing; never overwrite in library mode."""
    incoming = validate_catalog(incoming)
    replace_ids = set(replace_ids)
    current_ids = {s["id"] for s in existing}
    if not replace_ids <= current_ids:
        raise ValueError("O perfil mudou; confira as skills antes de substituir.")
    result = [copy.deepcopy(s) for s in existing if s["id"] not in replace_ids]
    original = {s["id"]: s for s in existing}
    changed = 0
    for entry in incoming:
        matches = [s for s in result if s["id"] == entry["id"] or fold(s["name"]) == fold(entry["name"])]
        if matches:
            if only_missing:
                continue
            raise ValueError("Skill existente fora da substituição autorizada.")
        entry = {k: v for k, v in entry.items() if k != "updated_at"}
        entry.setdefault("enabled", True)
        old = original.get(entry["id"])
        if old and {k: v for k, v in old.items() if k != "updated_at"} == entry:
            entry["updated_at"] = old["updated_at"]
        else:
            entry["updated_at"] = now
            changed += 1
        result.append(entry)
    validate_catalog(result)
    return result, changed
