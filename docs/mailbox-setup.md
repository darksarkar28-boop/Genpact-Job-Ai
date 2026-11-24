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
