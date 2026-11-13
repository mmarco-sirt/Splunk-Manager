import os
import tempfile
import unittest

from options.alerts import watch_alerts as w


def row(name, actions, t="1700000000", sid="sid1", count="3"):
    return {"savedsearch_name": name, "alert_actions": actions, "t": t, "sid": sid, "result_count": count}


class IsAlertTests(unittest.TestCase):
    def test_notification_actions_are_alerts(self):
        self.assertTrue(w.is_alert(row("CU-1 X", "email"), "FICOSA"))
        self.assertTrue(w.is_alert(row("CU-1 X", "logevent"), "FICOSA"))
        # Cyences no se avisa, salvo que la alerta tenga además una acción propia
        self.assertFalse(w.is_alert(row("Auth", "cyences_notable_event_action,cyences_send_email_action"), "FICOSA"))
        self.assertTrue(w.is_alert(row("Auth", "cyences_notable_event_action,email"), "FICOSA"))
        self.assertTrue(w.is_alert(row("Audit - X - Rule", "notable"), "PUIG"))
        # Una acción de aviso junto a otra de mantenimiento sigue siendo alerta
        self.assertTrue(w.is_alert(row("Rule", "risk,notable"), "PUIG"))

    def test_maintenance_only_is_not_alert(self):
        for actions in ("lookup", "summary_index", "outputtelemetry", "risk", "itsi_import_objects,summary_index", ""):
            self.assertFalse(w.is_alert(row("X", actions), "FICOSA"), actions)

    def test_ignored_names(self):
        self.assertFalse(w.is_alert(row("Lookup Generator sirt_asset_ip_by_hostname", "logevent"), "CZFB"))
        self.assertFalse(w.is_alert(row("Alimentar BRS", "cyences_notable_event_action"), "VILADECANS"))
        self.assertFalse(w.is_alert(row("CAP1 - Nombre de trucades - Alert", "logevent"), "VEUCTTI"))
        # El filtro por cliente solo aplica a ese cliente
        self.assertTrue(w.is_alert(row("CAP1 - Nombre de trucades - Alert", "logevent"), "FICOSA"))


class NewAlertsTests(unittest.TestCase):
    def test_deduplicates_by_sid(self):
        seen = {}
        rows = [row("A", "email", t="2", sid="s2"), row("A", "email", t="1", sid="s1"), row("L", "lookup", sid="s3")]
        first = w.new_alerts(rows, "FICOSA", seen)
        self.assertEqual([r["sid"] for r in first], ["s1", "s2"])  # ordenadas por hora, sin la de mantenimiento
        self.assertEqual(w.new_alerts(rows, "FICOSA", seen), [])
        # Mismo sid en otro cliente es otra alerta
        self.assertEqual(len(w.new_alerts(rows, "PSPV", seen)), 2)

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
