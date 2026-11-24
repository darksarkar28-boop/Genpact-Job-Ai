from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(name="Source", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("key", models.CharField(max_length=160, unique=True)),
            ("provider", models.CharField(max_length=32)),
            ("label", models.CharField(max_length=200)),
            ("enabled", models.BooleanField(default=True)),
            ("status", models.CharField(default="pending", max_length=32)),
            ("last_attempt", models.DateTimeField(blank=True, null=True)),
            ("last_success", models.DateTimeField(blank=True, null=True)),
            ("invalid_count", models.PositiveIntegerField(default=0)),
        ]),
        migrations.CreateModel(name="Posting", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("external_id", models.TextField()),
            ("employer", models.TextField()),
            ("title", models.TextField()),
            ("location", models.TextField(blank=True)),
            ("url", models.TextField()),
            ("region", models.CharField(max_length=8)),
            ("description", models.TextField(blank=True)),
            ("legacy_key", models.CharField(max_length=160)),
            ("posted_at", models.DateTimeField(blank=True, null=True)),
            ("updated_at", models.DateTimeField(blank=True, null=True)),
            ("expires_at", models.DateTimeField(blank=True, null=True)),
            ("last_seen", models.DateTimeField(db_index=True)),
            ("active", models.BooleanField(default=True)),
            ("source", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="postings", to="jobfeeds.source")),
        ], options={"constraints": [models.UniqueConstraint(fields=("source", "external_id"), name="jobfeeds_unique_source_job")]}),
        migrations.CreateModel(name="Preference", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("market", models.CharField(default="all", max_length=8)),
            ("role", models.CharField(blank=True, max_length=200)),
            ("location", models.CharField(blank=True, max_length=200)),
            ("zone", models.CharField(default="Asia/Kolkata", max_length=80)),
            ("hour", models.PositiveSmallIntegerField(default=8)),
            ("email", models.EmailField(blank=True, max_length=254)),
            ("verified", models.BooleanField(default=False)),
            ("opted_in", models.BooleanField(default=False)),
            ("verification_sent", models.DateTimeField(blank=True, null=True)),
            ("nonce", models.CharField(blank=True, max_length=64)),
            ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
        ]),
        migrations.CreateModel(name="Delivery", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("local_date", models.DateField()),
            ("status", models.CharField(default="claimed", max_length=16)),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("preference", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="jobfeeds.preference")),
        ], options={"constraints": [models.UniqueConstraint(fields=("preference", "local_date"), name="jobfeeds_one_digest_per_day")]}),
    ]
