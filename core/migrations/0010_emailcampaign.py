import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0009_user_unique_user_email_case_insensitive'),
    ]

    operations = [
        migrations.CreateModel(
            name='EmailCampaign',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('subject', models.CharField(max_length=255)),
                ('body', models.TextField()),
                ('filter_stage', models.CharField(blank=True, max_length=20)),
                ('filter_source', models.CharField(blank=True, max_length=20)),
                ('recipient_emails', models.JSONField(default=list)),
                ('recipient_count', models.PositiveIntegerField(default=0)),
                ('status', models.CharField(choices=[('queued', 'Queued'), ('sending', 'Sending'), ('sent', 'Sent'), ('failed', 'Failed')], default='queued', max_length=16)),
                ('error_message', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('sent_at', models.DateTimeField(blank=True, null=True)),
                ('business', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.business')),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='created_email_campaigns', to=settings.AUTH_USER_MODEL)),
                ('filter_assigned_user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='email_campaign_filters', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'indexes': [models.Index(fields=['business', 'created_at'], name='core_emailc_busines_2bdd19_idx'), models.Index(fields=['business', 'status'], name='core_emailc_busines_2ef909_idx')],
            },
        ),
    ]
