-- NOTHING PostgreSQL production schema v7: unique payment-route constraints.

CREATE UNIQUE INDEX IF NOT EXISTS ux_billing_invoice_xrp_route
    ON billing_invoices(network, destination, routing_reference)
    WHERE routing_mode = 'xrp_destination_tag';

CREATE UNIQUE INDEX IF NOT EXISTS ux_billing_invoice_unique_destination
    ON billing_invoices(network, destination)
    WHERE routing_mode = 'unique_destination';

ALTER TABLE billing_invoices
    DROP CONSTRAINT IF EXISTS billing_invoices_xrp_route_reference_check;
ALTER TABLE billing_invoices
    ADD CONSTRAINT billing_invoices_xrp_route_reference_check
    CHECK (
        routing_mode <> 'xrp_destination_tag'
        OR (
            network = 'xrpl'
            AND routing_reference ~ '^[1-9][0-9]{0,9}$'
            AND routing_reference::numeric <= 4294967295
        )
    );
