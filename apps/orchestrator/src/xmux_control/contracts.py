import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[4] / 'packages/contracts'
SCHEMAS = {p.stem.removesuffix('.schema'): json.loads(p.read_text())
           for p in (ROOT / 'schemas/v1').glob('*.json')}
REGISTRY = Registry().with_resources((s['$id'], Resource.from_contents(s)) for s in SCHEMAS.values())


def validate(name, value):
    Draft202012Validator(SCHEMAS[name], registry=REGISTRY, format_checker=FormatChecker()).validate(value)
