GRANT USAGE ON SCHEMA groundtruth TO anon, authenticated, service_role;
GRANT SELECT, INSERT, UPDATE ON groundtruth.prices TO service_role;
GRANT ALL ON groundtruth.payments TO service_role;
GRANT ALL ON groundtruth.licenses TO service_role;
GRANT ALL ON groundtruth.license_activations TO service_role;
GRANT INSERT ON groundtruth.feedback TO anon, authenticated, service_role;
GRANT SELECT, UPDATE, DELETE ON groundtruth.feedback TO service_role;
