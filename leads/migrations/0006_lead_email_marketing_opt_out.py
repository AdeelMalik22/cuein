from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('leads', '0005_sitevisit'),
    ]

    operations = [
        migrations.AddField(
            model_name='lead',
            name='email_marketing_opt_out',
            field=models.BooleanField(default=False),
        ),
    ]
