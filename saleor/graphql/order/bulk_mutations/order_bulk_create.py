import copy
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

import graphene
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import connection
from django.db.models import Q
from django.utils import timezone
from graphql import GraphQLError
from prices import Money

from ....account.models import Address, User
from ....app.models import App
from ....channel.models import Channel
from ....core import JobStatus
from ....core.prices import quantize_price
from ....core.tracing import traced_atomic_transaction
from ....core.utils.url import validate_storefront_url
from ....core.weight import zero_weight
from ....discount.models import OrderDiscount, Voucher
from ....giftcard.models import GiftCard
from ....invoice.models import Invoice
from ....order import (
    FulfillmentStatus,
    OrderEvents,
    OrderOrigin,
    OrderStatus,
    StockUpdatePolicy,
)
from ....order.error_codes import OrderBulkCreateErrorCode
from ....order.models import Fulfillment, FulfillmentLine, Order, OrderEvent, OrderLine
from ....order.utils import update_order_display_gross_prices
from ....payment.models import TransactionItem
from ....permission.enums import OrderPermissions
from ....product.models import ProductVariant
from ....shipping.models import ShippingMethod, ShippingMethodChannelListing
from ....tax.models import TaxClass
from ....warehouse.models import Stock, Warehouse
from ...account.i18n import I18nMixin
from ...account.types import AddressInput
from ...core import ResolveInfo
from ...core.descriptions import ADDED_IN_314, PREVIEW_FEATURE
from ...core.doc_category import DOC_CATEGORY_ORDERS
from ...core.enums import ErrorPolicy, ErrorPolicyEnum, LanguageCodeEnum
from ...core.mutations import BaseMutation
from ...core.scalars import PositiveDecimal, WeightScalar
from ...core.types import BaseInputObjectType, BaseObjectType, NonNullList
from ...core.types.common import OrderBulkCreateError
from ...core.utils import from_global_id_or_error
from ...meta.mutations import MetadataInput
from ...payment.mutations import TransactionCreate, TransactionCreateInput
from ...payment.utils import metadata_contains_empty_key
from ...plugins.dataloaders import get_plugin_manager_promise
from ..enums import OrderStatusEnum, StockUpdatePolicyEnum
from ..mutations.order_discount_common import (
    OrderDiscountCommon,
    OrderDiscountCommonInput,
)
from ..types import Order as OrderType
from .utils import get_instance

MINUTES_DIFF = 5
MAX_ORDERS = 50
MAX_NOTE_LENGTH = 200


@dataclass
class OrderBulkError:
    message: str
    code: Optional[OrderBulkCreateErrorCode] = None
    path: Optional[str] = None


@dataclass
class OrderBulkFulfillmentLine:
    line: FulfillmentLine
    warehouse: Warehouse


@dataclass
class OrderBulkFulfillment:
    fulfillment: Fulfillment
    lines: List[OrderBulkFulfillmentLine]


@dataclass
class OrderBulkOrderLine:
    line: OrderLine
    warehouse: Warehouse


@dataclass
class OrderBulkClass:
    order: Optional[Order]
    errors: List[OrderBulkError]
    lines: List[OrderBulkOrderLine]
    notes: List[OrderEvent]
    fulfillments: List[OrderBulkFulfillment]
    transactions: List[TransactionItem]
    invoices: List[Invoice]
    discounts: List[OrderDiscount]
    gift_cards: List[GiftCard]
    user: Optional[User] = None
    billing_address: Optional[Address] = None
    channel: Optional[Channel] = None
    shipping_address: Optional[Address] = None
    voucher: Optional[Voucher] = None
    # error which ignores error policy and disqualify order
    is_critical_error: bool = False

    def __init__(self):
        super().__init__()
        self.order = None
        self.errors = []
        self.lines = []
        self.notes = []
        self.fulfillments = []
        self.transactions = []
        self.invoices = []
        self.discounts = []
        self.gift_cards = []

    def set_fulfillment_id(self):
        for fulfillment in self.fulfillments:
            for line in fulfillment.lines:
                line.line.fulfillment_id = fulfillment.fulfillment.id

    def set_quantity_fulfilled(self):
        map = self.orderline_quantityfulfilled_map
        for order_line in self.lines:
            order_line.line.quantity_fulfilled = map.get(order_line.line.id, 0)

    def set_fulfillment_order(self):
        order = 1
        for fulfillment in self.fulfillments:
            fulfillment.fulfillment.fulfillment_order = order
            order += 1

    def link_gift_cards(self):
        if self.order:
            self.order.gift_cards.add(*self.gift_cards)

    @property
    def order_lines_duplicates(self) -> bool:
        keys = [
            f"{line.line.variant.id}_{line.warehouse.id}"
            for line in self.lines
            if line.line.variant
        ]
        return len(keys) != len(list(set(keys)))

    @property
    def all_order_lines(self) -> List[OrderLine]:
        return [line.line for line in self.lines]

    @property
    def all_fulfillment_lines(self) -> List[FulfillmentLine]:
        return [
            line.line for fulfillment in self.fulfillments for line in fulfillment.lines
        ]

    @property
    def all_transactions(self) -> List[TransactionItem]:
        return [transaction for transaction in self.transactions]

    @property
    def all_invoices(self) -> List[Invoice]:
        return [invoice for invoice in self.invoices]

    @property
    def all_discounts(self) -> List[OrderDiscount]:
        return [discount for discount in self.discounts]

    @property
    def orderline_fulfillmentlines_map(
        self,
    ) -> Dict[UUID, List[OrderBulkFulfillmentLine]]:
        map: Dict[UUID, list] = defaultdict(list)
        for fulfillment in self.fulfillments:
            for line in fulfillment.lines:
                map[line.line.order_line.id].append(line)
        return map

    @property
    def orderline_quantityfulfilled_map(self) -> Dict[UUID, int]:
        map: Dict[UUID, int] = defaultdict(int)
        for (
            order_line,
            fulfillment_lines,
        ) in self.orderline_fulfillmentlines_map.items():
            map[order_line] = sum([line.line.quantity for line in fulfillment_lines])
        return map

    @property
    def unique_variant_ids(self) -> List[int]:
        return list(
            set([line.line.variant.id for line in self.lines if line.line.variant])
        )

    @property
    def unique_warehouse_ids(self) -> List[UUID]:
        return list(set([line.warehouse.id for line in self.lines]))

    @property
    def total_order_quantity(self):
        return sum((line.line.quantity for line in self.lines))

    @property
    def total_fulfillment_quantity(self):
        return sum(
            (
                line.line.quantity
                for fulfillment in self.fulfillments
                for line in fulfillment.lines
            )
        )


@dataclass
class DeliveryMethod:
    warehouse: Optional[Warehouse]
    shipping_method: Optional[ShippingMethod]
    shipping_tax_class: Optional[TaxClass]
    shipping_tax_class_metadata: Optional[List[Dict[str, str]]]
    shipping_tax_class_private_metadata: Optional[List[Dict[str, str]]]


@dataclass
class OrderAmounts:
    shipping_price_gross: Decimal
    shipping_price_net: Decimal
    total_gross: Decimal
    total_net: Decimal
    undiscounted_total_gross: Decimal
    undiscounted_total_net: Decimal
    shipping_tax_rate: Decimal


@dataclass
class LineAmounts:
    total_gross: Decimal
    total_net: Decimal
    unit_gross: Decimal
    unit_net: Decimal
    undiscounted_total_gross: Decimal
    undiscounted_total_net: Decimal
    undiscounted_unit_gross: Decimal
    undiscounted_unit_net: Decimal
    quantity: int
    tax_rate: Decimal


class TaxedMoneyInput(BaseInputObjectType):
    gross = PositiveDecimal(required=True, description="Gross value of an item.")
    net = PositiveDecimal(required=True, description="Net value of an item.")

    class Meta:
        doc_category = DOC_CATEGORY_ORDERS


