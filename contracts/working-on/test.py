#!/usr/bin/env python3
"""The working-on contract test: the sample holds to its schemas and reads as expected.json says.

Run: python3 contracts/working-on/test.py   (from the specs repo; exit 0 = pass)

Fails, never skips (contracts/README.md rule 3), when:
  - expected.json, a schema or a sample file it names is missing;
  - a decision, card, run record or spec in sample/ (other than one expected.json lists as unread)
    does not hold to its kind's schema, or a facts.must_refuse case is accepted, or a
    facts.reader_refuses case is refused by the schema (the reader refuses it, not the schema);
  - a decision's `crossing:` does not read as expected.json says (accepted_reading.crossing: the
    scalar as written, a whole number in decimal; `0019` is 19, `0012` is 12, never the octal 10);
  - a run record's findings or a spec's amendments differ from facts.finding and facts.amendment.

Needs python3 and PyYAML only. The validator covers the JSON Schema keywords these schemas use
and refuses any other, so a schema can never pass on a keyword nobody checked.
"""
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "sample")

try:
    import yaml
except ImportError:
    print("FAIL: PyYAML is required (pip install pyyaml); the sample cannot be read")
    sys.exit(1)


class _StrDates(yaml.SafeLoader):
    """A YAML loader that keeps timestamps as the strings they were written as."""


_StrDates.yaml_implicit_resolvers = {
    k: [(tag, rx) for tag, rx in v if tag != "tag:yaml.org,2002:timestamp"]
    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
}

ANNOTATIONS = {"$schema", "$id", "title", "description", "$comment", "$defs", "examples", "default"}
CHECKED = {"type", "const", "enum", "pattern", "required", "properties", "additionalProperties",
           "items", "minimum", "$ref", "oneOf", "anyOf"}
TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "null": lambda v: v is None,
}
SCHEMAS = {"decision": "decision.schema.json", "card": "card.schema.json",
           "run record": "run-record.schema.json", "spec": "spec.schema.json"}


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
    if isinstance(v, str) and "pattern" in s and not re.search(s["pattern"], v):
        errs.append(f"{path}: {v!r} does not match {s['pattern']}")
    if TYPES["number"](v) and "minimum" in s and v < s["minimum"]:
        errs.append(f"{path}: {v!r} below {s['minimum']}")
    if isinstance(v, list) and "items" in s:
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
            elif isinstance(s.get("additionalProperties"), (dict, bool)):
                validate(x, s["additionalProperties"], root, f"{path}.{k}", errs)
    for key, want in (("oneOf", lambda n: n == 1), ("anyOf", lambda n: n >= 1)):
        if key in s:
            n = 0
            for sub in s[key]:
                probe = []
                validate(v, sub, root, path, probe)
                if any("does not check" in e or "only local" in e for e in probe):
                    errs.extend(probe)
                    return
                n += not probe
            if not want(n):
                errs.append(f"{path}: {v!r} matches {n} of {key}")


def frontmatter_text(path):
    """The text between the opening and closing `---`, or None when there is no front-matter."""
    text = open(path).read()
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    return text[4:end + 1] if end != -1 else None


def frontmatter(path):
    fm = frontmatter_text(path)
    return {} if fm is None else (yaml.load(fm, Loader=_StrDates) or {})


