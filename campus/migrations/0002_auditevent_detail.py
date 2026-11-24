from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('campus', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='auditevent',
            name='detail',
            field=models.TextField(blank=True),
        ),
    ]