class OrderBulkCreateUserInput(BaseInputObjectType):
    id = graphene.ID(description="Customer ID associated with the order.")
    email = graphene.String(description="Customer email associated with the order.")
    external_reference = graphene.String(
        description="Customer external ID associated with the order."
    )

    class Meta:
        doc_category = DOC_CATEGORY_ORDERS


class OrderBulkCreateInvoiceInput(BaseInputObjectType):
    created_at = graphene.DateTime(
        required=True, description="The date, when the invoice was created."
    )
    number = graphene.String(description="Invoice number.")
    url = graphene.String(description="URL of the invoice to download.")
    metadata = NonNullList(MetadataInput, description="Metadata of the invoice.")
    private_metadata = NonNullList(
        MetadataInput, description="Private metadata of the invoice."
    )

    class Meta:
        doc_category = DOC_CATEGORY_ORDERS


class OrderBulkCreateDeliveryMethodInput(BaseInputObjectType):
    warehouse_id = graphene.ID(description="The ID of the warehouse.")
    warehouse_name = graphene.String(description="The name of the warehouse.")
    shipping_method_id = graphene.ID(description="The ID of the shipping method.")
    shipping_method_name = graphene.String(
        description="The name of the shipping method."
    )
    shipping_price = graphene.Field(
        TaxedMoneyInput, description="The price of the shipping."
    )
    shipping_tax_rate = PositiveDecimal(description="Tax rate of the shipping.")
    shipping_tax_class_id = graphene.ID(description="The ID of the tax class.")
    shipping_tax_class_name = graphene.String(description="The name of the tax class.")
    shipping_tax_class_metadata = NonNullList(
        MetadataInput, description="Metadata of the tax class."
    )
    shipping_tax_class_private_metadata = NonNullList(
        MetadataInput, description="Private metadata of the tax class."
    )

    class Meta:
        doc_category = DOC_CATEGORY_ORDERS


class OrderBulkCreateNoteInput(BaseInputObjectType):
    message = graphene.String(
        required=True, description=f"Note message. Max characters: {MAX_NOTE_LENGTH}."
    )
    date = graphene.DateTime(description="The date associated with the message.")
    user_id = graphene.ID(description="The user ID associated with the message.")
    user_email = graphene.ID(description="The user email associated with the message.")
    user_external_reference = graphene.ID(
        description="The user external ID associated with the message."
    )
    app_id = graphene.ID(description="The app ID associated with the message.")

    class Meta:
        doc_category = DOC_CATEGORY_ORDERS


class OrderBulkCreateFulfillmentLineInput(BaseInputObjectType):
    variant_id = graphene.ID(description="The ID of the product variant.")
    variant_sku = graphene.String(description="The SKU of the product variant.")
    variant_external_reference = graphene.String(
        description="The external ID of the product variant."
    )
    quantity = graphene.Int(
        description="The number of line items to be fulfilled from given warehouse.",
        required=True,
    )
    warehouse = graphene.ID(
        description="ID of the warehouse from which the item will be fulfilled.",
        required=True,
    )
    order_line_index = graphene.Int(
        required=True,
        description=(
            "0-based index of order line, which the fulfillment line refers to."
        ),
    )

    class Meta:
        doc_category = DOC_CATEGORY_ORDERS


class OrderBulkCreateFulfillmentInput(BaseInputObjectType):
    tracking_code = graphene.String(description="Fulfillments tracking code.")
    lines = NonNullList(
        OrderBulkCreateFulfillmentLineInput,
        description="List of items informing how to fulfill the order.",
    )

    class Meta:
        doc_category = DOC_CATEGORY_ORDERS


class OrderBulkCreateOrderLineInput(BaseInputObjectType):
    variant_id = graphene.ID(description="The ID of the product variant.")
    variant_sku = graphene.String(description="The SKU of the product variant.")
    variant_external_reference = graphene.String(
        description="The external ID of the product variant."
    )
    variant_name = graphene.String(description="The name of the product variant.")
    product_name = graphene.String(description="The name of the product.")
    translated_variant_name = graphene.String(
        description="Translation of the product variant name."
    )
    translated_product_name = graphene.String(
        description="Translation of the product name."
    )
    created_at = graphene.DateTime(
        required=True, description="The date, when the order line was created."
    )
    is_shipping_required = graphene.Boolean(
        required=True,
        description="Determines whether shipping of the order line items is required.",
    )
    is_gift_card = graphene.Boolean(required=True, description="Gift card flag.")
    quantity = graphene.Int(
        required=True, description="Number of items in the order line"
    )
    total_price = graphene.Field(
        TaxedMoneyInput, required=True, description="Price of the order line."
    )
    undiscounted_total_price = graphene.Field(
        TaxedMoneyInput,
        required=True,
        description="Price of the order line excluding applied discount.",
    )
    warehouse = graphene.ID(
        required=True,
        description="The ID of the warehouse, where the line will be allocated.",
    )
    metadata = NonNullList(MetadataInput, description="Metadata of the order line.")
    private_metadata = NonNullList(
        MetadataInput, description="Private metadata of the order line."
    )
    tax_rate = PositiveDecimal(description="Tax rate of the order line.")
    tax_class_id = graphene.ID(description="The ID of the tax class.")
    tax_class_name = graphene.String(description="The name of the tax class.")
    tax_class_metadata = NonNullList(
        MetadataInput, description="Metadata of the tax class."
    )
    tax_class_private_metadata = NonNullList(
        MetadataInput, description="Private metadata of the tax class."
    )

    class Meta:
        doc_category = DOC_CATEGORY_ORDERS


class OrderBulkCreateInput(BaseInputObjectType):
    number = graphene.String(description="Unique string identifier of the order.")
    external_reference = graphene.String(description="External ID of the order.")
    channel = graphene.String(
        required=True, description="Slug of the channel associated with the order."
    )
    created_at = graphene.DateTime(
        required=True,
        description="The date, when the order was inserted to Saleor database.",
    )
    status = OrderStatusEnum(description="Status of the order.")
    user = graphene.Field(
        OrderBulkCreateUserInput,
        required=True,
        description="Customer associated with the order.",
    )
    tracking_client_id = graphene.String(description="Tracking ID of the customer.")
    billing_address = graphene.Field(
        AddressInput, required=True, description="Billing address of the customer."
    )
    shipping_address = graphene.Field(
        AddressInput, description="Shipping address of the customer."
    )
    currency = graphene.String(required=True, description="Currency code.")
    metadata = NonNullList(MetadataInput, description="Metadata of the order.")
    private_metadata = NonNullList(
        MetadataInput, description="Private metadata of the order."
    )
    customer_note = graphene.String(description="Note about customer.")
    notes = NonNullList(
        OrderBulkCreateNoteInput,
        description="Notes related to the order.",
    )
    language_code = graphene.Argument(
        LanguageCodeEnum, required=True, description="Order language code."
    )
    display_gross_prices = graphene.Boolean(
        description="Determines whether checkout prices should include taxes, "
        "when displayed in a storefront.",
    )
    weight = WeightScalar(description="Weight of the order in kg.")
    redirect_url = graphene.String(
        description="URL of a view, where users should be redirected "
        "to see the order details.",
    )
    lines = NonNullList(
        OrderBulkCreateOrderLineInput, required=True, description="List of order lines."
    )
    delivery_method = graphene.Field(
        OrderBulkCreateDeliveryMethodInput,
        required=True,
        description="The delivery method selected for this order.",
    )
    gift_cards = NonNullList(
        graphene.String,
        description="List of gift card codes associated with the order.",
    )
    voucher = graphene.String(
        description="Code of a voucher associated with the order."
    )
    discounts = NonNullList(OrderDiscountCommonInput, description="List of discounts.")
    fulfillments = NonNullList(
        OrderBulkCreateFulfillmentInput, description="Fulfillments of the order."
    )
    transactions = NonNullList(
        TransactionCreateInput, description="Transactions related to the order."
    )
    invoices = NonNullList(
        OrderBulkCreateInvoiceInput, description="Invoices related to the order."
    )

    class Meta:
        doc_category = DOC_CATEGORY_ORDERS


