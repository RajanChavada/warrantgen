import pytest

from warrantgen import Rule
from warrantgen.emit import Unsupported, dump, template

WRITE = {
    "write_file": {"repo": Rule("var", "exact"), "path": Rule("var", "oneof"), "size": Rule("range", (1, 503)),
                   "text": Rule("wildcard"), "branch": Rule("pattern", "fixagent/*")},
    "get_pr_state": {"number": Rule("var", "exact")},
}


def test_one_runs_values_become_a_template():
    doc = template(WRITE, name="write", bindings={"repo": "acme/site", "path": ["public/robots.txt", "public/llms.txt"],
                                                 "number": 7})
    assert doc["capabilities"]["write_file"] == {"repo": "acme/site", "path": ["public/llms.txt", "public/robots.txt"],
                                                 "size": "1..503", "text": "any", "branch": {"pattern": "fixagent/*"}}
    assert doc["capabilities"]["get_pr_state"] == {"number": "7..7"}
    assert dump(doc).startswith("version: 2\nname: write\nttl: 5m\n")


def test_strings_that_read_as_wildcard_or_range_stay_exact():
    doc = template({"t": {"a": Rule("exact", "any"), "b": Rule("exact", "1..5"), "c": Rule("exact", "v1.2")}}, name="x")
    assert doc["capabilities"]["t"] == {"a": {"exact": "any"}, "b": {"exact": "1..5"}, "c": "v1.2"}


def test_refuses_instead_of_widening():
    rules = {"read_file": {"path": Rule("var", "under")}, "build": {"fixes": Rule("subset", ["a", "b"]),
                                                                    "repo": Rule("var", "exact")}}
    with pytest.raises(Unsupported) as e:
        template(rules, name="x", bindings={"path": ["web/x"]})
    assert [g.split(":")[0] for g in e.value.gaps] == ["read_file.path", "build.fixes", "build.repo"]
