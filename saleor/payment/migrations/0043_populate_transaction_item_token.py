# Generated by Django 3.2.18 on 2023-03-20 12:52

from django.apps import apps as registry
from django.db import migrations
from django.db.models.signals import post_migrate

from .tasks.saleor3_12 import update_transaction_token_field


def update_transaction_uuid_field(apps, _schema_editor):
    def on_migrations_complete(sender=None, **kwargs):
        update_transaction_token_field.delay()

    sender = registry.get_app_config("account")
    post_migrate.connect(on_migrations_complete, weak=False, sender=sender)


class Migration(migrations.Migration):
    dependencies = [
        ("payment", "0042_auto_20230320_1252"),
    ]

    operations = [
        migrations.RunPython(
            update_transaction_uuid_field, reverse_code=migrations.RunPython.noop
        ),
    ]
