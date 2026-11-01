#!/usr/bin/env python3
"""The atlax contract test: every sample holds to its schema.

Run: python3 contracts/atlax/test.py   (from the specs repo; exit 0 = pass)

Reads case.json. Fails, never skips (contracts/README.md rule 3), when:
  - a sample or schema case.json lists is missing, or a *.sample.* / *.schema.json
    file here is not listed (so a new sample cannot go untested);
  - a sample does not hold to its schema, or a must_refuse case is accepted;
  - a published element's file name is not its designation (':' written '.');
  - a published copy differs from the sample it must equal;
  - the published folder lacks PUBLISHED, plant.yaml, elements/ or derived/map.json;
  - platform/artifacts.yaml's sample lists a slug twice;
  - a claims-state fact's body does not hold to its kind's schema (claim, claim-release) or names
    another card or artifact than its file.

Needs python3 and PyYAML only. The validator below covers the JSON Schema
keywords these schemas use and refuses any other, so a schema can never pass
on a keyword nobody checked.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

try:
    import yaml
except ImportError:
    print("FAIL: PyYAML is required (pip install pyyaml); the YAML samples cannot be read")
    sys.exit(1)


class _StrDates(yaml.SafeLoader):
    """A YAML loader that keeps timestamps as the strings they were written as."""


_StrDates.yaml_implicit_resolvers = {
    k: [(tag, rx) for tag, rx in v if tag != "tag:yaml.org,2002:timestamp"]
    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
}

ANNOTATIONS = {"$schema", "$id", "title", "description", "$comment", "$defs", "examples", "default"}
CHECKED = {"type", "const", "enum", "pattern", "required", "properties", "additionalProperties",
           "items", "minItems", "minLength", "uniqueItems", "$ref", "allOf", "if", "then", "else"}
TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "null": lambda v: v is None,
}


def _eq(a, b):
    # JSON equality: true is not 1.
    return type(a) is type(b) and a == b if isinstance(a, bool) or isinstance(b, bool) else a == b


def validate(v, s, root, path, errs):
    if s is True:
        return
    if s is False:
        errs.append(f"{path}: not allowed")
        return
    unknown = set(s) - ANNOTATIONS - CHECKED
    if unknown:
        errs.append(f"{path}: schema keyword(s) this test does not check: {sorted(unknown)}")
        return
    if "$ref" in s:
        ref = s["$ref"]
        if not ref.startswith("#/$defs/"):
            errs.append(f"{path}: only local #/$defs refs are supported, got {ref}")
            return
        validate(v, root["$defs"][ref[len("#/$defs/"):]], root, path, errs)
    if "type" in s:
        ts = s["type"] if isinstance(s["type"], list) else [s["type"]]
        if not any(TYPES[t](v) for t in ts):
            errs.append(f"{path}: {v!r} is not {s['type']}")
            return
    if "const" in s and not _eq(v, s["const"]):
        errs.append(f"{path}: {v!r} is not {s['const']!r}")
    if "enum" in s and not any(_eq(v, e) for e in s["enum"]):
        errs.append(f"{path}: {v!r} not in {s['enum']}")
    if isinstance(v, str):
        if "pattern" in s and not re.search(s["pattern"], v):
            errs.append(f"{path}: {v!r} does not match {s['pattern']}")
        if "minLength" in s and len(v) < s["minLength"]:
            errs.append(f"{path}: shorter than {s['minLength']}")
    if isinstance(v, list):
        if "minItems" in s and len(v) < s["minItems"]:
            errs.append(f"{path}: fewer than {s['minItems']} items")
        if s.get("uniqueItems") and len({json.dumps(x, sort_keys=True) for x in v}) != len(v):
            errs.append(f"{path}: items not unique")
        if "items" in s:
            for i, x in enumerate(v):
                validate(x, s["items"], root, f"{path}[{i}]", errs)
    if isinstance(v, dict):
        for k in s.get("required", []):
            if k not in v:
                errs.append(f"{path}: missing {k}")
        props = s.get("properties", {})
        for k, x in v.items():
            if k in props:
                validate(x, props[k], root, f"{path}.{k}", errs)
            elif "additionalProperties" in s:
                validate(x, s["additionalProperties"], root, f"{path}.{k}", errs)
    for sub in s.get("allOf", []):
        validate(v, sub, root, path, errs)
    if "if" in s:
        probe = []
        validate(v, s["if"], root, path, probe)
        branch = s.get("then") if not probe else s.get("else")
        if branch is not None:
            validate(v, branch, root, path, errs)


def load(path):
    with open(path) as f:
        if path.endswith(".json"):
            return json.load(f)
        return yaml.load(f, Loader=_StrDates)


def unique_slugs(rows):
    slugs = [r.get("slug") for r in rows]
    return len(slugs) == len(set(slugs))


def main():
    fails, passes = [], 0

    def check(ok, what):
        nonlocal passes
        if ok:
            passes += 1
            print(f"ok   {what}")
        else:
            fails.append(what)
            print(f"FAIL {what}")

    case_path = os.path.join(HERE, "case.json")
    if not os.path.isfile(case_path):
        print(f"FAIL contract sample missing: {case_path}")
        return 1
    case = json.load(open(case_path))
    schemas = {}

    def schema(name):
        if name not in schemas:
            schemas[name] = load(os.path.join(HERE, name))
        return schemas[name]

    listed_samples = {c["sample"] for c in case["samples"]}
    listed_schemas = {c["schema"] for c in case["samples"]}
    for c in case["samples"]:
        for f in (c["sample"], c["schema"]):
            if not os.path.isfile(os.path.join(HERE, f)):
                check(False, f"present: {f}")
                break
        else:
            errs = []
            s = schema(c["schema"])
            validate(load(os.path.join(HERE, c["sample"])), s, s, c["sample"], errs)
            check(not errs, f"{c['sample']} holds to {c['schema']}" + "".join(f"\n       {e}" for e in errs))
            if "equals" in c:
                same = open(os.path.join(HERE, c["sample"]), "rb").read() == open(os.path.join(HERE, c["equals"]), "rb").read()
                check(same, f"{c['sample']} is a byte copy of {c['equals']}")

    # Nothing here goes unlisted: a new sample or schema must be wired into case.json.
    for dirpath, _, files in sorted(os.walk(HERE)):
        for f in sorted(files):
            rel = os.path.relpath(os.path.join(dirpath, f), HERE)
            if ".sample." in f or rel.startswith("published" + os.sep):
                check(rel in listed_samples, f"listed in case.json: {rel}")
            elif f.endswith(".schema.json"):
                check(rel in listed_schemas, f"used by a sample: {rel}")

    pub = os.path.join(HERE, case["published_folder"]["path"])
    for r in case["published_folder"]["required"]:
        check(os.path.exists(os.path.join(pub, r)), f"published folder has {r}")
    edir = os.path.join(pub, "elements")
    names = sorted(os.listdir(edir)) if os.path.isdir(edir) else []
    check(bool(names), "published elements/ is not empty")
    for n in names:
        d = (load(os.path.join(edir, n)) or {}).get("designation", "")
        check(n == d.replace(":", ".") + ".yaml", f"elements/{n} is named by its designation {d!r}")

    # platform/artifacts.yaml: a slug once (the watcher stops on a repeat; JSON Schema cannot say it).
    arts = load(os.path.join(HERE, "artifacts.sample.yaml")).get("artifacts") or []
    check(unique_slugs(arts), "artifacts.sample.yaml lists each slug once")
    check(not unique_slugs([{"slug": "a", "remote": "x"}, {"slug": "a", "remote": "y"}]),
          "a slug listed twice is refused")

    # The claims file: each fact's body holds to the schema of its kind, and names the file's card + artifact.
    st = load(os.path.join(HERE, "claims-state.sample.json"))
    by_kind = {"atlax_claim": "claim.schema.json", "atlax_claim_released": "claim-release.schema.json"}
    kinds = [f.get("kind") for f in st.get("facts", [])]
    check(set(kinds) == set(by_kind), "claims-state.sample.json carries both kinds")
    for i, f in enumerate(st.get("facts", [])):
        errs = []
        s = schema(by_kind[f["kind"]])
        validate(f.get("body"), s, s, f"facts[{i}].body", errs)
        check(not errs, f"claims-state facts[{i}] ({f['kind']}) body holds to {by_kind[f['kind']]}"
              + "".join(f"\n       {e}" for e in errs))
        b = f.get("body") or {}
        check(b.get("card") == st["card"] and b.get("artifact") == st["artifact"],
              f"claims-state facts[{i}] names the file's card and artifact")
    for i, f in enumerate(st.get("facts", [])):
        if f["kind"] == "atlax_claim_released":
            check(f["at"] == f["body"]["released_at"], f"claims-state facts[{i}] at is its released_at")
    bad = {"kind": "atlax_claim", "v": 1, "at": "2026-11-02T14:05:00Z",
           "body": {"card": "agent-slack/x", "factory": "lodestar", "artifact": "agent-mailbox-api",
                    "designations": [{"designation": "fn:mailbox.threads.list"}]}}
    errs = []
    validate(bad["body"], schema("claim.schema.json"), schema("claim.schema.json"), "body", errs)
    check(bool(errs), "a claims-state body carrying element content is refused by claim.schema.json")

    for i, m in enumerate(case["must_refuse"]):
        errs = []
        s = schema(m["schema"])
        validate(m["case"], s, s, f"must_refuse[{i}]", errs)
        unchecked = [e for e in errs if "does not check" in e or "only local" in e]
        check(errs and not unchecked, f"must_refuse[{i}] refused by {m['schema']}: {m['reason']}")

    print(f"\n{passes} passed, {len(fails)} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
