"""External action plugins. The TVDB action writes only after the dispatcher guard."""
from config import TVDB_BASE_URL, TVDB_PASSWORD, TVDB_USER
from net.secrets import SecretStore, SecretStoreError
from net.redaction import REDACTOR
from plugins.registry import ActionContext, ActionPlugin, register_action
from ticket import ticket_summary


class ScopedActionSecrets:
    """TVDB credentials are read from the scoped SecretStore or documented environment variables."""
    def __init__(self, action_id):
        self.action_id = action_id
        try:
            self._scope = SecretStore().scoped("action", action_id)
        except SecretStoreError:
            self._scope = None

    def get(self, slot):
        if self._scope is not None:
            try:
                value = self._scope.get(slot)
                if value:
                    REDACTOR.register(value)
                    return value
            except SecretStoreError:
                pass
        value = {"user": TVDB_USER, "password": TVDB_PASSWORD}.get(slot, "")
        REDACTOR.register(value)
        return value

    def configured(self, slot):
        return bool(self.get(slot))


class TVDBTicketAction(ActionPlugin):
    key = "tvdb-ticket"
    label = "TVDB-Ticket"
    effects = ("support-center-ticket-create",)
    needs_secrets = ("user", "password")

    def preview(self, ctx, request):
        """Build an exact local snapshot; this method never performs HTTP."""
        ticket_id = int(request["ticketId"])
        rows = ctx.reader("SELECT * FROM ticket WHERE id=?", (ticket_id,))
        if not rows:
            raise ValueError("Ticket nicht gefunden.")
        ticket = rows[0]
        if ticket["status"] != "erstellt" or ticket["tvdb_no"]:
            raise ValueError("Nur ein noch nicht eingereichter Entwurf kann vorbereitet werden.")
        subject, _cut = ticket_summary(ticket["series_title"], ticket["season"], ticket["lang"])
        return {
            "ticketId": ticket_id,
            "subject": subject,
            "bodyHtml": ticket["body_html"] or "",
            "bodyText": ticket["body"] or "",
            "target": TVDB_BASE_URL.rstrip("/") + "/open.php",
            "host": TVDB_BASE_URL.split("//", 1)[-1].split("/", 1)[0],
        }

    def execute(self, ctx, preview, confirmation):
        """Perform the write only when the caller has confirmed the stored preview."""
        if confirmation is not True:
            raise ValueError("Ausdrueckliche Bestaetigung fehlt.")
        user = ctx.secrets.get("user")
        password = ctx.secrets.get("password")
        if not user or not password:
            raise ValueError("TVDB-Zugangsdaten sind nicht konfiguriert.")
        from tvdb import submit_ticket
        number = submit_ticket(preview["subject"], preview["bodyHtml"] or preview["bodyText"],
                               user=user, password=password, http_client=ctx.http)
        return {"externalId": str(number), "summary": {"ticketId": preview["ticketId"], "tvdbNumber": int(number)}}


register_action(TVDBTicketAction)


def action_context(http_client, reader):
    return ActionContext(http=http_client, reader=reader, writer=None,
                         secrets=ScopedActionSecrets("tvdb-ticket"))
