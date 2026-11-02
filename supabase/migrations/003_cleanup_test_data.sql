DELETE FROM groundtruth.license_activations
WHERE license_id IN (
  SELECT id FROM groundtruth.licenses
  WHERE payment_id IN (
    SELECT id FROM groundtruth.payments
    WHERE paystack_ref LIKE 'test_ref_%'
  )
);
DELETE FROM groundtruth.licenses
WHERE payment_id IN (
  SELECT id FROM groundtruth.payments WHERE paystack_ref LIKE 'test_ref_%'
);
DELETE FROM groundtruth.payments WHERE paystack_ref LIKE 'test_ref_%';
DELETE FROM groundtruth.feedback WHERE message LIKE 'deployment test%';
