import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kit.lib.frontmatter import FrontMatterError, as_list, parse  # noqa: E402

# The role file example in docs/system-brief.md section 5, verbatim.
BRIEF_EXAMPLE = """\
---
role: story-artist
room: story                # story | production | post | distribution | lab | studio
kind: maker                # orchestrator | maker | checker | judge | operator | analyst
analog: story artist
stage: board               # stages this role works in
writes: episodes/*/sequences/*/shots/**
reads: bible/**, episodes/**
checked_by: continuity-supervisor, pitch-judge   # makers only
may_not: change the style lock
model: maker               # maker | checker: which configured model slot runs it
family: any                # any | other: `other` must not share the maker's model family
tools: Read, Edit, Write, Grep, Glob, Bash       # harness tools the role may use
knowledge: skills, memory-read                   # Fermi capabilities (docs/harness.md); empty for none
secrets:                   # names of tool credentials injected into this role's environment
gated: no                  # yes for operators: each job needs a producer gate PASS
---
<prompt body>
"""


class ParseTest(unittest.TestCase):
    def test_brief_example(self):
        meta, body = parse(BRIEF_EXAMPLE)
        self.assertEqual(meta, {
            "role": "story-artist",
            "room": "story",
            "kind": "maker",
            "analog": "story artist",
            "stage": "board",
            "writes": "episodes/*/sequences/*/shots/**",
            "reads": "bible/**, episodes/**",
            "checked_by": "continuity-supervisor, pitch-judge",
            "may_not": "change the style lock",
            "model": "maker",
            "family": "any",
            "tools": "Read, Edit, Write, Grep, Glob, Bash",
            "knowledge": "skills, memory-read",
            "secrets": "",
            "gated": "no",
        })
        self.assertEqual(as_list(meta["checked_by"]), ["continuity-supervisor", "pitch-judge"])
        self.assertEqual(as_list(meta["secrets"]), [])
        self.assertEqual(body, "<prompt body>\n")

    def test_body_is_everything_after_the_second_marker(self):
        meta, body = parse("---\na: 1\n---\nline one\n---\nline three\n")
        self.assertEqual(meta, {"a": "1"})
        self.assertEqual(body, "line one\n---\nline three\n")

    def test_hash_without_whitespace_is_not_a_comment(self):
        meta, _ = parse("---\nchannel: a#b   # comment\nwhole: #all\n---\n")
        self.assertEqual(meta, {"channel": "a#b", "whole": ""})

    def test_comment_line_and_blank_line_are_ignored(self):
        meta, _ = parse("---\n# a note\n\na: x\n   # indented note\n---\n")
        self.assertEqual(meta, {"a": "x"})

    def test_value_keeps_later_colons(self):
        meta, _ = parse("---\nlink: media:abc\n---\n")
        self.assertEqual(meta, {"link": "media:abc"})

    def test_empty_value_and_empty_body(self):
        self.assertEqual(parse("---\nsecrets:\n---\n"), ({"secrets": ""}, ""))

    def test_duplicate_key_is_an_error_with_its_line(self):
        with self.assertRaises(FrontMatterError) as caught:
            parse("---\na: 1\nb: 2\na: 3\n---\n")
        self.assertEqual(caught.exception.line, 4)
        self.assertIn("line 4", str(caught.exception))
        self.assertIn("duplicate key 'a'", str(caught.exception))

    def test_line_without_colon_is_an_error_with_its_line(self):
        with self.assertRaises(FrontMatterError) as caught:
            parse("---\na: 1\nno colon here\n---\n")
        self.assertEqual(caught.exception.line, 3)
        self.assertIn("no colon", str(caught.exception))

    def test_colon_only_in_comment_is_still_no_colon(self):
        with self.assertRaises(FrontMatterError) as caught:
            parse("---\nword # key: value\n---\n")
        self.assertEqual(caught.exception.line, 2)

    def test_missing_closing_marker_is_an_error(self):
        with self.assertRaises(FrontMatterError) as caught:
            parse("---\na: 1\nb: 2\n")
        self.assertEqual(caught.exception.line, 1)
        self.assertIn("missing closing ---", str(caught.exception))

    def test_missing_opening_marker_is_an_error(self):
        with self.assertRaises(FrontMatterError) as caught:
            parse("a: 1\n---\n")
        self.assertEqual(caught.exception.line, 1)

    def test_windows_line_endings(self):
        self.assertEqual(parse("---\r\na: 1\r\n---\r\nbody\r\n"), ({"a": "1"}, "body\r\n"))


class AsListTest(unittest.TestCase):
    def test_splits_and_trims(self):
        self.assertEqual(as_list(" a,b ,  c d ,"), ["a", "b", "c d"])

    def test_empty(self):
        self.assertEqual(as_list(""), [])
        self.assertEqual(as_list(None), [])


if __name__ == "__main__":
    unittest.main()