def read_crossing(path):
    """accepted_reading.crossing: the scalar as written, digits read as a decimal whole number."""
    fm = frontmatter_text(path)
    if fm is None:
        return None
    node = yaml.compose(fm)
    for k, v in node.value:
        if k.value == "crossing":
            raw = v.value.strip() if isinstance(v, yaml.ScalarNode) else None
            if not raw or raw in ("~", "null"):
                return None
            if not re.fullmatch(r"[0-9]+", raw):
                raise ValueError(f"crossing {raw!r} is not a number")
            return int(raw, 10)
    return None


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

    exp_path = os.path.join(HERE, "expected.json")
    for p in [exp_path] + [os.path.join(HERE, "schemas", f) for f in SCHEMAS.values()]:
        if not os.path.isfile(p):
            print(f"FAIL contract sample missing: {p}")
            return 1
    exp = json.load(open(exp_path))
    schemas = {k: json.load(open(os.path.join(HERE, "schemas", f))) for k, f in SCHEMAS.items()}
    unread = {p["path"] for p in exp["problems"] if p.get("unread")}
    ledgers = set(exp["skipped_silently"])

    def holds(kind, rel):
        errs = []
        s = schemas[kind]
        validate(frontmatter(os.path.join(ROOT, rel)), s, s, rel, errs)
        check(not errs, f"{rel} holds to {SCHEMAS[kind]}" + "".join(f"\n       {e}" for e in errs))

    # Every file of every kind in sample/ holds to its schema.
    rel = lambda p: os.path.relpath(p, ROOT)
    decisions = sorted(glob.glob(os.path.join(ROOT, "working-on", "decisions", "*.md")))
    cards = sorted(glob.glob(os.path.join(ROOT, "working-on", "*.md")) + glob.glob(os.path.join(ROOT, "working-on", "done", "*.md")))
    runs = sorted(glob.glob(os.path.join(ROOT, "docs", "runs", "*.md")))
    specs = sorted(glob.glob(os.path.join(ROOT, "docs", "specs", "*.md")))
    check(len(decisions) == len(exp["decisions"]), f"{len(decisions)} decision files, expected.json lists {len(exp['decisions'])}")
    for p in decisions:
        holds("decision", rel(p))
    for p in cards:
        if rel(p) not in unread and rel(p) not in ledgers:
            holds("card", rel(p))
    for p in runs:
        holds("run record", rel(p))
    for p in specs:
        holds("spec", rel(p))

    # accepted_reading.crossing: an unquoted zero-padded number is the whole number, in decimal.
    check("crossing" in exp["accepted_reading"], "expected.json accepted_reading.crossing is written")
    by_number = {os.path.basename(p)[:4]: p for p in decisions}
    for d in exp["decisions"]:
        p = by_number.get(d["number"])
        if p is None:
            check(False, f"decision {d['number']} present in sample/")
            continue
        try:
            got = read_crossing(p)
        except ValueError as e:
            check(False, f"decision {d['number']}: {e}")
            continue
        check(got == d.get("crossing"), f"decision {d['number']} reads crossing {got!r}, expected {d.get('crossing')!r}")
    for f in exp["facts"]["decision"]:
        if "crossing" in f:
            check(read_crossing(by_number[f["number"]]) == f["crossing"],
                  f"decision fact {f['number']} carries crossing {f['crossing']}")
    # The sample exercises the traps: a YAML library alone gets these wrong.
    raw_0016 = frontmatter(by_number["0016"]).get("crossing")
    raw_0017 = frontmatter(by_number["0017"]).get("crossing")
    check(raw_0016 == "0019" and raw_0017 == 10,
          f"YAML 1.1 alone reads 0016's crossing as {raw_0016!r} and 0017's as {raw_0017!r} (the reading is 19 and 12)")

    # Run records' findings and specs' amendments are the facts expected.json lists.
    exp_runs = {r["name"]: r for r in exp["runs"]}
    findings = []
    for p in runs:
        name = os.path.basename(p)[:-3]
        fs = frontmatter(p).get("findings") or []
        if name in exp_runs:
            check(len(fs) == exp_runs[name]["findings"], f"run {name} has {len(fs)} findings, expected {exp_runs[name]['findings']}")
        for n, f in enumerate(fs, 1):
            row = {"run": name, "n": n, "kind": f["kind"], "to": f.get("to")}
            if f.get("card"):
                row["card"] = f["card"]
            findings.append(row)
    check(findings == exp["facts"]["finding"], "run records' findings are facts.finding")
    amendments = []
    for p in specs:
        name = os.path.basename(p)[:-3]
        for n, a in enumerate(frontmatter(p).get("amendments") or [], 1):
            row = {"spec": name, "n": n, "date": a["date"], "cause": a["cause"]}
            if a.get("card"):
                row["card"] = a["card"]
            amendments.append(row)
    check(amendments == exp["facts"]["amendment"], "specs' amendments are facts.amendment")

    for i, m in enumerate(exp["facts"]["must_refuse"]):
        errs = []
        s = schemas[m["schema"]]
        validate(m["frontmatter"], s, s, f"must_refuse[{i}]", errs)
        unchecked = [e for e in errs if "does not check" in e or "only local" in e]
        check(errs and not unchecked, f"must_refuse[{i}] refused by {SCHEMAS[m['schema']]}: {m['name']}")
    for i, m in enumerate(exp["facts"]["reader_refuses"]):
        errs = []
        validate(m["frontmatter"], schemas["decision"], schemas["decision"], f"reader_refuses[{i}]", errs)
        check(not errs, f"reader_refuses[{i}] is schema-valid (the reader refuses it): {m['name']}")

    print(f"\n{passes} passed, {len(fails)} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
