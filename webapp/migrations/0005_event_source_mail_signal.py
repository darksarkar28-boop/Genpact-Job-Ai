"""Link audited application-stage events to their approved source email."""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("webapp", "0004_application_tracker_import")]
    operations = [
        migrations.AddField(
            model_name="applicationstatusevent",
            name="mail_signal",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="status_events", to="webapp.mailsignal",
            ),
        ),
    ]
