import graphene

from ....graphql.meta.tests.utils import (
    execute_clear_private_metadata_for_item,
    execute_clear_public_metadata_for_item,
    execute_update_private_metadata_for_item,
    execute_update_public_metadata_for_item,
    item_contains_proper_private_metadata,
    item_contains_proper_public_metadata,
    item_without_private_metadata,
    item_without_public_metadata,
)
from ...tests.utils import assert_no_permission, get_graphql_content

PRIVATE_KEY = "private_key"
PRIVATE_VALUE = "private_vale"

PUBLIC_KEY = "key"
PUBLIC_KEY2 = "key2"
PUBLIC_VALUE = "value"
PUBLIC_VALUE2 = "value2"

QUERY_WAREHOUSE_PUBLIC_META = """
    query warehouseMeta($id: ID!){
         warehouse(id: $id){
            metadata{
                key
                value
            }
        }
    }
"""


def test_query_public_meta_for_warehouse_as_anonymous_user(api_client, warehouse):
    # given
    warehouse.store_value_in_metadata({PUBLIC_KEY: PUBLIC_VALUE})
    warehouse.save(update_fields=["metadata"])
    variables = {
        "id": graphene.Node.to_global_id("Warehouse", warehouse.pk),
    }

    # when
    response = api_client.post_graphql(QUERY_WAREHOUSE_PUBLIC_META, variables)
    content = get_graphql_content(response)

    # then
    metadata = content["data"]["warehouse"]["metadata"][0]
    assert metadata["key"] == PUBLIC_KEY
    assert metadata["value"] == PUBLIC_VALUE


def test_query_public_meta_for_warehouse_as_customer(user_api_client, warehouse):
    # given
    warehouse.store_value_in_metadata({PUBLIC_KEY: PUBLIC_VALUE})
    warehouse.save(update_fields=["metadata"])
    variables = {
        "id": graphene.Node.to_global_id("Warehouse", warehouse.pk),
    }

    # when
    response = user_api_client.post_graphql(QUERY_WAREHOUSE_PUBLIC_META, variables)
    content = get_graphql_content(response)

    # then
    metadata = content["data"]["warehouse"]["metadata"][0]
    assert metadata["key"] == PUBLIC_KEY
    assert metadata["value"] == PUBLIC_VALUE


def test_query_public_meta_for_warehouse_as_staff(
    staff_api_client, warehouse, permission_manage_products
):
    # given
    warehouse.store_value_in_metadata({PUBLIC_KEY: PUBLIC_VALUE})
    warehouse.save(update_fields=["metadata"])
    variables = {"id": graphene.Node.to_global_id("Warehouse", warehouse.pk)}

    # when
    response = staff_api_client.post_graphql(
        QUERY_WAREHOUSE_PUBLIC_META,
        variables,
        [permission_manage_products],
        check_no_permissions=False,
    )
    content = get_graphql_content(response)

    # then
    metadata = content["data"]["warehouse"]["metadata"][0]
    assert metadata["key"] == PUBLIC_KEY
    assert metadata["value"] == PUBLIC_VALUE


def test_query_public_meta_for_warehouse_as_app(
    app_api_client, warehouse, permission_manage_products
):
    # given
    warehouse.store_value_in_metadata({PUBLIC_KEY: PUBLIC_VALUE})
    warehouse.save(update_fields=["metadata"])
    variables = {"id": graphene.Node.to_global_id("Warehouse", warehouse.pk)}

    # when
    response = app_api_client.post_graphql(
        QUERY_WAREHOUSE_PUBLIC_META,
        variables,
        [permission_manage_products],
        check_no_permissions=False,
    )
    content = get_graphql_content(response)

    # then
    metadata = content["data"]["warehouse"]["metadata"][0]
    assert metadata["key"] == PUBLIC_KEY
    assert metadata["value"] == PUBLIC_VALUE


QUERY_WAREHOUSE_PRIVATE_META = """
    query warehouseMeta($id: ID!){
        warehouse(id: $id){
            privateMetadata{
                key
                value
            }
        }
    }
"""


def test_query_private_meta_for_warehouse_as_anonymous_user(api_client, warehouse):
    # given
    variables = {
        "id": graphene.Node.to_global_id("Warehouse", warehouse.pk),
    }

    # when
    response = api_client.post_graphql(QUERY_WAREHOUSE_PRIVATE_META, variables)

    # then
    assert_no_permission(response)


