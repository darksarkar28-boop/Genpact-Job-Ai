# Gmail and Outlook read-only mailbox setup

Issue #5 adds **manual, opt-in** mailbox checks to the Django application board.

1. Register a Google OAuth web app with Gmail read-only scope
   (https://www.googleapis.com/auth/gmail.readonly). Add the exact HTTPS callback
   https://YOUR_DOMAIN/applications/mail/google/callback/ to the allowed redirects.
   Set JOBWEB_GOOGLE_CLIENT_ID, JOBWEB_GOOGLE_CLIENT_SECRET and optionally
   JOBWEB_GOOGLE_REDIRECT_URI to the registered value.
2. Register a Microsoft identity-platform web app with delegated Mail.Read,
   User.Read and offline_access. Add the callback
   https://YOUR_DOMAIN/applications/mail/microsoft/callback/ and set
   JOBWEB_MICROSOFT_CLIENT_ID, JOBWEB_MICROSOFT_CLIENT_SECRET, and optionally
   JOBWEB_MICROSOFT_REDIRECT_URI and JOBWEB_MICROSOFT_TENANT (default common).
3. Generate a Fernet key using
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   and set JOBWEB_MAIL_TOKEN_KEY to the result in your secret manager.
   Keep this key stable across restarts: changing it makes old saved tokens unreadable.
4. Configure production TLS, persistent JOBWEB_SECRET_KEY and your normal
   JOBWEB_ALLOWED_HOSTS. Run python manage.py migrate and restart the web app.
5. A candidate signs in, connects the mailbox, then presses Sync and reviews each
   suggested change; nothing changes automatically. Disconnect deletes locally
   stored tokens and proposals. For remote revocation, also revoke the app in
   Google/Microsoft account permissions.

The app **never requests send/modify scopes**. Tokens are encrypted at rest;
it stores only minimal message metadata and no raw email body. Sync currently
uses the most recent two pages (at most 100 messages) per provider from a
bounded period for Gmail. It is a user-initiated check, not a background
subscription. Reconnect if refresh access expires or is revoked. Provider
credentials are required for production integration tests; GitHub browser
acceptance only uses fake client IDs and intercepts the consent navigation,
never accessing a real mailbox.


## Production acceptance and limitations

The GitHub Actions tests exercise consent URL generation, PKCE/state validation,
token encryption/refresh via provider doubles, a real Django/browser session,
and application-stage approval. A real Google/Microsoft OAuth handshake **cannot**
be established with dummy client IDs. Before a production launch, register OAuth
apps (including Google's required review where applicable) and use test Gmail and
Microsoft accounts to verify consent, token expiry/revocation, full message
pagination and account disconnection. Do not put test account refresh tokens in
GitHub Actions artifacts or commit them to the repository.

The manual sync reads recent mailbox messages only (up to 100 per provider
per click); it is not a mailbox subscription or historical backfill service.
Additional pages and background scheduling are an operational follow-up,
not something these fixture tests can prove is active. Unknown or conflicting
messages are flagged for **manual review**, without guessing their application.
