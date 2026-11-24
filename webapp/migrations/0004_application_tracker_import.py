"""Preserve legacy tracker date and idempotent import identity per account."""
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("webapp", "0003_mail_connections")]
    operations = [
        migrations.AddField(
            model_name="application", name="import_key",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="application", name="tracker_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name="application",
            constraint=models.UniqueConstraint(
                fields=("user", "import_key"), condition=~models.Q(import_key=""),
                name="application_import_per_user",
            ),
        ),
    ]
