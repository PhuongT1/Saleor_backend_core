import graphene
import pytest

from .....warehouse.models import Stock
from ....tests.utils import get_graphql_content

STOCKS_BULK_UPDATE_MUTATION = """
    mutation StocksBulkUpdate($stocks: [StockBulkUpdateInput!]!){
        stocksBulkUpdate(stocks: $stocks){
            results{
                errors {
                    field
                    message
                    code
                }
                stock{
                    id
                    quantity
                }
            }
            count
        }
    }
"""


@pytest.mark.django_db
@pytest.mark.count_queries(autouse=False)
def test_stocks_bulk_update_queries_count(
    staff_api_client,
    variant,
    variant_with_many_stocks,
    warehouse_no_shipping_zone,
    warehouse,
    permission_manage_products,
    django_assert_num_queries,
    count_queries,
):
    # given
    variant_1 = variant_with_many_stocks
    variant_2 = variant

    stock_3 = Stock.objects.create(
        warehouse=warehouse, product_variant=variant, quantity=4
    )
    stock_4 = Stock.objects.create(
        warehouse=warehouse_no_shipping_zone, product_variant=variant, quantity=4
    )

    variant_1_id = graphene.Node.to_global_id("ProductVariant", variant_1.pk)

    stocks = variant.stocks.all()
    stock_1 = stocks[0]
    stock_2 = stocks[1]

    new_quantity = 999

    warehouse_1_id = graphene.Node.to_global_id("Warehouse", stock_1.warehouse_id)
    warehouse_4_id = graphene.Node.to_global_id("Warehouse", stock_4.warehouse_id)

    stocks_input = [
        {
            "variantId": variant_1_id,
            "warehouseId": warehouse_1_id,
            "quantity": new_quantity,
        }
    ]

    # test number of queries when single object is updated
    with django_assert_num_queries(9):
        staff_api_client.user.user_permissions.add(permission_manage_products)
        response = staff_api_client.post_graphql(
            STOCKS_BULK_UPDATE_MUTATION, {"stocks": stocks_input}
        )
        content = get_graphql_content(response)
        data = content["data"]["stocksBulkUpdate"]
        assert data["count"] == 1

    stocks_input += [
        {
            "variantId": variant_1_id,
            "warehouseExternalReference": stock_2.warehouse.external_reference,
            "quantity": new_quantity,
        },
        {
            "variantExternalReference": variant_2.external_reference,
            "warehouseExternalReference": stock_3.warehouse.external_reference,
            "quantity": new_quantity,
        },
        {
            "variantExternalReference": variant_2.external_reference,
            "warehouseId": warehouse_4_id,
            "quantity": new_quantity,
        },
    ]

    # Test number of queries when multiple objects are updated
    with django_assert_num_queries(9):
        staff_api_client.user.user_permissions.add(permission_manage_products)
        response = staff_api_client.post_graphql(
            STOCKS_BULK_UPDATE_MUTATION, {"stocks": stocks_input}
        )
        content = get_graphql_content(response)
        data = content["data"]["stocksBulkUpdate"]
        assert data["count"] == 4
