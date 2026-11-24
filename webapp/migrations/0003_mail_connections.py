# Mailbox credentials and idempotent signals (issue #5)
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ("webapp", "0002_application_board"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name="MailConnection",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("provider", models.CharField(max_length=16, choices=[("google", "Gmail"), ("microsoft", "Outlook")])),
                ("access_token_ciphertext", models.TextField()),
                ("refresh_token_ciphertext", models.TextField(blank=True)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("needs_reconnect", models.BooleanField(default=False)),
                ("last_sync_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="mail_connections", to=settings.AUTH_USER_MODEL)),
            ]),
        migrations.CreateModel(
            name="MailSignal",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("provider_message_id", models.CharField(max_length=512)),
                ("subject", models.CharField(blank=True, max_length=300)),
                ("sender", models.CharField(blank=True, max_length=250)),
                ("source_link", models.URLField(blank=True, max_length=2048)),
                ("received_at", models.DateTimeField(blank=True, null=True)),
                ("proposed_status", models.CharField(blank=True, max_length=20)),
                ("base_version", models.PositiveIntegerField(blank=True, null=True)),
                ("state", models.CharField(choices=[
                    ("pending", "Awaiting approval"), ("needs_review", "Needs manual review"),
                    ("ignored", "No actionable change"), ("approved", "Approved"),
                    ("dismissed", "Dismissed"), ("stale", "Stale"),
                ], default="ignored", max_length=20)),
                ("reason", models.CharField(blank=True, max_length=300)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("decided_at", models.DateTimeField(blank=True, null=True)),
                ("application", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="mail_signals", to="webapp.application")),
                ("connection", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="signals", to="webapp.mailconnection")),
            ]),
        migrations.AddConstraint(model_name="mailconnection", constraint=models.UniqueConstraint(fields=("user", "provider"), name="mail_connection_per_user")),
        migrations.AddConstraint(model_name="mailsignal", constraint=models.UniqueConstraint(fields=("connection", "provider_message_id"), name="mail_message_once")),
    ]
