from unittest import mock

import pytest

from ....core import EventDeliveryStatus
from ....core.models import EventDelivery, EventPayload
from ....webhook.event_types import WebhookEventSyncType
from ....webhook.models import Webhook, WebhookEvent
from ..tasks import trigger_tax_webhook_sync
from ..utils import parse_tax_data


@pytest.fixture
def tax_checkout_webhooks(tax_app):
    webhooks = [
        Webhook(
            name=f"Tax checkout webhook no {i}",
            app=tax_app,
            target_url=f"https://www.example.com/tax-checkout-{i}",
        )
        for i in range(3)
    ]
    Webhook.objects.bulk_create(webhooks)
    WebhookEvent.objects.bulk_create(
        WebhookEvent(
            event_type=WebhookEventSyncType.CHECKOUT_CALCULATE_TAXES,
            webhook=webhook,
        )
        for webhook in webhooks
    )

    return webhooks


@mock.patch("saleor.plugins.webhook.tasks.send_webhook_request_sync")
def test_trigger_tax_webhook_sync(
    mock_request,
    tax_checkout_webhook,
    tax_data_response,
):
    # given
    mock_request.return_value = tax_data_response
    event_type = WebhookEventSyncType.CHECKOUT_CALCULATE_TAXES
    data = '{"key": "value"}'

    # when
    tax_data = trigger_tax_webhook_sync(event_type, data, parse_tax_data)

    # then
    payload = EventPayload.objects.first()
    delivery = EventDelivery.objects.first()
    assert payload.payload == data
    assert delivery.status == EventDeliveryStatus.PENDING
    assert delivery.event_type == event_type
    assert delivery.payload == payload
    assert delivery.webhook == tax_checkout_webhook

    mock_request.assert_called_once_with(tax_checkout_webhook.app.name, delivery)
    assert tax_data == parse_tax_data(tax_data_response)


@mock.patch("saleor.plugins.webhook.tasks.send_webhook_request_sync")
def test_trigger_tax_webhook_sync_multiple_webhooks_first(
    mock_request,
    tax_checkout_webhooks,
    tax_data_response,
):
    # given
    mock_request.side_effect = [tax_data_response, {}, {}]
    event_type = WebhookEventSyncType.CHECKOUT_CALCULATE_TAXES
    data = '{"key": "value"}'

    # when
    tax_data = trigger_tax_webhook_sync(event_type, data, parse_tax_data)

    # then
    successful_webhook = tax_checkout_webhooks[0]
    payload = EventPayload.objects.first()
    delivery = EventDelivery.objects.first()
    assert payload.payload == data
    assert delivery.status == EventDeliveryStatus.PENDING
    assert delivery.event_type == event_type
    assert delivery.payload == payload
    assert delivery.webhook == successful_webhook

    mock_request.assert_called_once_with(successful_webhook.app.name, delivery)
    assert tax_data == parse_tax_data(tax_data_response)


@mock.patch("saleor.plugins.webhook.tasks.send_webhook_request_sync")
def test_trigger_tax_webhook_sync_multiple_webhooks_last(
    mock_request,
    tax_checkout_webhooks,
    tax_data_response,
):
    # given
    mock_request.side_effect = [{}, {}, tax_data_response]
    event_type = WebhookEventSyncType.CHECKOUT_CALCULATE_TAXES
    data = '{"key": "value"}'

    # when
    tax_data = trigger_tax_webhook_sync(event_type, data, parse_tax_data)

    # then
    assert mock_request.call_count == 3
    payload = EventPayload.objects.first()
    deliveries = EventDelivery.objects.order_by("pk")
    assert payload.payload == data

    for delivery, webhook in zip(deliveries, tax_checkout_webhooks):
        delivery.status = EventDeliveryStatus.PENDING
        delivery.event_type = event_type
        delivery.payload = payload
        delivery.webhook = webhook

    for call, webhook, delivery in zip(
        mock_request.call_args_list, tax_checkout_webhooks, deliveries
    ):
        assert call == ((webhook.app.name, delivery), {})

    assert tax_data == parse_tax_data(tax_data_response)


@mock.patch("saleor.plugins.webhook.tasks.send_webhook_request_sync")
def test_trigger_tax_webhook_sync_invalid_webhooks(
    mock_request,
    tax_checkout_webhooks,
    tax_data_response,
):
    # given
    mock_request.return_value = {}
    event_type = WebhookEventSyncType.CHECKOUT_CALCULATE_TAXES
    data = '{"key": "value"}'

    # when
    tax_data = trigger_tax_webhook_sync(event_type, data, parse_tax_data)

    # then
    assert mock_request.call_count == len(tax_checkout_webhooks)
    assert tax_data is None
