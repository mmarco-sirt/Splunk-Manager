import os
import tempfile
import unittest

from options.alerts import watch_alerts as w


def row(name, actions, t="1700000000", sid="sid1", count="3", app="search"):
    return {"savedsearch_name": name, "alert_actions": actions, "t": t, "sid": sid, "result_count": count, "app": app}


def entry(name, to="", cc=None, bcc=None, app="search"):
    return {"name": name, "acl": {"app": app},
            "content": {"action.email.to": to, "action.email.cc": cc, "action.email.bcc": bcc}}


RECIPIENTS = w.recipients_map([
    entry("CU-SOC", "soc@sirt.com, jcrodriguezpis@sirt.com"),
    entry("CU-SOC-CC", "persona@sirt.com", cc="SOC@sirt.com"),
    entry("CU-SOC-OTRA-APP", "soc@sirt.com", app="SIRT-Estandarizacion-CU"),
    entry("CU-PRUEBA", "mmarco@sirt.com"),
    entry("Lookup Generator x", "soc@sirt.com"),
    entry("CAP1 - KPI - Alert", "soc@sirt.com"),
])


class RecipientsTests(unittest.TestCase):
    def test_parse_emails(self):
        self.assertEqual(w.parse_emails("soc@sirt.com, A@B.es;c@d.com  e@f.com", None, ""),
                         {"soc@sirt.com", "a@b.es", "c@d.com", "e@f.com"})
        self.assertEqual(w.parse_emails("$result.email$"), set())

    def test_lookup_by_app_and_fallback_by_name(self):
        self.assertIn("soc@sirt.com", w.recipients_of(row("CU-SOC-OTRA-APP", "email", app="SIRT-Estandarizacion-CU"),
                                                       RECIPIENTS))
        # La app del log no coincide: se usa el nombre
        self.assertIn("soc@sirt.com", w.recipients_of(row("CU-SOC-OTRA-APP", "email", app="search"), RECIPIENTS))
        self.assertIsNone(w.recipients_of(row("Desconocida", "email"), RECIPIENTS))


class IsAlertTests(unittest.TestCase):
    def test_email_to_soc_is_alert(self):
        self.assertTrue(w.is_alert(row("CU-SOC", "email"), "FICOSA", RECIPIENTS))
        self.assertTrue(w.is_alert(row("CU-SOC", "email,logevent"), "FICOSA", RECIPIENTS))
        self.assertTrue(w.is_alert(row("CU-SOC-CC", "email"), "FICOSA", RECIPIENTS))  # en copia y en mayúsculas

    def test_not_productive(self):
        # Correo que no va a soc@
        self.assertFalse(w.is_alert(row("CU-PRUEBA", "email"), "CZFB", RECIPIENTS))
        # Va a soc@ pero esta vez no ha enviado el correo (solo logevent)
        self.assertFalse(w.is_alert(row("CU-SOC", "logevent"), "FICOSA", RECIPIENTS))
        # Cyences, notables, mantenimiento
        for actions in ("cyences_notable_event_action,cyences_send_email_action", "notable", "risk,notable", "lookup", ""):
            self.assertFalse(w.is_alert(row("CU-SOC", actions), "FICOSA", RECIPIENTS), actions)
        # Alerta que no está en la configuración leída
        self.assertFalse(w.is_alert(row("Desconocida", "email"), "FICOSA", RECIPIENTS))

    def test_other_required_recipient(self):
        self.assertTrue(w.is_alert(row("CU-PRUEBA", "email"), "CZFB", RECIPIENTS, "mmarco@sirt.com"))

    def test_ignored_names(self):
        self.assertFalse(w.is_alert(row("Lookup Generator x", "email"), "CZFB", RECIPIENTS))
        self.assertFalse(w.is_alert(row("CAP1 - KPI - Alert", "email"), "VEUCTTI", RECIPIENTS))
        # El filtro por cliente solo aplica a ese cliente
        self.assertTrue(w.is_alert(row("CAP1 - KPI - Alert", "email"), "FICOSA", RECIPIENTS))


class NewAlertsTests(unittest.TestCase):
    def test_deduplicates_by_sid(self):
        seen = {}
        rows = [row("CU-SOC", "email", t="2", sid="s2"), row("CU-SOC", "email", t="1", sid="s1"),
                row("CU-PRUEBA", "email", sid="s3")]
        first = w.new_alerts(rows, "FICOSA", seen, RECIPIENTS)
        self.assertEqual([r["sid"] for r in first], ["s1", "s2"])  # ordenadas por hora, sin la que no va a soc@
        self.assertEqual(w.new_alerts(rows, "FICOSA", seen, RECIPIENTS), [])
        # Mismo sid en otro cliente es otra alerta
        self.assertEqual(len(w.new_alerts(rows, "PSPV", seen, RECIPIENTS)), 2)

    def test_state_round_trip_and_expiry(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "estado.json")
            seen = {"nuevo": w.time.time(), "viejo": w.time.time() - w.SEEN_TTL - 10}
            kept = w.save_state(path, {"FICOSA": 123.0}, seen)
            self.assertEqual(set(kept), {"nuevo"})
            last, loaded = w.load_state(path)
            self.assertEqual(last, {"FICOSA": 123.0})
            self.assertEqual(set(loaded), {"nuevo"})


class TeamsMessageTests(unittest.TestCase):
    def test_card_and_detail(self):
        alerts = [row("A", "email", t="1700000000", sid="1", count="2"), row("A", "email", t="1700000600", sid="2"),
                  row("B", "logevent", sid="3", count="1")]
        lines = w.detail_lines(alerts)
        self.assertEqual(len(lines), 2)
        self.assertIn("2 veces, 5 resultados", lines[0])
        self.assertIn("1 vez, 1 resultados", lines[1])

        sent = []
        channel = w.TeamsChannel("https://example.invalid/hook")
        original = w.teams.post
        w.teams.post = lambda url, message: sent.append(message)
        try:
            channel.alerts({"FICOSA": alerts})
        finally:
            w.teams.post = original
        body = sent[0]["attachments"][0]["content"]["body"]
        self.assertEqual(sent[0]["attachments"][0]["contentType"], "application/vnd.microsoft.card.adaptive")
        self.assertIn("3 alerta(s) nueva(s) en 1 cliente(s)", body[0]["text"])
        self.assertEqual(body[1]["text"], "FICOSA (3)")

    def test_failure_does_not_raise(self):
        channel = w.TeamsChannel("https://example.invalid/hook")
        original_post, original_toast = w.teams.post, w.toast
        w.teams.post = lambda url, message: (_ for _ in ()).throw(OSError("caído"))
        w.toast = lambda title, lines: None
        try:
            self.assertFalse(channel.send("t", []))
            self.assertTrue(channel.failing)
        finally:
            w.teams.post, w.toast = original_post, original_toast


if __name__ == "__main__":
    unittest.main()
