"""Keep the originating provider message reference in history after disconnect."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("webapp", "0006_expand_application_event_origin")]
    operations = [
        migrations.AddField(
            model_name="applicationstatusevent", name="source_message_id",
            field=models.CharField(max_length=512, blank=True),
        ),
        migrations.AddField(
            model_name="applicationstatusevent", name="source_message_link",
            field=models.URLField(max_length=2048, blank=True),
        ),
    ]
