"""Workflow lead schemas and the disposition catalogue (configuration, not data)."""
from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .normalize import normalise_header

DEFAULT_SCHEMAS_DIR = Path(
    os.environ.get("LEDGER_SCHEMAS_DIR")
    or Path(__file__).resolve().parents[2] / "assignment-a-data" / "schemas"
)

FIELD_TYPES = {"string", "phone", "date", "decimal", "enum"}


@dataclass(frozen=True)
class Field:
    name: str
    type: str
    required: bool = False
    pii: bool = False
    reference: bool = False
    aliases: tuple[str, ...] = ()
    values: tuple[str, ...] = ()
    default: str | None = None


@dataclass
class WorkflowSchema:
    workflow_id: str
    fields: list[Field]
    header_map: dict[str, str] = field(default_factory=dict)  # normalised header -> field name

    def __post_init__(self) -> None:
        phones = [f for f in self.fields if f.type == "phone"]
        refs = [f for f in self.fields if f.reference]
        if len(phones) != 1:
            raise ValueError(f"{self.workflow_id}: exactly one phone field required (lead identity)")
        if len(refs) != 1:
            raise ValueError(f"{self.workflow_id}: exactly one reference field required")
        if refs[0].pii:
            raise ValueError(f"{self.workflow_id}: reference field must not be PII "
                             "(it is stored in plaintext for sorting/reporting)")
        for f in self.fields:
            if f.type not in FIELD_TYPES:
                raise ValueError(f"{self.workflow_id}.{f.name}: unknown type {f.type!r}")
            for key in (f.name, *f.aliases):
                n = normalise_header(key)
                other = self.header_map.setdefault(n, f.name)
                if other != f.name:
                    raise ValueError(f"{self.workflow_id}: header {key!r} is ambiguous "
                                     f"({other} vs {f.name})")

    @property
    def phone_field(self) -> Field:
        return next(f for f in self.fields if f.type == "phone")

    @property
    def reference_field(self) -> Field:
        return next(f for f in self.fields if f.reference)


@dataclass(frozen=True)
class Disposition:
    code: str
    is_terminal: bool
    is_success: bool


def _bool(s: str) -> bool:
    v = s.strip().lower()
    if v not in ("true", "false"):
        raise ValueError(f"bad boolean {s!r} in dispositions.csv")
    return v == "true"


@dataclass
class Config:
    schemas: dict[str, WorkflowSchema]
    dispositions: dict[str, dict[str, Disposition]]  # workflow -> code -> Disposition

    @classmethod
    def load(cls, schemas_dir: Path | str = DEFAULT_SCHEMAS_DIR) -> "Config":
        d = Path(schemas_dir)
        schemas: dict[str, WorkflowSchema] = {}
        for p in sorted(d.glob("*.json")):
            raw = json.loads(p.read_text(encoding="utf-8"))
            fields = [
                Field(
                    name=f["name"], type=f["type"], required=bool(f.get("required", False)),
                    pii=bool(f.get("pii", False)), reference=bool(f.get("reference", False)),
                    aliases=tuple(f.get("aliases", [])), values=tuple(f.get("values", [])),
                    default=f.get("default"),
                )
                for f in raw["fields"]
            ]
            schemas[raw["workflow_id"]] = WorkflowSchema(raw["workflow_id"], fields)
        dispositions: dict[str, dict[str, Disposition]] = {}
        with open(d / "dispositions.csv", newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                code = row["code"].strip()
                dispositions.setdefault(row["workflow_id"].strip(), {})[code] = Disposition(
                    code, _bool(row["is_terminal"]), _bool(row["is_success"]))
        if not schemas:
            raise FileNotFoundError(f"no workflow schemas found in {d}")
        return cls(schemas, dispositions)
