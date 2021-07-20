from collections import defaultdict
from typing import TYPE_CHECKING, Dict, Iterable, List, Tuple

from django.db.models import Sum
from django.db.models.functions import Coalesce

from ..core.exceptions import InsufficientStock, InsufficientStockData
from ..product.models import ProductVariantChannelListing
from .models import Stock, StockQuerySet

if TYPE_CHECKING:
    from ..product.models import Product, ProductVariant


def _get_available_quantity(stocks: StockQuerySet) -> int:
    results = stocks.aggregate(
        total_quantity=Coalesce(Sum("quantity", distinct=True), 0),
        quantity_allocated=Coalesce(Sum("allocations__quantity_allocated"), 0),
    )
    total_quantity = results["total_quantity"]
    quantity_allocated = results["quantity_allocated"]

    return max(total_quantity - quantity_allocated, 0)


def check_stock_and_preorder_quantity(
    variant: "ProductVariant", country_code: str, channel_slug: str, quantity: int
):
    """Validate if there is stock/preorder available for given variant.

    :raises InsufficientStock: when there is not enough items in stock for a variant
    or there is not enough available preorder items for a variant.
    """
    if variant.is_preorder:
        check_preorder_threshold_bulk([variant], [quantity], channel_slug)
    else:
        check_stock_quantity(variant, country_code, channel_slug, quantity)


def check_stock_quantity(
    variant: "ProductVariant", country_code: str, channel_slug: str, quantity: int
):
    """Validate if there is stock available for given variant in given country.

    If so - returns None. If there is less stock then required raise InsufficientStock
    exception.
    """
    if variant.track_inventory:
        stocks = Stock.objects.get_variant_stocks_for_country(
            country_code, channel_slug, variant
        )
        if not stocks:
            raise InsufficientStock([InsufficientStockData(variant=variant)])

        if quantity > _get_available_quantity(stocks):
            raise InsufficientStock([InsufficientStockData(variant=variant)])


def check_stock_and_preorder_quantity_bulk(
    variants: Iterable["ProductVariant"],
    country_code: str,
    quantities: Iterable[int],
    channel_slug: str,
):
    """Validate if products are available for stocks/preorder.

    :raises InsufficientStock: when there is not enough items in stock for a variant
    or there is not enough available preorder items for a variant.
    """
    (
        stock_variants,
        stock_quantities,
        preorder_variants,
        preorder_quantities,
    ) = _split_lines_for_trackable_and_preorder(variants, quantities)
    if stock_variants:
        check_stock_quantity_bulk(
            stock_variants, country_code, stock_quantities, channel_slug
        )
    if preorder_variants:
        check_preorder_threshold_bulk(
            preorder_variants, preorder_quantities, channel_slug
        )


def _split_lines_for_trackable_and_preorder(
    variants: Iterable["ProductVariant"], quantities: Iterable[int]
) -> Tuple[
    Iterable["ProductVariant"], Iterable[int], Iterable["ProductVariant"], Iterable[int]
]:
    """Return variants and quantities splitted by "is_preorder" flag."""
    stock_variants, stock_quantities = [], []
    preorder_variants, preorder_quantities = [], []

    for variant, quantity in zip(variants, quantities):
        if variant.is_preorder:
            preorder_variants.append(variant)
            preorder_quantities.append(quantity)
        else:
            stock_variants.append(variant)
            stock_quantities.append(quantity)
    return (
        stock_variants,
        stock_quantities,
        preorder_variants,
        preorder_quantities,
    )


def check_stock_quantity_bulk(
    variants: Iterable["ProductVariant"],
    country_code: str,
    quantities: Iterable[int],
    channel_slug: str,
):
    """Validate if there is stock available for given variants in given country.

    :raises InsufficientStock: when there is not enough items in stock for a variant.
    """
    all_variants_stocks = (
        Stock.objects.for_country_and_channel(country_code, channel_slug)
        .filter(product_variant__in=variants)
        .annotate_available_quantity()
    )

    variant_stocks: Dict[int, List[Stock]] = defaultdict(list)
    for stock in all_variants_stocks:
        variant_stocks[stock.product_variant_id].append(stock)

    insufficient_stocks: List[InsufficientStockData] = []
    for variant, quantity in zip(variants, quantities):
        stocks = variant_stocks.get(variant.pk, [])
        available_quantity = sum(
            [stock.available_quantity for stock in stocks]  # type: ignore
        )

        if not stocks:
            insufficient_stocks.append(
                InsufficientStockData(
                    variant=variant, available_quantity=available_quantity
                )
            )
        elif variant.track_inventory:
            if quantity > available_quantity:
                insufficient_stocks.append(
                    InsufficientStockData(
                        variant=variant, available_quantity=available_quantity
                    )
                )

    if insufficient_stocks:
        raise InsufficientStock(insufficient_stocks)


def is_product_in_stock(
    product: "Product", country_code: str, channel_slug: str
) -> bool:
    """Check if there is any variant of given product available in given country."""
    stocks = Stock.objects.get_product_stocks_for_country_and_channel(
        country_code, channel_slug, product
    ).annotate_available_quantity()
    return any(stocks.values_list("available_quantity", flat=True))


def check_preorder_threshold_bulk(
    variants: Iterable["ProductVariant"],
    quantities: Iterable[int],
    channel_slug: str,
):
    """Validate if there is enough preordered variants according to thresholds.

    :raises InsufficientStock: when there is not enough available items for a variant.
    """
    all_variants_channel_listings = (
        ProductVariantChannelListing.objects.filter(variant__in=variants)
        .annotate_available_preorder_quantities()
        .select_related("channel")
    )
    variants_channel_availability = {
        channel_listing.variant_id: (
            channel_listing.available_preorder_quantity,
            channel_listing.preorder_quantity_threshold,
        )
        for channel_listing in all_variants_channel_listings
        if channel_listing.channel.slug == channel_slug
    }

    variant_channels: Dict[int, List[ProductVariantChannelListing]] = defaultdict(list)
    for channel_listing in all_variants_channel_listings:
        variant_channels[channel_listing.variant_id].append(channel_listing)

    variants_global_allocations = {
        variant_id: sum(
            channel_listing.preorder_quantity_allocated  # type: ignore
            for channel_listing in channel_listings
        )
        for variant_id, channel_listings in variant_channels.items()
    }

    insufficient_stocks: List[InsufficientStockData] = []
    for variant, quantity in zip(variants, quantities):
        if variants_channel_availability[variant.id][1] is not None:
            if quantity > variants_channel_availability[variant.id][0]:
                insufficient_stocks.append(
                    InsufficientStockData(
                        variant=variant,
                        available_quantity=variants_channel_availability[variant.id][0],
                    )
                )

        if variant.preorder_global_threshold is not None:
            global_availability = (
                variant.preorder_global_threshold
                - variants_global_allocations[variant.id]
            )
            if quantity > global_availability:
                insufficient_stocks.append(
                    InsufficientStockData(
                        variant=variant,
                        available_quantity=global_availability,
                    )
                )

    if insufficient_stocks:
        raise InsufficientStock(insufficient_stocks)
