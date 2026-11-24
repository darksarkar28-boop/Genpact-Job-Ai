from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("webapp", "0001_initial"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(name="CareerContext", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("data", models.JSONField(default=dict)),
            ("updated_at", models.DateTimeField(auto_now=True)),
            ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
        ]),
        migrations.CreateModel(name="MockInterview", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("context", models.JSONField(default=dict)),
            ("questions", models.JSONField(default=list)),
            ("profile_revision", models.PositiveIntegerField()),
            ("question_index", models.PositiveIntegerField(default=0)),
            ("deadline", models.DateTimeField()),
            ("mode", models.CharField(default="text", max_length=10)),
            ("recording_consent", models.BooleanField(default=False)),
            ("completed", models.BooleanField(default=False)),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
        ]),
        migrations.CreateModel(name="MockAnswer", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("question_index", models.PositiveIntegerField()),
            ("transcript", models.TextField(blank=True)),
            ("feedback", models.JSONField(default=dict)),
            ("recording", models.BinaryField(blank=True, default=bytes, editable=False)),
            ("media_type", models.CharField(blank=True, max_length=32)),
            ("timed_out", models.BooleanField(default=False)),
            ("interview", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="answers", to="webapp.mockinterview")),
        ]),
        migrations.AddConstraint(model_name="mockanswer", constraint=models.UniqueConstraint(fields=("interview", "question_index"), name="unique_mock_answer")),
    ]
