# Application board and immutable per-stage history (issue #5)
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

STATUS_CHOICES = [
    ("drafted", "Drafted"), ("applied", "Applied"), ("interview", "Interview"),
    ("offer", "Offer"), ("hired", "Hired"), ("rejected", "Rejected"),
    ("no_response", "No response"), ("offer_declined", "Offer declined"),
    ("withdrawn", "Withdrawn"),
]


class Migration(migrations.Migration):
    dependencies = [
        ("webapp", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Application",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("company", models.CharField(max_length=250)),
                ("role", models.CharField(max_length=250)),
                ("source_url", models.URLField(blank=True, max_length=2048)),
                ("status", models.CharField(choices=STATUS_CHOICES, default="drafted", max_length=20)),
                ("version", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="job_applications", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="ApplicationStatusEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("before", models.CharField(blank=True, max_length=20)),
                ("after", models.CharField(choices=STATUS_CHOICES, max_length=20)),
                ("origin", models.CharField(default="manual", max_length=16)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("application", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="events", to="webapp.application")),
            ],
        ),
    ]
