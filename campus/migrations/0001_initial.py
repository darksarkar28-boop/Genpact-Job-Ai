import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Cohort',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
            ],
            options={
                'ordering': ['name', 'pk'],
            },
        ),
        migrations.CreateModel(
            name='Organization',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('slug', models.SlugField(unique=True)),
                ('name', models.CharField(max_length=120)),
                ('seat_limit', models.PositiveIntegerField()),
            ],
        ),
        migrations.CreateModel(
            name='Membership',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('role', models.CharField(choices=[('admin', 'Organization admin'), ('mentor', 'Mentor'), ('candidate', 'Candidate')], max_length=12)),
                ('contact_email', models.EmailField(blank=True, max_length=254)),
                ('active', models.BooleanField(default=True)),
                ('applications', models.PositiveIntegerField(default=0)),
                ('interviews', models.PositiveIntegerField(default=0)),
                ('offers', models.PositiveIntegerField(default=0)),
                ('reported_at', models.DateTimeField(blank=True, null=True)),
                ('joined_at', models.DateTimeField(auto_now_add=True)),
                ('cohort', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='members', to='campus.cohort')),
                ('mentor_cohorts', models.ManyToManyField(blank=True, related_name='mentors', to='campus.cohort')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='campus_memberships', to=settings.AUTH_USER_MODEL)),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='members', to='campus.organization')),
            ],
        ),
        migrations.CreateModel(
            name='Invitation',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('email', models.EmailField(max_length=254)),
                ('role', models.CharField(choices=[('admin', 'Organization admin'), ('mentor', 'Mentor'), ('candidate', 'Candidate')], max_length=12)),
                ('expires_at', models.DateTimeField()),
                ('revoked_at', models.DateTimeField(null=True)),
                ('accepted_at', models.DateTimeField(null=True)),
                ('accepted_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('cohort', models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, to='campus.cohort')),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='invitations', to='campus.organization')),
            ],
        ),
        migrations.AddField(
            model_name='cohort',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='cohorts', to='campus.organization'),
        ),
        migrations.CreateModel(
            name='AuditEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('action', models.CharField(max_length=80)),
                ('subject', models.CharField(max_length=254)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('actor', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='events', to='campus.organization')),
            ],
            options={
                'ordering': ['-pk'],
            },
        ),
        migrations.CreateModel(
            name='ResumeReview',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('shared_at', models.DateTimeField(auto_now_add=True)),
                ('withdrawn_at', models.DateTimeField(null=True)),
                ('cohort', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='reviews', to='campus.cohort')),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reviews', to='campus.membership')),
            ],
        ),
        migrations.CreateModel(
            name='Revision',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('number', models.PositiveIntegerField()),
                ('filename', models.CharField(max_length=150)),
                ('document', models.BinaryField()),
                ('sha256', models.CharField(max_length=64)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('review', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='revisions', to='campus.resumereview')),
            ],
            options={
                'ordering': ['-number'],
            },
        ),
        migrations.CreateModel(
            name='Feedback',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('body', models.TextField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('mentor', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='campus.membership')),
                ('revision', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='feedback', to='campus.revision')),
            ],
            options={
                'ordering': ['created_at', 'pk'],
            },
        ),
        migrations.AddConstraint(
            model_name='membership',
            constraint=models.UniqueConstraint(fields=('organization', 'user'), name='campus_one_membership'),
        ),
        migrations.AddConstraint(
            model_name='membership',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('cohort__isnull', False), ('role', 'candidate')), models.Q(('cohort__isnull', True), ('role__in', ['admin', 'mentor'])), _connector='OR'), name='campus_candidate_has_cohort'),
        ),
        migrations.AddConstraint(
            model_name='cohort',
            constraint=models.UniqueConstraint(fields=('organization', 'name'), name='campus_cohort_name'),
        ),
        migrations.AddConstraint(
            model_name='resumereview',
            constraint=models.UniqueConstraint(condition=models.Q(('withdrawn_at__isnull', True)), fields=('owner',), name='campus_one_open_review'),
        ),
        migrations.AddConstraint(
            model_name='revision',
            constraint=models.UniqueConstraint(fields=('review', 'number'), name='campus_revision_number'),
        ),
    ]
