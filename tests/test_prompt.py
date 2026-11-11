import unittest

from kit.lib.prompt import PromptError, render

BODY = "Ticket {{TICKET}} in {{WORKTREE}}.\n\nTask: {{TASK}}\n\nAgain: {{TICKET}}\n"
VALUES = {"TICKET": "T-1", "WORKTREE": "/work/t1", "TASK": "Draft the page."}


class RenderTest(unittest.TestCase):
    def test_clean_body(self):
        self.assertEqual(render(BODY, VALUES),
                         "Ticket T-1 in /work/t1.\n\nTask: Draft the page.\n\nAgain: T-1\n")

    def test_unused_value_is_not_an_error(self):
        self.assertEqual(render("{{A}}", {"A": "x", "UNUSED": "y"}), "x")

    def test_missing_value_names_the_first_by_position(self):
        with self.assertRaises(PromptError) as caught:
            render("{{TICKET}} {{ZED}} {{ALPHA}}", {"TICKET": "T-1"})
        self.assertEqual(str(caught.exception), "no value for {{ZED}}")

    def test_value_holding_a_placeholder_is_not_expanded(self):
        values = dict(VALUES, TASK="Write {{TASK}} and {{TICKET}} literally.")
        self.assertIn("Task: Write {{TASK}} and {{TICKET}} literally.", render(BODY, values))

    def test_value_with_backslashes_is_inserted_as_is(self):
        self.assertEqual(render("{{A}}", {"A": r"\1 \g<0>"}), r"\1 \g<0>")

    def test_only_upper_case_names_are_placeholders(self):
        self.assertEqual(render("{{lower}} {{1X}} {{ A }}", {}), "{{lower}} {{1X}} {{ A }}")

    def test_same_input_same_bytes(self):
        first = render(BODY, VALUES).encode("utf-8")
        self.assertEqual(first, render(BODY, dict(VALUES)).encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
