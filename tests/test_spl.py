import unittest

from core.spl import lookup_refs, macro_calls, split_args, strip_comments


def stanzas(text):
    return [c["stanza"] for c in macro_calls(text)]


class TestMacroCalls(unittest.TestCase):
    def test_simple_and_with_args(self):
        calls = macro_calls('`cim_Authentication_indexes` | `security_content_ctime(firstTime)` | `drop_dm_object_name("Authentication")`')
        self.assertEqual([c["stanza"] for c in calls],
                         ["cim_Authentication_indexes", "security_content_ctime(1)", "drop_dm_object_name(1)"])
        self.assertEqual(calls[1]["args"], ["firstTime"])
        self.assertEqual(calls[1]["call"], "`security_content_ctime(firstTime)`")

    def test_arguments_with_commas_inside_quotes_and_parentheses(self):
        calls = macro_calls("""`m("a,b", if(x>1, 2, 3), 'campo, raro', "con \\" comilla")`""")
        self.assertEqual(calls[0]["arity"], 4)
        self.assertEqual(calls[0]["stanza"], "m(4)")
        self.assertEqual(calls[0]["args"][1], "if(x>1, 2, 3)")

    def test_no_args(self):
        self.assertEqual(stanzas("`m()` | `n`"), ["m", "n"])

    def test_repeated_calls_once(self):
        self.assertEqual(stanzas("`a` | `a` | `a(1)` | `a(2)`"), ["a", "a(1)"])

    def test_comments_are_ignored(self):
        spl = "index=main ```aquí `comentada` no cuenta``` | `real` ```otra `x(1)` ```"
        self.assertEqual(stanzas(spl), ["real"])
        self.assertEqual(strip_comments("a ```b``` c").split(), ["a", "c"])

    def test_tokens_and_invalid_names_are_skipped(self):
        self.assertEqual(stanzas("`$macro$` | `foo bar` | ``"), [])

    def test_split_args(self):
        self.assertEqual(split_args(""), [])
        self.assertEqual(split_args("a"), ["a"])
        self.assertEqual(split_args(" a , b "), ["a", "b"])
        self.assertEqual(split_args("a,"), ["a", ""])


class TestLookupRefs(unittest.TestCase):
    def refs(self, text):
        return {r["name"]: r["commands"] for r in lookup_refs(text)}

    def test_commands_and_options(self):
        spl = ('| inputlookup append=t start=0 assets.csv where priority="high" '
               '| lookup local=true users user OUTPUT name '
               '| outputlookup create_empty=false "mi lookup.csv"')
        self.assertEqual(self.refs(spl), {"assets.csv": ["inputlookup"], "users": ["lookup"], "mi lookup.csv": ["outputlookup"]})

    def test_subsearches_and_macro_definitions(self):
        self.assertEqual(self.refs("index=a [| inputlookup bar | fields user] [inputlookup baz]"), {"bar": ["inputlookup"], "baz": ["inputlookup"]})
        # Una definición de macro puede empezar por el comando, sin la barra
        self.assertEqual(self.refs("inputlookup foo | stats count"), {"foo": ["inputlookup"]})

    def test_same_lookup_with_several_commands(self):
        self.assertEqual(self.refs("| inputlookup x | eval a=1 | outputlookup x"), {"x": ["inputlookup", "outputlookup"]})

    def test_not_a_command(self):
        self.assertEqual(self.refs("index=lookup sourcetype=lookup lookup=1 | stats count by lookup"), {})
        self.assertEqual(self.refs("| lookupfoo x"), {})

    def test_dynamic_names_are_skipped(self):
        self.assertEqual(self.refs("| lookup `nombre_lookup` x | inputlookup $tabla$"), {})

    def test_comments_are_ignored(self):
        self.assertEqual(self.refs("index=a ```| lookup viejo x``` | lookup nuevo x"), {"nuevo": ["lookup"]})


if __name__ == "__main__":
    unittest.main()
