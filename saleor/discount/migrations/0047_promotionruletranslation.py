# Generated by Django 3.2.18 on 2023-06-23 13:21

from django.db import migrations, models
import django.db.models.deletion
import saleor.core.db.fields
import saleor.core.utils.editorjs


class Migration(migrations.Migration):
    dependencies = [
        ("discount", "0046_promotiontranslation"),
    ]

    operations = [
        migrations.CreateModel(
            name="PromotionRuleTranslation",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("language_code", models.CharField(max_length=35)),
                ("name", models.CharField(blank=True, max_length=255, null=True)),
                (
                    "description",
                    saleor.core.db.fields.SanitizedJSONField(
                        blank=True,
                        null=True,
                        sanitizer=saleor.core.utils.editorjs.clean_editor_js,
                    ),
                ),
                (
                    "promotion_rule",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="translations",
                        to="discount.promotionrule",
                    ),
                ),
            ],
            options={
                "unique_together": {("language_code", "promotion_rule")},
            },
        ),
    ]
