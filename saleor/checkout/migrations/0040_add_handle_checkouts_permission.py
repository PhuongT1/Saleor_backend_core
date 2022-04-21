# Generated by Django 3.2.12 on 2022-03-08 10:35

from django.core.management.sql import emit_post_migrate_signal
from django.db import migrations


def assign_permissions(apps, schema_editor):
    # force post signal as permissions are created in post migrate signals
    # related Django issue https://code.djangoproject.com/ticket/23422
    emit_post_migrate_signal(2, False, "default")
    Permission = apps.get_model("auth", "Permission")
    App = apps.get_model("app", "App")
    Group = apps.get_model("auth", "Group")

    handle_checkouts = Permission.objects.filter(
        codename="handle_checkouts", content_type__app_label="checkout"
    ).first()
    manage_checkouts = Permission.objects.filter(
        codename="manage_checkouts", content_type__app_label="checkout"
    ).first()

    apps = App.objects.filter(
        permissions=manage_checkouts,
    )
    for app in apps.iterator():
        app.permissions.add(handle_checkouts)

    groups = Group.objects.filter(
        permissions=manage_checkouts,
    )
    for group in groups.iterator():
        group.permissions.add(handle_checkouts)


class Migration(migrations.Migration):

    dependencies = [
        ("product", "0159_auto_20220209_1501"),
        ("order", "0133_rename_order_token_id"),
        ("checkout", "0039_alter_checkout_email"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="checkout",
            options={
                "ordering": ("-last_change", "pk"),
                "permissions": (
                    ("manage_checkouts", "Manage checkouts"),
                    ("handle_checkouts", "Handle checkouts"),
                ),
            },
        ),
        migrations.RunPython(assign_permissions, migrations.RunPython.noop),
    ]
