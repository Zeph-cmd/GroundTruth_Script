DELETE FROM groundtruth.license_activations
WHERE license_id IN (
  SELECT id FROM groundtruth.licenses
  WHERE payment_id IN (
    SELECT id FROM groundtruth.payments
    WHERE paystack_ref LIKE 'gt_style_%'
  )
);
DELETE FROM groundtruth.licenses
WHERE payment_id IN (
  SELECT id FROM groundtruth.payments WHERE paystack_ref LIKE 'gt_style_%'
);
DELETE FROM groundtruth.payments WHERE paystack_ref LIKE 'gt_style_%';
