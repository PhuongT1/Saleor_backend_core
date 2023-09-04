# Generated by Django 3.2.19 on 2023-07-10 07:00

from decimal import Decimal
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    dependencies = [
        ("order", "0170_auto_20230529_1314"),
        ("discount", "0046_update_discounts_with_promotions"),
    ]

    operations = [
        migrations.CreateModel(
            name="OrderLineDiscount",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                        unique=True,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "type",
                    models.CharField(
                        choices=[
                            ("sale", "Sale"),
                            ("voucher", "Voucher"),
                            ("manual", "Manual"),
                            ("promotion", "Promotion"),
                        ],
                        default="manual",
                        max_length=10,
                    ),
                ),
                (
                    "value_type",
                    models.CharField(
                        choices=[("fixed", "fixed"), ("percentage", "%")],
                        default="fixed",
                        max_length=10,
                    ),
                ),
                (
                    "value",
                    models.DecimalField(
                        decimal_places=3, default=Decimal("0.0"), max_digits=12
                    ),
                ),
                (
                    "amount_value",
                    models.DecimalField(
                        decimal_places=3, default=Decimal("0.0"), max_digits=12
                    ),
                ),
                ("currency", models.CharField(max_length=3)),
                ("name", models.CharField(blank=True, max_length=255, null=True)),
                (
                    "translated_name",
                    models.CharField(blank=True, max_length=255, null=True),
                ),
                ("reason", models.TextField(blank=True, null=True)),
                (
                    "line",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="discounts",
                        to="order.orderline",
                    ),
                ),
                (
                    "promotion_rule",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="discount.promotionrule",
                    ),
                ),
                (
                    "sale",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="discount.sale",
                    ),
                ),
                (
                    "voucher",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="discount.voucher",
                    ),
                ),
            ],
            options={
                "ordering": ("created_at", "id"),
            },
        ),
    ]
