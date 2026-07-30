export type OT3Product = {
  id: 'pro-30' | 'pro-90' | 'pro-monthly' | 'pro-yearly';
  kind: 'onetime' | 'subscription';
  labelKey: string;
  displayPrice: string;
};

// Akagi-3 ships these operator-defined ids. Prices are display-only: the
// inference server and PayPal approval page own and verify the actual amount.
export const OT3_PRODUCTS: readonly OT3Product[] = [
  {
    id: 'pro-30',
    kind: 'onetime',
    labelKey: 'settings.model_config.product_pro_30',
    displayPrice: 'US$10',
  },
  {
    id: 'pro-90',
    kind: 'onetime',
    labelKey: 'settings.model_config.product_pro_90',
    displayPrice: 'US$27',
  },
  {
    id: 'pro-monthly',
    kind: 'subscription',
    labelKey: 'settings.model_config.product_pro_monthly',
    displayPrice: 'US$10 / month',
  },
  {
    id: 'pro-yearly',
    kind: 'subscription',
    labelKey: 'settings.model_config.product_pro_yearly',
    displayPrice: 'US$100 / year',
  },
] as const;
