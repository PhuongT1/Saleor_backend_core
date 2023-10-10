# Generated by Django 3.2.20 on 2023-08-21 07:23

from django.db import migrations


BATCH_SIZE = 5000


def queryset_in_batches(queryset):
    start_pk = 0

    while True:
        qs = queryset.order_by("pk").filter(pk__gt=start_pk)[:BATCH_SIZE]
        pks = list(qs.values_list("pk", flat=True))

        if not pks:
            break

        yield pks

        start_pk = pks[-1]


def move_codes_to_new_model(apps, schema_editor):
    Voucher = apps.get_model("discount", "Voucher")
    VoucherCode = apps.get_model("discount", "VoucherCode")

    queryset = Voucher.objects.all()

    for batch_pks in queryset_in_batches(queryset):
        voucher_codes = []
        vouchers = Voucher.objects.filter(pk__in=batch_pks)

        for voucher in vouchers.values("id", "code", "used"):
            voucher_codes.append(
                VoucherCode(
                    voucher_id=voucher["id"],
                    code=voucher["code"],
                    used=voucher["used"],
                )
            )
        VoucherCode.objects.bulk_create(voucher_codes)


class Migration(migrations.Migration):
    dependencies = [
        ("discount", "0056_voucher_code_indexes"),
    ]

    operations = [
        migrations.RunPython(
            move_codes_to_new_model,
            migrations.RunPython.noop,
        ),
    ]