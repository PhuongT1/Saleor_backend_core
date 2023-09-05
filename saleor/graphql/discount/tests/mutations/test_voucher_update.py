import json
from unittest.mock import patch

import graphene
from django.utils.functional import SimpleLazyObject
from freezegun import freeze_time

from .....core.utils.json_serializer import CustomJsonEncoder
from .....discount import DiscountValueType
from .....webhook.event_types import WebhookEventAsyncType
from .....webhook.payloads import generate_meta, generate_requestor
from ....tests.utils import get_graphql_content
from ...enums import DiscountValueTypeEnum

UPDATE_VOUCHER_MUTATION = """
mutation voucherUpdate($id: ID!, $input: VoucherInput!) {
        voucherUpdate(id: $id, input: $input) {
            errors {
                field
                code
                message
                voucherCodes
            }
            voucher {
                type
                minCheckoutItemsQuantity
                name
                codes{
                    code
                }
                discountValueType
                startDate
                endDate
                applyOncePerOrder
                applyOncePerCustomer
            }
        }
    }
"""


def test_update_voucher(staff_api_client, voucher, permission_manage_discounts):
    # given
    apply_once_per_order = not voucher.apply_once_per_order
    # Set discount value type to 'fixed' and change it in mutation
    voucher.discount_value_type = DiscountValueType.FIXED
    voucher.save()
    assert voucher.codes.count() == 1

    variables = {
        "id": graphene.Node.to_global_id("Voucher", voucher.id),
        "input": {
            "codes": [
                {"code": "newCode", "usageLimit": 10},
            ],
            "discountValueType": DiscountValueTypeEnum.PERCENTAGE.name,
            "applyOncePerOrder": apply_once_per_order,
            "minCheckoutItemsQuantity": 10,
        },
    }

    # when
    response = staff_api_client.post_graphql(
        UPDATE_VOUCHER_MUTATION, variables, permissions=[permission_manage_discounts]
    )
    content = get_graphql_content(response)
    data = content["data"]["voucherUpdate"]["voucher"]
    voucher.refresh_from_db()

    # then
    assert len(data["codes"]) == 2
    assert data["discountValueType"] == DiscountValueType.PERCENTAGE.upper()
    assert data["applyOncePerOrder"] == apply_once_per_order
    assert data["minCheckoutItemsQuantity"] == 10
    assert voucher.codes.count() == 2


@freeze_time("2022-05-12 12:00:00")
@patch("saleor.plugins.webhook.plugin.get_webhooks_for_event")
@patch("saleor.plugins.webhook.plugin.trigger_webhooks_async")
def test_update_voucher_trigger_webhook(
    mocked_webhook_trigger,
    mocked_get_webhooks_for_event,
    any_webhook,
    staff_api_client,
    voucher,
    permission_manage_discounts,
    settings,
):
    # given
    mocked_get_webhooks_for_event.return_value = [any_webhook]
    settings.PLUGINS = ["saleor.plugins.webhook.plugin.WebhookPlugin"]
    new_code = "newCode"

    variables = {
        "id": graphene.Node.to_global_id("Voucher", voucher.id),
        "input": {
            "codes": [
                {"code": new_code, "usageLimit": 10},
            ]
        },
    }

    # when
    response = staff_api_client.post_graphql(
        UPDATE_VOUCHER_MUTATION, variables, permissions=[permission_manage_discounts]
    )
    content = get_graphql_content(response)

    # then
    assert content["data"]["voucherUpdate"]["voucher"]
    mocked_webhook_trigger.assert_called_once_with(
        json.dumps(
            {
                "id": variables["id"],
                "name": voucher.name,
                "code": new_code,
                "meta": generate_meta(
                    requestor_data=generate_requestor(
                        SimpleLazyObject(lambda: staff_api_client.user)
                    )
                ),
            },
            cls=CustomJsonEncoder,
        ),
        WebhookEventAsyncType.VOUCHER_UPDATED,
        [any_webhook],
        voucher,
        SimpleLazyObject(lambda: staff_api_client.user),
    )
