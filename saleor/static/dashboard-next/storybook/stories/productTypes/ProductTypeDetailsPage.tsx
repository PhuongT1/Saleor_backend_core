import { Omit } from "@material-ui/core";
import { storiesOf } from "@storybook/react";
import React from "react";

import { listActionsProps } from "@saleor/fixtures";
import ProductTypeDetailsPage, {
  ProductTypeDetailsPageProps
} from "../../../productTypes/components/ProductTypeDetailsPage";
import { productType } from "../../../productTypes/fixtures";
import { WeightUnitsEnum } from "../../../types/globalTypes";
import Decorator from "../../Decorator";

const props: Omit<ProductTypeDetailsPageProps, "classes"> = {
  defaultWeightUnit: "kg" as WeightUnitsEnum,
  disabled: false,
  errors: [],
  onAttributeAdd: () => undefined,
  onAttributeClick: () => undefined,
  onAttributeUnassign: () => undefined,
  onBack: () => undefined,
  onDelete: () => undefined,
  onSubmit: () => undefined,
  pageTitle: productType.name,
  productAttributeList: listActionsProps,
  productType,
  saveButtonBarState: "default",
  taxTypes: [],
  variantAttributeList: listActionsProps
};

storiesOf("Views / Product types / Product type details", module)
  .addDecorator(Decorator)
  .add("default", () => <ProductTypeDetailsPage {...props} />)
  .add("loading", () => (
    <ProductTypeDetailsPage
      {...props}
      disabled={true}
      pageTitle={undefined}
      productType={undefined}
    />
  ));