class OrderBulkCreateResult(BaseObjectType):
    order = graphene.Field(OrderType, description="Order data.")
    errors = NonNullList(
        OrderBulkCreateError,
        description="List of errors occurred on create attempt.",
    )

    class Meta:
        doc_category = DOC_CATEGORY_ORDERS


class OrderBulkCreate(BaseMutation, I18nMixin):
    count = graphene.Int(
        required=True,
        default_value=0,
        description="Returns how many objects were created.",
    )
    results = NonNullList(
        OrderBulkCreateResult,
        required=True,
        default_value=[],
        description="List of the created orders.",
    )

    class Arguments:
        orders = NonNullList(
            OrderBulkCreateInput,
            required=True,
            description=f"Input list of orders to create. Orders limit: {MAX_ORDERS}.",
        )
        error_policy = ErrorPolicyEnum(
            required=False,
            description=(
                "Policies of error handling. DEFAULT: "
                + ErrorPolicyEnum.REJECT_EVERYTHING.name
            ),
        )
        stock_update_policy = StockUpdatePolicyEnum(
            required=False,
            description=(
                "Determine how stock should be updated, while processing the order. "
                "DEFAULT: UPDATE - Only do update, if there is enough stocks."
            ),
        )

    class Meta:
        description = "Creates multiple orders." + ADDED_IN_314 + PREVIEW_FEATURE
        permissions = (OrderPermissions.MANAGE_ORDERS_IMPORT,)
        doc_category = DOC_CATEGORY_ORDERS
        error_type_class = OrderBulkCreateError
        support_meta_field = True
        support_private_meta_field = True

    @classmethod
    def get_all_instances(cls, orders_input) -> Dict[str, Any]:
        """Retrieve all required instances to process orders.

        Return:
            Dictionary with keys "{model_name}.{key_name}.{key_value}" and model
            instances as values.

        """
        # Collect all model keys from input
        keys = defaultdict(list)
        for order in orders_input:
            keys["User.id"].append(order["user"].get("id"))
            keys["User.email"].append(order["user"].get("email"))
            keys["User.external_reference"].append(
                order["user"].get("external_reference")
            )
            keys["Channel.slug"].append(order.get("channel"))
            keys["Voucher.code"].append(order.get("voucher"))
            keys["Warehouse.id"].append(order["delivery_method"].get("warehouse_id"))
            keys["ShippingMethod.id"].append(
                order["delivery_method"].get("shipping_method_id")
            )
            keys["TaxClass.id"].append(
                order["delivery_method"].get("shipping_tax_class_id")
            )
            keys["Order.number"].append(order.get("number"))
            for note in order["notes"]:
                keys["User.id"].append(note.get("user_id"))
                keys["User.email"].append(note.get("user_email"))
                keys["User.external_reference"].append(
                    note.get("user_external_reference")
                )
                keys["App.id"].append(note.get("app_id"))
            for order_line in order["lines"]:
                keys["ProductVariant.id"].append(order_line.get("variant_id"))
                keys["ProductVariant.sku"].append(order_line.get("variant_sku"))
                keys["ProductVariant.external_reference"].append(
                    order_line.get("variant_external_reference")
                )
                keys["Warehouse.id"].append(order_line.get("warehouse"))
                keys["TaxClass.id"].append(order_line.get("tax_class_id"))
            for fulfillment in order["fulfillments"]:
                for line in fulfillment["lines"]:
                    keys["ProductVariant.id"].append(line.get("variant_id"))
                    keys["ProductVariant.sku"].append(line.get("variant_sku"))
                    keys["ProductVariant.external_reference"].append(
                        line.get("variant_external_reference")
                    )
                    keys["Warehouse.id"].append(line.get("warehouse"))
            for gift_card_code in order["gift_cards"]:
                keys["GiftCard.code"].append(gift_card_code)

        # Convert global ids to model ids and get rid of Nones
        for key, values in keys.items():
            keys[key] = [value for value in values if value is not None]
            object_type, key_field = key.split(".")
            if key_field == "id":
                model_ids = []
                for global_id in values:
                    try:
                        _, id = from_global_id_or_error(
                            str(global_id), object_type, raise_error=True
                        )
                        model_ids.append(id)
                    except GraphQLError:
                        pass
                keys[key] = model_ids

        # Make API calls
        users = User.objects.filter(
            Q(pk__in=keys["User.id"])
            | Q(email__in=keys["User.email"])
            | Q(external_reference__in=keys["User.external_reference"])
        )
        variants = ProductVariant.objects.filter(
            Q(pk__in=keys["ProductVariant.id"])
            | Q(sku__in=keys["ProductVariant.sku"])
            | Q(external_reference__in=keys["ProductVariant.external_reference"])
        )
        channels = Channel.objects.filter(slug__in=keys["Channel.slug"])
        vouchers = Voucher.objects.filter(code__in=keys["Voucher.code"])
        warehouses = Warehouse.objects.filter(pk__in=keys["Warehouse.id"])
        shipping_methods = ShippingMethod.objects.filter(
            pk__in=keys["ShippingMethod.id"]
        )
        tax_classes = TaxClass.objects.filter(pk__in=keys["TaxClass.id"])
        apps = App.objects.filter(pk__in=keys["App.id"])
        gift_cards = GiftCard.objects.filter(code__in=keys["GiftCard.code"])
        orders = Order.objects.filter(number__in=keys["Order.number"])

        # Create dictionary
        object_storage: Dict[str, Any] = {}
        for user in users:
            object_storage[f"User.id.{user.id}"] = user
            object_storage[f"User.email.{user.email}"] = user
            if user.external_reference:
                object_storage[
                    f"User.external_reference.{user.external_reference}"
                ] = user

        for variant in variants:
            object_storage[f"ProductVariant.id.{variant.id}"] = variant
            if variant.sku:
                object_storage[f"ProductVariant.id.{variant.sku}"] = variant
            if variant.external_reference:
                object_storage[
                    f"ProductVariant.external_reference.{variant.external_reference}"
                ] = variant

        for channel in channels:
            object_storage[f"Channel.slug.{channel.slug}"] = channel

        for voucher in vouchers:
            object_storage[f"Voucher.code.{voucher.code}"] = voucher

        for gift_card in gift_cards:
            object_storage[f"GiftCard.code.{gift_card.code}"] = gift_card

        for order in orders:
            object_storage[f"Order.number.{order.number}"] = order

        for object in [*warehouses, *shipping_methods, *tax_classes, *apps]:
            object_storage[f"{object.__class__.__name__}.id.{object.pk}"] = object

        return object_storage

    @classmethod
    def is_datetime_valid(cls, date: datetime) -> bool:
        """We accept future time values with 5 minutes from current time.

        Some systems might have incorrect time that is in the future compared to Saleor.
        At the same time, we don't want to create orders that are too far in the future.
        """
        return date < timezone.now() + timedelta(minutes=MINUTES_DIFF)

    @classmethod
    def validate_order_input(
        cls, order_input, order: OrderBulkClass, object_storage: Dict[str, Any]
    ):
        date = order_input.get("created_at")
        if date and not cls.is_datetime_valid(date):
            order.errors.append(
                OrderBulkError(
                    message="Order input contains future date.",
                    path="created_at",
                    code=OrderBulkCreateErrorCode.FUTURE_DATE,
                )
            )

        if redirect_url := order_input.get("redirect_url"):
            try:
                validate_storefront_url(redirect_url)
            except ValidationError as err:
                order.errors.append(
                    OrderBulkError(
                        message=f"Invalid redirect url: {err.message}.",
                        path="redirect_url",
                        code=OrderBulkCreateErrorCode.INVALID,
                    )
                )

        weight = order_input.get("weight")
        if weight and weight.value < 0:
            order.errors.append(
                OrderBulkError(
                    message="Order can't have negative weight.",
                    path="weight",
                    code=OrderBulkCreateErrorCode.INVALID,
                )
            )

        if number := order_input.get("number"):
            lookup_key = f"Order.number.{number}"
            if object_storage.get(lookup_key):
                order.errors.append(
                    OrderBulkError(
                        message=f"Order with number: {number} already exists.",
                        path="number",
                        code=OrderBulkCreateErrorCode.UNIQUE,
                    )
                )
                order.is_critical_error = True

    @classmethod
    def validate_order_status(cls, status: str, order: OrderBulkClass):
        total_order_quantity = order.total_order_quantity
        total_fulfillment_quantity = order.total_fulfillment_quantity

        is_invalid = False
        if total_fulfillment_quantity == 0 and status in [
            OrderStatus.PARTIALLY_FULFILLED,
            OrderStatus.FULFILLED,
        ]:
            is_invalid = True
        if (
            total_fulfillment_quantity > 0
            and (total_order_quantity - total_fulfillment_quantity) > 0
            and status in [OrderStatus.FULFILLED, OrderStatus.UNFULFILLED]
        ):
            is_invalid = True
        if total_order_quantity == total_fulfillment_quantity and status in [
            OrderStatus.PARTIALLY_FULFILLED,
            OrderStatus.UNFULFILLED,
        ]:
            is_invalid = True

        if is_invalid:
            order.errors.append(
                OrderBulkError(
                    message="Invalid order status.",
                    path="status",
                    code=OrderBulkCreateErrorCode.INVALID,
                )
            )

    @classmethod
    def validate_order_numbers(cls, orders: List[OrderBulkClass]):
        numbers = []
        for order in orders:
            if order.order and order.order.number in numbers:
                order.errors.append(
                    OrderBulkError(
                        message=f"Input contains multiple orders with number:"
                        f" {order.order.number}.",
                        path="number",
                        code=OrderBulkCreateErrorCode.UNIQUE,
                    )
                )
                order.order = None
            elif order.order:
                numbers.append(order.order.number)

        int_numbers = [int(number) for number in numbers if number and number.isdigit()]
        if int_numbers:
            with connection.cursor() as cursor:
                cursor.execute("SELECT currval('order_order_number_seq')")
                curr_number = cursor.fetchone()[0]
                int_numbers.append(curr_number)
                max_number = max(int_numbers)
                cursor.execute(f"SELECT setval('order_order_number_seq', {max_number})")

    @classmethod
    def process_metadata(
        cls,
        metadata: List[Dict[str, str]],
        errors: List[OrderBulkError],
        path: str,
        field: Any,
    ):
        if metadata_contains_empty_key(metadata):
            errors.append(
                OrderBulkError(
                    message="Metadata key cannot be empty.",
                    path=path,
                    code=OrderBulkCreateErrorCode.METADATA_KEY_REQUIRED,
                )
            )
            metadata = [data for data in metadata if data["key"].strip() != ""]
        for data in metadata:
            field.update({data["key"]: data["value"]})

    @classmethod
    def get_instance_with_errors(
        cls,
        input: Dict[str, Any],
        model,
        key_map: Dict[str, str],
        errors: List[OrderBulkError],
        object_storage: Dict[str, Any],
        path: str = "",
    ):
        """Resolve instance based on input data, model and `key_map` argument provided.

        Args:
            input: data from input
            model: database model associated with searched instance
            key_map: mapping between keys from input and keys from database
            errors: error list to be updated if an error occur
            object_storage: dict with key pattern: {model_name}_{key_name}_{key_value}
                              and instances as values; it is used to search for already
                              resolved instances
            path: path to input field, which caused an error

        Return:
            model instance

        """
        instance = None
        try:
            instance = get_instance(
                input, model, key_map, object_storage, OrderBulkCreateErrorCode, path
            )
        except ValidationError as err:
            errors.append(
                OrderBulkError(
                    message=str(err.message),
                    code=OrderBulkCreateErrorCode(err.code),
                    path=err.params["path"] if err.params else None,
                )
            )
        return instance

    @classmethod
    def get_instances_related_to_order(
        cls,
        order_input: Dict[str, Any],
        order: OrderBulkClass,
        object_storage: Dict[str, Any],
    ):
        """Get all instances of objects needed to create an order."""
        user = cls.get_instance_with_errors(
            input=order_input["user"],
            errors=order.errors,
            model=User,
            key_map={
                "id": "id",
                "email": "email",
                "external_reference": "external_reference",
            },
            object_storage=object_storage,
            path="user",
        )

        # If user can't be found, but email is provided, consider it as valid.
        if (
            not user
            and order.errors[-1].code == OrderBulkCreateErrorCode.NOT_FOUND
            and order_input["user"].get("email")
        ):
            order.errors.pop()

        channel = cls.get_instance_with_errors(
            input=order_input,
            errors=order.errors,
            model=Channel,
            key_map={"channel": "slug"},
            object_storage=object_storage,
        )

        billing_address: Optional[Address] = None
        billing_address_input = order_input["billing_address"]
        try:
            billing_address = cls.validate_address(billing_address_input)
        except Exception:
            order.errors.append(
                OrderBulkError(
                    message="Invalid billing address.",
                    path="billing_address",
                    code=OrderBulkCreateErrorCode.INVALID,
                )
            )

        shipping_address: Optional[Address] = None
        if shipping_address_input := order_input.get("shipping_address"):
            try:
                shipping_address = cls.validate_address(shipping_address_input)
            except Exception:
                order.errors.append(
                    OrderBulkError(
                        message="Invalid shipping address.",
                        path="shipping_address",
                        code=OrderBulkCreateErrorCode.INVALID,
                    )
                )

        voucher = None
        if order_input.get("voucher"):
            voucher = cls.get_instance_with_errors(
                input=order_input,
                errors=order.errors,
                model=Voucher,
                key_map={"voucher": "code"},
                object_storage=object_storage,
            )

        code_index = 0
        for code in order_input.get("gift_cards", []):
            key = f"GiftCard.code.{code}"
            if gift_card := object_storage.get(key):
                order.gift_cards.append(gift_card)
                code_index += 1
            else:
                order.errors.append(
                    OrderBulkError(
                        message=f"Gift card with code {code} doesn't exist.",
                        code=OrderBulkCreateErrorCode.NOT_FOUND,
                        path=f"gift_cards.[{code_index}]",
                    )
                )

        order.user = user
        order.channel = channel
        order.billing_address = billing_address
        order.shipping_address = shipping_address
        order.voucher = voucher
        return

    @classmethod
    def make_order_line_calculations(
        cls,
        line_input: Dict[str, Any],
        errors: List[OrderBulkError],
        currency: str,
        index: int,
    ) -> Optional[LineAmounts]:
        gross_amount = line_input["total_price"]["gross"]
        net_amount = line_input["total_price"]["net"]
        undiscounted_gross_amount = line_input["undiscounted_total_price"]["gross"]
        undiscounted_net_amount = line_input["undiscounted_total_price"]["net"]
        quantity = line_input["quantity"]
        tax_rate = line_input.get("tax_rate", None)

        is_exit_error = False
        if quantity < 1 or int(quantity) != quantity:
            errors.append(
                OrderBulkError(
                    message="Invalid quantity. "
                    "Must be integer greater then or equal to 1.",
                    path=f"lines.[{index}].quantity",
                    code=OrderBulkCreateErrorCode.INVALID_QUANTITY,
                )
            )
            is_exit_error = True
        if gross_amount < net_amount:
            errors.append(
                OrderBulkError(
                    message="Net price can't be greater then gross price.",
                    path=f"lines.[{index}].total_price",
                    code=OrderBulkCreateErrorCode.PRICE_ERROR,
                )
            )
            is_exit_error = True
        if undiscounted_gross_amount < undiscounted_net_amount:
            errors.append(
                OrderBulkError(
                    message="Net price can't be greater then gross price.",
                    path=f"lines.[{index}].undiscounted_total_price",
                    code=OrderBulkCreateErrorCode.PRICE_ERROR,
                )
            )
            is_exit_error = True

        if is_exit_error:
            return None

        unit_price_net_amount = quantize_price(Decimal(net_amount / quantity), currency)
        unit_price_gross_amount = quantize_price(
            Decimal(gross_amount / quantity), currency
        )
        undiscounted_unit_price_net_amount = quantize_price(
            Decimal(undiscounted_net_amount / quantity), currency
        )
        undiscounted_unit_price_gross_amount = quantize_price(
            Decimal(undiscounted_gross_amount / quantity), currency
        )

        if tax_rate is None and net_amount > 0:
            tax_rate = Decimal(gross_amount / net_amount - 1)

        return LineAmounts(
            total_gross=gross_amount,
            total_net=net_amount,
            unit_gross=unit_price_gross_amount,
            unit_net=unit_price_net_amount,
            undiscounted_total_gross=undiscounted_gross_amount,
            undiscounted_total_net=undiscounted_net_amount,
            undiscounted_unit_gross=undiscounted_unit_price_gross_amount,
            undiscounted_unit_net=undiscounted_unit_price_net_amount,
            quantity=quantity,
            tax_rate=tax_rate,
        )

    @classmethod
    def make_order_calculations(
        cls,
        delivery_method: DeliveryMethod,
        order_lines: List[OrderLine],
        channel: Channel,
        delivery_input: Dict[str, Any],
        object_storage: Dict[str, Any],
    ) -> OrderAmounts:
        """Calculate all order amount fields."""

        # Calculate shipping amounts
        shipping_price_net_amount = Decimal(0)
        shipping_price_gross_amount = Decimal(0)
        shipping_price_tax_rate = Decimal(delivery_input.get("shipping_tax_rate", 0))

        if delivery_method.shipping_method:
            if shipping_price := delivery_input.get("shipping_price"):
                shipping_price_net_amount = Decimal(shipping_price.net)
                shipping_price_gross_amount = Decimal(shipping_price.gross)
                shipping_price_tax_rate = (
                    shipping_price_gross_amount / shipping_price_net_amount - 1
                )
            else:
                lookup_key = f"shipping_price.{delivery_method.shipping_method.id}"
                db_price_amount = object_storage.get(lookup_key) or (
                    ShippingMethodChannelListing.objects.values_list(
                        "price_amount", flat=True
                    )
                    .filter(
                        shipping_method_id=delivery_method.shipping_method.id,
                        channel_id=channel.id,
                    )
                    .first()
                )
                if db_price_amount:
                    shipping_price_net_amount = Decimal(db_price_amount)
                    shipping_price_gross_amount = Decimal(
                        shipping_price_net_amount * (1 + shipping_price_tax_rate)
                    )
                    object_storage[lookup_key] = db_price_amount

        # Calculate lines
        order_total_gross_amount = Decimal(
            sum((line.total_price_gross_amount for line in order_lines))
        )
        order_undiscounted_total_gross_amount = Decimal(
            sum((line.undiscounted_total_price_gross_amount for line in order_lines))
        )
        order_total_net_amount = Decimal(
            sum((line.total_price_net_amount for line in order_lines))
        )
        order_undiscounted_total_net_amount = Decimal(
            sum((line.undiscounted_total_price_net_amount for line in order_lines))
        )

        return OrderAmounts(
            shipping_price_gross=shipping_price_gross_amount,
            shipping_price_net=shipping_price_net_amount,
            shipping_tax_rate=shipping_price_tax_rate,
            total_gross=order_total_gross_amount,
            total_net=order_total_net_amount,
            undiscounted_total_gross=order_undiscounted_total_gross_amount,
            undiscounted_total_net=order_undiscounted_total_net_amount,
        )

    @classmethod
    def get_delivery_method(
        cls, input: Dict[str, Any], errors: List[OrderBulkError], object_storage
    ) -> Optional[DeliveryMethod]:
        warehouse, shipping_method, shipping_tax_class = None, None, None
        shipping_tax_class_metadata, shipping_tax_class_private_metadata = None, None
        is_warehouse_delivery = input.get("warehouse_id")
        is_shipping_delivery = input.get("shipping_method_id")

        if is_warehouse_delivery and is_shipping_delivery:
            errors.append(
                OrderBulkError(
                    message="Can't provide both warehouse and shipping method IDs.",
                    path="delivery_method",
                    code=OrderBulkCreateErrorCode.TOO_MANY_IDENTIFIERS,
                )
            )

        if is_warehouse_delivery:
            warehouse = cls.get_instance_with_errors(
                input=input,
                errors=errors,
                model=Warehouse,
                key_map={"warehouse_id": "id"},
                object_storage=object_storage,
                path="delivery_method",
            )

        if is_shipping_delivery:
            shipping_method = cls.get_instance_with_errors(
                input=input,
                errors=errors,
                model=ShippingMethod,
                key_map={"shipping_method_id": "id"},
                object_storage=object_storage,
                path="delivery_method",
            )
            shipping_tax_class = cls.get_instance_with_errors(
                input=input,
                errors=errors,
                model=TaxClass,
                key_map={"shipping_tax_class_id": "id"},
                object_storage=object_storage,
                path="delivery_method",
            )
            shipping_tax_class_metadata = input.get("shipping_tax_class_metadata")
            shipping_tax_class_private_metadata = input.get(
                "shipping_tax_class_private_metadata"
            )

        delivery_method = None
        if not warehouse and not shipping_method:
            errors.append(
                OrderBulkError(
                    message="No delivery method provided.",
                    path="delivery_method",
                    code=OrderBulkCreateErrorCode.REQUIRED,
                )
            )
        else:
            delivery_method = DeliveryMethod(
                warehouse=warehouse,
                shipping_method=shipping_method,
                shipping_tax_class=shipping_tax_class,
                shipping_tax_class_metadata=shipping_tax_class_metadata,
                shipping_tax_class_private_metadata=shipping_tax_class_private_metadata,
            )

        return delivery_method

    @classmethod
    def create_single_note(
        cls,
        note_input,
        order: OrderBulkClass,
        object_storage: Dict[str, Any],
        index: int,
    ) -> Optional[OrderEvent]:
        if len(note_input["message"]) > MAX_NOTE_LENGTH:
            order.errors.append(
                OrderBulkError(
                    message=f"Note message exceeds character limit: {MAX_NOTE_LENGTH}.",
                    path=f"notes.[{index}].message",
                    code=OrderBulkCreateErrorCode.NOTE_LENGTH,
                )
            )
            return None

        date = note_input.get("date")
        if date and not cls.is_datetime_valid(date):
            order.errors.append(
                OrderBulkError(
                    message="Note input contains future date.",
                    path=f"notes.[{index}].date",
                    code=OrderBulkCreateErrorCode.FUTURE_DATE,
                )
            )
            date = timezone.now()

        user, app = None, None
        user_key_map = {
            "user_id": "id",
            "user_email": "email",
            "user_external_reference": "external_reference",
        }
        if any([note_input.get(key) for key in user_key_map.keys()]):
            user = cls.get_instance_with_errors(
                input=note_input,
                errors=order.errors,
                model=User,
                key_map=user_key_map,
                object_storage=object_storage,
                path=f"notes.[{index}]",
            )

        if note_input.get("app_id"):
            app = cls.get_instance_with_errors(
                input=note_input,
                errors=order.errors,
                model=App,
                key_map={"app_id": "id"},
                object_storage=object_storage,
                path=f"notes.[{index}]",
            )

        if user and app:
            user, app = None, None
            order.errors.append(
                OrderBulkError(
                    message="Note input contains both user and app identifier.",
                    code=OrderBulkCreateErrorCode.TOO_MANY_IDENTIFIERS,
                    path=f"notes.[{index}]",
                )
            )

        event = OrderEvent(
            date=date,
            type=OrderEvents.NOTE_ADDED,
            order=order.order,
            parameters={"message": note_input["message"]},
            user=user,
            app=app,
        )

        return event

    @classmethod
    def create_single_discount(
        cls,
        discount_input: Dict[str, Any],
        order: OrderBulkClass,
        order_amounts: OrderAmounts,
        currency: str,
        index: int,
    ) -> OrderDiscount:
        max_total = Money(order_amounts.undiscounted_total_gross, currency)
        try:
            OrderDiscountCommon.validate_order_discount_input(max_total, discount_input)
        except ValidationError as err:
            order.errors.append(
                OrderBulkError(
                    message=err.messages[0],
                    path=f"discounts.[{index}]",
                    code=OrderBulkCreateErrorCode.INVALID,
                )
            )

        return OrderDiscount(
            order=order.order,
            value_type=discount_input["value_type"],
            value=discount_input["value"],
            reason=discount_input.get("reason"),
        )

    @classmethod
    def create_single_invoice(
        cls,
        invoice_input: Dict[str, Any],
        order: OrderBulkClass,
        index: int,
    ) -> Invoice:
        created_at = invoice_input["created_at"]
        if not cls.is_datetime_valid(created_at):
            order.errors.append(
                OrderBulkError(
                    message="Invoice input contains future date.",
                    path=f"invoices.[{index}].created_at",
                    code=OrderBulkCreateErrorCode.FUTURE_DATE,
                )
            )
            created_at = None

        if url := invoice_input.get("url"):
            try:
                URLValidator()(url)
            except ValidationError:
                order.errors.append(
                    OrderBulkError(
                        message="Invalid URL format.",
                        path=f"invoices.[{index}].url",
                        code=OrderBulkCreateErrorCode.INVALID,
                    )
                )
                url = None

        invoice = Invoice(
            order=order.order,
            number=invoice_input.get("number"),
            status=JobStatus.SUCCESS,
            external_url=url,
            created_at=created_at,
        )

        if metadata := invoice_input.get("metadata"):
            cls.process_metadata(
                metadata=metadata,
                errors=order.errors,
                path=f"invoices.[{index}].metadata",
                field=invoice.metadata,
            )
        if private_metadata := invoice_input.get("private_metadata"):
            cls.process_metadata(
                metadata=private_metadata,
                errors=order.errors,
                path=f"invoices.[{index}].private_metadata",
                field=invoice.private_metadata,
            )

        return invoice

    @classmethod
    def create_single_order_line(
        cls,
        order_line_input: Dict[str, Any],
        order: OrderBulkClass,
        object_storage,
        order_input: Dict[str, Any],
        index: int,
    ) -> Optional[OrderBulkOrderLine]:
        variant = cls.get_instance_with_errors(
            input=order_line_input,
            errors=order.errors,
            model=ProductVariant,
            key_map={
                "variant_id": "id",
                "variant_external_reference": "external_reference",
                "variant_sku": "sku",
            },
            object_storage=object_storage,
            path=f"lines.[{index}]",
        )
        if not variant:
            return None

        warehouse = cls.get_instance_with_errors(
            input=order_line_input,
            errors=order.errors,
            model=Warehouse,
            key_map={"warehouse": "id"},
            object_storage=object_storage,
            path=f"lines.[{index}]",
        )
        if not warehouse:
            return None

        line_tax_class = cls.get_instance_with_errors(
            input=order_line_input,
            errors=order.errors,
            model=TaxClass,
            key_map={"tax_class_id": "id"},
            object_storage=object_storage,
            path=f"lines.[{index}]",
        )
        tax_class_name = order_line_input.get(
            "tax_class_name", line_tax_class.name if line_tax_class else None
        )

        line_amounts = cls.make_order_line_calculations(
            order_line_input, order.errors, order_input["currency"], index
        )
        if not line_amounts:
            return None

        if not cls.is_datetime_valid(order_line_input["created_at"]):
            order.errors.append(
                OrderBulkError(
                    message="Order line input contains future date.",
                    path=f"lines.[{index}].created_at",
                    code=OrderBulkCreateErrorCode.FUTURE_DATE,
                )
            )

        order_line = OrderLine(
            order=order.order,
            variant=variant,
            product_name=order_line_input.get("product_name", ""),
            variant_name=order_line_input.get("variant_name", variant.name),
            translated_product_name=order_line_input.get("translated_product_name", ""),
            translated_variant_name=order_line_input.get("translated_variant_name", ""),
            product_variant_id=variant.get_global_id(),
            created_at=order_line_input["created_at"],
            is_shipping_required=order_line_input["is_shipping_required"],
            is_gift_card=order_line_input["is_gift_card"],
            currency=order_input["currency"],
            quantity=line_amounts.quantity,
            unit_price_net_amount=line_amounts.unit_net,
            unit_price_gross_amount=line_amounts.unit_gross,
            total_price_net_amount=line_amounts.total_net,
            total_price_gross_amount=line_amounts.total_gross,
            undiscounted_unit_price_net_amount=line_amounts.undiscounted_unit_net,
            undiscounted_unit_price_gross_amount=line_amounts.undiscounted_unit_gross,
            undiscounted_total_price_net_amount=line_amounts.undiscounted_total_net,
            undiscounted_total_price_gross_amount=line_amounts.undiscounted_total_gross,
            tax_rate=line_amounts.tax_rate,
            tax_class=line_tax_class,
            tax_class_name=tax_class_name,
        )

        if metadata := order_line_input.get("metadata"):
            cls.process_metadata(
                metadata=metadata,
                errors=order.errors,
                path=f"lines.[{index}].metadata",
                field=order_line.metadata,
            )
        if private_metadata := order_line_input.get("private_metadata"):
            cls.process_metadata(
                metadata=private_metadata,
                errors=order.errors,
                path=f"lines.[{index}].private_metadata",
                field=order_line.private_metadata,
            )
        if tax_class_metadata := order_line_input.get("tax_class_metadata"):
            cls.process_metadata(
                metadata=tax_class_metadata,
                errors=order.errors,
                path=f"lines.[{index}].tax_class_metadata",
                field=order_line.tax_class_metadata,
            )
        if tax_class_private_metadata := order_line_input.get(
            "tax_class_private_metadata"
        ):
            cls.process_metadata(
                metadata=tax_class_private_metadata,
                errors=order.errors,
                path=f"lines.[{index}].tax_class_private_metadata",
                field=order_line.tax_class_private_metadata,
            )

        return OrderBulkOrderLine(line=order_line, warehouse=warehouse)

    @classmethod
    def create_single_fulfillment(
        cls,
        fulfillment_input: Dict[str, Any],
        order_lines: List[OrderBulkOrderLine],
        order: OrderBulkClass,
        object_storage: Dict[str, Any],
        index: int,
    ) -> Optional[OrderBulkFulfillment]:
        fulfillment = Fulfillment(
            order=order.order,
            status=FulfillmentStatus.FULFILLED,
            tracking_number=fulfillment_input.get("tracking_code", ""),
            fulfillment_order=1,
        )

        lines_input = fulfillment_input["lines"]
        lines: List[OrderBulkFulfillmentLine] = []
        line_index = 0
        for line_input in lines_input:
            path = f"fulfillments.[{index}].lines.[{line_index}]"
            variant = cls.get_instance_with_errors(
                input=line_input,
                errors=order.errors,
                model=ProductVariant,
                key_map={
                    "variant_id": "id",
                    "variant_external_reference": "external_reference",
                    "variant_sku": "sku",
                },
                object_storage=object_storage,
                path=path,
            )
            if not variant:
                return None

            warehouse = cls.get_instance_with_errors(
                input=line_input,
                errors=order.errors,
                model=Warehouse,
                key_map={"warehouse": "id"},
                object_storage=object_storage,
                path=path,
            )
            if not warehouse:
                return None

            order_line_index = line_input["order_line_index"]
            if order_line_index < 0:
                order.errors.append(
                    OrderBulkError(
                        message="Order line index can't be negative.",
                        path=f"{path}.order_line_index",
                        code=OrderBulkCreateErrorCode.NEGATIVE_INDEX,
                    )
                )
                return None

            try:
                order_line = order_lines[order_line_index]
            except IndexError:
                order.errors.append(
                    OrderBulkError(
                        message=f"There is no order line with index:"
                        f" {order_line_index}.",
                        path=f"{path}.order_line_index",
                        code=OrderBulkCreateErrorCode.NO_RELATED_ORDER_LINE,
                    )
                )
                return None

            if order_line.warehouse.id != warehouse.id:
                code = OrderBulkCreateErrorCode.ORDER_LINE_FULFILLMENT_LINE_MISMATCH
                order.errors.append(
                    OrderBulkError(
                        message="Fulfillment line's warehouse is different"
                        " then order line's warehouse.",
                        path=f"{path}.warehouse",
                        code=code,
                    )
                )
                return None

            if order_line.line.variant.id != variant.id:
                code = OrderBulkCreateErrorCode.ORDER_LINE_FULFILLMENT_LINE_MISMATCH
                order.errors.append(
                    OrderBulkError(
                        message="Fulfillment line's product variant is different"
                        " then order line's product variant.",
                        path=f"{path}.variant_id",
                        code=code,
                    )
                )
                return None

            fulfillment_line = FulfillmentLine(
                fulfillment=fulfillment,
                order_line=order_line.line,
                quantity=line_input["quantity"],
            )
            lines.append(OrderBulkFulfillmentLine(fulfillment_line, warehouse))
            line_index += 1

        return OrderBulkFulfillment(fulfillment=fulfillment, lines=lines)

    @classmethod
    def create_single_order(
        cls, order_input, object_storage: Dict[str, Any]
    ) -> OrderBulkClass:
        order = OrderBulkClass()
        cls.validate_order_input(order_input, order, object_storage)
        if order.is_critical_error:
            return order

        order.order = Order(currency=order_input["currency"])
        # get order related instances
        cls.get_instances_related_to_order(
            order_input=order_input,
            order=order,
            object_storage=object_storage,
        )
        delivery_input = order_input["delivery_method"]
        delivery_method = cls.get_delivery_method(
            input=delivery_input,
            errors=order.errors,
            object_storage=object_storage,
        )
        if not (
            delivery_method
            and (order.user or order_input["user"].get("email"))
            and order.channel
            and order.billing_address
        ):
            order.order = None
            return order

        # create lines
        order_lines_input = order_input["lines"]
        order_line_index = 0
        for order_line_input in order_lines_input:
            if order_line := cls.create_single_order_line(
                order_line_input, order, object_storage, order_input, order_line_index
            ):
                order.lines.append(order_line)
            else:
                order.is_critical_error = True
            order_line_index += 1

        if order.is_critical_error:
            order.order = None
            return order

        # calculate order amounts
        order_amounts = cls.make_order_calculations(
            delivery_method,
            order.all_order_lines,
            order.channel,
            delivery_input,
            object_storage,
        )

        # create notes
        if notes_input := order_input.get("notes"):
            note_index = 0
            for note_input in notes_input:
                if note := cls.create_single_note(
                    note_input, order, object_storage, note_index
                ):
                    order.notes.append(note)
                note_index += 1

        # create fulfillments
        if fulfillments_input := order_input.get("fulfillments"):
            fulfillment_index = 0
            for fulfillment_input in fulfillments_input:
                if fulfillment := cls.create_single_fulfillment(
                    fulfillment_input,
                    order.lines,
                    order,
                    object_storage,
                    fulfillment_index,
                ):
                    order.fulfillments.append(fulfillment)
                else:
                    order.is_critical_error = True
                fulfillment_index += 1
            if order.is_critical_error:
                order.order = None
                return order

        # create transactions
        if transactions_input := order_input.get("transactions"):
            transaction_index = 0
            for transaction_input in transactions_input:
                try:
                    transaction = TransactionCreate.prepare_transaction_item_for_order(
                        order.order, transaction_input
                    )
                    order.transactions.append(transaction)
                except ValidationError as error:
                    for field, err in error.error_dict.items():
                        message = str(err[0].message)
                        code = err[0].code
                        order.errors.append(
                            OrderBulkError(
                                message=message,
                                path=f"transactions.[{transaction_index}]",
                                code=OrderBulkCreateErrorCode(code),
                            )
                        )
                transaction_index += 1

        # create invoices
        if invoices_input := order_input.get("invoices"):
            invoice_index = 0
            for invoice_input in invoices_input:
                order.invoices.append(
                    cls.create_single_invoice(invoice_input, order, invoice_index)
                )
                invoice_index += 1

        # create discounts
        if discounts_input := order_input.get("discounts"):
            discount_index = 0
            for discount_input in discounts_input:
                order.discounts.append(
                    cls.create_single_discount(
                        discount_input,
                        order,
                        order_amounts,
                        order_input["currency"],
                        discount_index,
                    )
                )
                discount_index += 1

        cls.validate_order_status(order_input["status"], order)

        if order_number := order_input.get("number"):
            order.order.number = order_number
        order.order.external_reference = order_input.get("external_reference")
        order.order.channel = order.channel
        order.order.created_at = order_input["created_at"]
        order.order.status = order_input["status"]
        order.order.user = order.user
        order.order.billing_address = order.billing_address
        order.order.shipping_address = order.shipping_address
        order.order.language_code = order_input["language_code"]
        order.order.user_email = (
            order.user.email if order.user else order_input["user"].get("email")
        )
        order.order.collection_point = delivery_method.warehouse
        order.order.collection_point_name = delivery_input.get(
            "warehouse_name"
        ) or getattr(delivery_method.warehouse, "name", None)
        order.order.shipping_method = delivery_method.shipping_method
        order.order.shipping_method_name = delivery_input.get(
            "shipping_method_name"
        ) or getattr(delivery_method.shipping_method, "name", None)
        order.order.shipping_tax_class = delivery_method.shipping_tax_class
        order.order.shipping_tax_class_name = delivery_input.get(
            "shipping_tax_class_name"
        ) or getattr(delivery_method.shipping_tax_class, "name", None)
        order.order.shipping_tax_rate = order_amounts.shipping_tax_rate
        order.order.shipping_price_gross_amount = order_amounts.shipping_price_gross
        order.order.shipping_price_net_amount = order_amounts.shipping_price_net
        order.order.total_gross_amount = order_amounts.total_gross
        order.order.undiscounted_total_gross_amount = (
            order_amounts.undiscounted_total_gross
        )
        order.order.total_net_amount = order_amounts.total_net
        order.order.undiscounted_total_net_amount = order_amounts.undiscounted_total_net
        order.order.customer_note = order_input.get("customer_note", "")
        order.order.redirect_url = order_input.get("redirect_url")
        order.order.origin = OrderOrigin.BULK_CREATE
        order.order.weight = order_input.get("weight", zero_weight())
        order.order.tracking_client_id = order_input.get("tracking_client_id")
        order.order.currency = order_input["currency"]
        order.order.should_refresh_prices = False
        order.order.voucher = order.voucher
        update_order_display_gross_prices(order.order)

        if metadata := order_input.get("metadata"):
            cls.process_metadata(
                metadata=metadata,
                errors=order.errors,
                path="metadata",
                field=order.order.metadata,
            )
        if private_metadata := order_input.get("private_metadata"):
            cls.process_metadata(
                metadata=private_metadata,
                errors=order.errors,
                path="private_metadata",
                field=order.order.private_metadata,
            )
        if shipping_metadata := delivery_method.shipping_tax_class_metadata:
            cls.process_metadata(
                metadata=shipping_metadata,
                errors=order.errors,
                path="delivery_method.shipping_tax_class_metadata",
                field=order.order.shipping_tax_class_metadata,
            )
        shipping_private_metadata = delivery_method.shipping_tax_class_private_metadata
        if shipping_private_metadata:
            cls.process_metadata(
                metadata=shipping_private_metadata,
                errors=order.errors,
                path="delivery_method.shipping_tax_class_private_metadata",
                field=order.order.shipping_tax_class_private_metadata,
            )

        return order

    @classmethod
    def handle_stocks(
        cls, orders: List[OrderBulkClass], stock_update_policy: str
    ) -> List[Stock]:
        variant_ids: List[int] = sum(
            [order.unique_variant_ids for order in orders if order.order], []
        )
        warehouse_ids: List[UUID] = sum(
            [order.unique_warehouse_ids for order in orders if order.order], []
        )
        stocks = Stock.objects.filter(
            warehouse__id__in=warehouse_ids, product_variant__id__in=variant_ids
        ).all()
        stocks_map: Dict[str, Stock] = {
            f"{stock.product_variant_id}_{stock.warehouse_id}": stock
            for stock in stocks
        }

        for order in orders:
            # Create a copy of stocks. If full iteration over order lines
            # and fulfillments will not produce error, which disqualify whole order,
            # than replace the copy with original stocks.
            stocks_map_copy = copy.deepcopy(stocks_map)
            line_index = 0
            for line in order.lines:
                order_line = line.line
                variant_id = order_line.variant_id
                warehouse = line.warehouse.id
                quantity_to_fulfill = order_line.quantity
                quantity_fulfilled = order.orderline_quantityfulfilled_map.get(
                    order_line.id, 0
                )
                quantity_to_allocate = quantity_to_fulfill - quantity_fulfilled

                if quantity_to_allocate < 0:
                    order.errors.append(
                        OrderBulkError(
                            message=f"There is more fulfillments, than ordered quantity"
                            f" for order line with variant: {variant_id} and warehouse:"
                            f" {warehouse}",
                            path=f"lines.[{line_index}]",
                            code=OrderBulkCreateErrorCode.INVALID_QUANTITY,
                        )
                    )
                    order.is_critical_error = True
                    break

                stock = stocks_map_copy.get(f"{variant_id}_{warehouse}")
                if not stock:
                    order.errors.append(
                        OrderBulkError(
                            message=f"There is no stock for given product variant:"
                            f" {variant_id} and warehouse: "
                            f"{warehouse}.",
                            path=f"lines.[{line_index}]",
                            code=OrderBulkCreateErrorCode.NON_EXISTING_STOCK,
                        )
                    )
                    order.is_critical_error = True
                    break

                available_quantity = stock.quantity - stock.quantity_allocated
                if (
                    quantity_to_allocate > available_quantity
                    and stock_update_policy != StockUpdatePolicy.FORCE
                ):
                    order.errors.append(
                        OrderBulkError(
                            message=f"Insufficient stock for product variant: "
                            f"{variant_id} and warehouse: "
                            f"{warehouse}.",
                            path=f"lines.[{line_index}]",
                            code=OrderBulkCreateErrorCode.INSUFFICIENT_STOCK,
                        )
                    )
                    order.is_critical_error = True

                stock.quantity_allocated += quantity_to_allocate

                fulfillment_lines: List[
                    OrderBulkFulfillmentLine
                ] = order.orderline_fulfillmentlines_map.get(order_line.id, [])
                for fulfillment_line in fulfillment_lines:
                    stock.quantity -= fulfillment_line.line.quantity
                line_index += 1

            if not order.is_critical_error:
                stocks_map = stocks_map_copy

        return [stock for stock in stocks_map.values()]

    @classmethod
    def handle_error_policy(cls, orders: List[OrderBulkClass], error_policy: str):
        errors = [error for order in orders for error in order.errors]
        if errors:
            for order in orders:
                if error_policy == ErrorPolicy.REJECT_EVERYTHING:
                    order.order = None
                elif error_policy == ErrorPolicy.REJECT_FAILED_ROWS:
                    if order.errors:
                        order.order = None
        return orders

    @classmethod
    def save_data(cls, orders: List[OrderBulkClass], stocks: List[Stock]):
        for order in orders:
            order.set_quantity_fulfilled()
            order.set_fulfillment_order()
            if order.is_critical_error:
                order.order = None

        addresses = []
        for order in orders:
            if order.order:
                if billing_address := order.order.billing_address:
                    addresses.append(billing_address)
                if shipping_address := order.order.shipping_address:
                    addresses.append(shipping_address)
        Address.objects.bulk_create(addresses)

        Order.objects.bulk_create([order.order for order in orders if order.order])

        order_lines: List[OrderLine] = sum(
            [order.all_order_lines for order in orders if order.order], []
        )
        OrderLine.objects.bulk_create(order_lines)

        notes = [note for order in orders for note in order.notes if order.order]
        OrderEvent.objects.bulk_create(notes)

        fulfillments = [
            fulfillment.fulfillment
            for order in orders
            for fulfillment in order.fulfillments
            if order.order
        ]
        Fulfillment.objects.bulk_create(fulfillments)
        for order in orders:
            order.set_fulfillment_id()
        fulfillment_lines: List[FulfillmentLine] = sum(
            [order.all_fulfillment_lines for order in orders if order.order], []
        )
        FulfillmentLine.objects.bulk_create(fulfillment_lines)

        Stock.objects.bulk_update(stocks, ["quantity"])

        transactions: List[TransactionItem] = sum(
            [order.all_transactions for order in orders if order.order], []
        )
        TransactionItem.objects.bulk_create(transactions)

        invoices: List[Invoice] = sum(
            [order.all_invoices for order in orders if order.order], []
        )
        Invoice.objects.bulk_create(invoices)

        discounts: List[OrderDiscount] = sum(
            [order.all_discounts for order in orders if order.order], []
        )
        OrderDiscount.objects.bulk_create(discounts)

        for order in orders:
            order.link_gift_cards()

        return orders

    @classmethod
    def perform_mutation(cls, _root, info: ResolveInfo, /, **data):
        orders_input = data["orders"]
        if len(orders_input) > MAX_ORDERS:
            error = OrderBulkError(
                message=f"Number of orders exceeds limit: {MAX_ORDERS}.",
                code=OrderBulkCreateErrorCode.BULK_LIMIT,
            )
            result = OrderBulkCreateResult(order=None, error=error)
            return OrderBulkCreate(count=0, results=result)

        orders: List[OrderBulkClass] = []
        with traced_atomic_transaction():
            # Create dictionary, which stores already resolved objects:
            #   - key for instances: "{model_name}.{key_name}.{key_value}"
            #   - key for shipping prices: "shipping_price.{shipping_method_id}"
            object_storage: Dict[str, Any] = cls.get_all_instances(orders_input)
            for order_input in orders_input:
                orders.append(cls.create_single_order(order_input, object_storage))

            error_policy = data.get("error_policy", ErrorPolicy.REJECT_EVERYTHING)
            stock_update_policy = data.get(
                "stock_update_policy", StockUpdatePolicy.UPDATE
            )
            stocks: List[Stock] = []

            cls.validate_order_numbers(orders)
            cls.handle_error_policy(orders, error_policy)
            if stock_update_policy != StockUpdatePolicy.SKIP:
                stocks = cls.handle_stocks(orders, stock_update_policy)
            cls.save_data(orders, stocks)

            manager = get_plugin_manager_promise(info.context).get()
            for order in orders:
                if order.order:
                    cls.call_event(manager.order_bulk_created, order.order)

            results = [
                OrderBulkCreateResult(order=order.order, errors=order.errors)
                for order in orders
            ]
            count = sum([order.order is not None for order in orders])
            return OrderBulkCreate(count=count, results=results)
