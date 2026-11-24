"""Store descriptive audit origins on PostgreSQL and other strict SQL engines."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("webapp", "0005_event_source_mail_signal")]
    operations = [
        migrations.AlterField(
            model_name="applicationstatusevent",
            name="origin",
            field=models.CharField(default="manual", max_length=32),
        ),
    ]