def test_query_private_meta_for_warehouse_as_customer(user_api_client, warehouse):
    # given
    variables = {
        "id": graphene.Node.to_global_id("Warehouse", warehouse.pk),
    }

    # when
    response = user_api_client.post_graphql(QUERY_WAREHOUSE_PUBLIC_META, variables)

    # then
    assert_no_permission(response)


def test_query_private_meta_for_warehouse_as_staff(
    staff_api_client, warehouse, permission_manage_products
):
    # given
    warehouse.store_value_in_private_metadata({PRIVATE_KEY: PRIVATE_VALUE})
    warehouse.save(update_fields=["private_metadata"])
    variables = {"id": graphene.Node.to_global_id("Warehouse", warehouse.pk)}

    # when
    response = staff_api_client.post_graphql(
        QUERY_WAREHOUSE_PRIVATE_META,
        variables,
        [permission_manage_products],
        check_no_permissions=False,
    )
    content = get_graphql_content(response)

    # then
    metadata = content["data"]["warehouse"]["privateMetadata"][0]
    assert metadata["key"] == PRIVATE_KEY
    assert metadata["value"] == PRIVATE_VALUE


def test_query_private_meta_for_warehouse_as_app(
    app_api_client, warehouse, permission_manage_products
):
    # given
    warehouse.store_value_in_private_metadata({PRIVATE_KEY: PRIVATE_VALUE})
    warehouse.save(update_fields=["private_metadata"])
    variables = {
        "id": graphene.Node.to_global_id("Warehouse", warehouse.pk),
    }

    # when
    response = app_api_client.post_graphql(
        QUERY_WAREHOUSE_PRIVATE_META,
        variables,
        [permission_manage_products],
        check_no_permissions=False,
    )
    content = get_graphql_content(response)

    # then
    metadata = content["data"]["warehouse"]["privateMetadata"][0]
    assert metadata["key"] == PRIVATE_KEY
    assert metadata["value"] == PRIVATE_VALUE


def test_add_public_metadata_for_warehouse(
    staff_api_client, permission_manage_products, warehouse
):
    # given
    warehouse_id = graphene.Node.to_global_id("Warehouse", warehouse.pk)

    # when
    response = execute_update_public_metadata_for_item(
        staff_api_client, permission_manage_products, warehouse_id, "Warehouse"
    )

    # then
    assert item_contains_proper_public_metadata(
        response["data"]["updateMetadata"]["item"], warehouse, warehouse_id
    )


def test_delete_public_metadata_for_warehouse(
    staff_api_client, permission_manage_products, warehouse
):
    # given
    warehouse.store_value_in_metadata({PUBLIC_KEY: PUBLIC_VALUE})
    warehouse.save(update_fields=["metadata"])
    warehouse_id = graphene.Node.to_global_id("Warehouse", warehouse.pk)

    # when
    response = execute_clear_public_metadata_for_item(
        staff_api_client, permission_manage_products, warehouse_id, "Warehouse"
    )

    # then
    assert item_without_public_metadata(
        response["data"]["deleteMetadata"]["item"], warehouse, warehouse_id
    )


def test_add_private_metadata_for_warehouse(
    staff_api_client, permission_manage_products, warehouse
):
    # given
    warehouse_id = graphene.Node.to_global_id("Warehouse", warehouse.pk)

    # when
    response = execute_update_private_metadata_for_item(
        staff_api_client, permission_manage_products, warehouse_id, "Warehouse"
    )

    # then
    assert item_contains_proper_private_metadata(
        response["data"]["updatePrivateMetadata"]["item"], warehouse, warehouse_id
    )


def test_delete_private_metadata_for_warehouse(
    staff_api_client, permission_manage_products, warehouse
):
    # given
    warehouse.store_value_in_private_metadata({PRIVATE_KEY: PRIVATE_VALUE})
    warehouse.save(update_fields=["private_metadata"])
    warehouse_id = graphene.Node.to_global_id("Warehouse", warehouse.pk)

    # when
    response = execute_clear_private_metadata_for_item(
        staff_api_client, permission_manage_products, warehouse_id, "Warehouse"
    )

    # then
    assert item_without_private_metadata(
        response["data"]["deletePrivateMetadata"]["item"], warehouse, warehouse_id
    )
