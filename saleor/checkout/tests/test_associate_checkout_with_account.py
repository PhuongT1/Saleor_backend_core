import pytest
from unittest import mock
from saleor.checkout.complete_checkout import complete_checkout
from saleor.channel import MarkAsPaidStrategy
from ..fetch import fetch_checkout_info, fetch_checkout_lines
from ...plugins.manager import get_plugins_manager


@pytest.mark.django_db
@pytest.mark.parametrize(
    "email, paid_strategy", 
    [
        ("test@example.com", MarkAsPaidStrategy.TRANSACTION_FLOW), 
        ("test@example.com", MarkAsPaidStrategy.PAYMENT_FLOW), 
    ],
)
def test_associate_guest_checkout_with_account_if_exists(
    email, 
    paid_strategy, 
    app, 
    address, 
    checkout, 
    customer_user
):
    # set the checkout email
    checkout.email = email
    checkout.save()
    user = None
    manager = get_plugins_manager()
    lines, _ = fetch_checkout_lines(checkout)
    checkout_info = fetch_checkout_info(checkout, lines, [], manager)
    checkout_info.billing_address = address
    checkout_info.channel.order_mark_as_paid_strategy == paid_strategy

    # call the complete_checkout function with the checkout object
    order, _, _ = complete_checkout(
        checkout_info=checkout_info,
        manager=manager,
        lines=lines,
        payment_data={},
        store_source=False,
        user=user,
        app=app
    )

    # assert that the order is associated with the correct user
    assert order.user == customer_user


@pytest.mark.django_db
@pytest.mark.parametrize(
    "email, paid_strategy", 
    [
        ("guest@email.com", MarkAsPaidStrategy.TRANSACTION_FLOW),
        ("guest@email.com", MarkAsPaidStrategy.PAYMENT_FLOW),
    ],
)
def test_associate_guest_checkout_with_account_if_exists_with_guest_user(
    email, 
    paid_strategy, 
    app, 
    address, 
    checkout, 
):
    # set the checkout email
    checkout.email = email
    checkout.save()
    user = None
    manager = get_plugins_manager()
    lines, _ = fetch_checkout_lines(checkout)
    checkout_info = fetch_checkout_info(checkout, lines, [], manager)
    checkout_info.billing_address = address
    checkout_info.channel.order_mark_as_paid_strategy == paid_strategy

    # call the complete_checkout function with the checkout object
    order, _, _ = complete_checkout(
        checkout_info=checkout_info,
        manager=manager,
        lines=lines,
        payment_data={},
        store_source=False,
        user=user,
        app=app
    )

    # assert that the order is associated with the correct user
    assert order.user is None
    
