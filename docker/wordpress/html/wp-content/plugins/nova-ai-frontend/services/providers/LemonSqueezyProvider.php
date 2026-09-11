<?php
/**
 * LemonSqueezy Payment Provider
 *
 * @package NovAI
 */

namespace NovAI\Services\Providers;

defined('ABSPATH') || exit;

require_once __DIR__ . '/PaymentProviderInterface.php';

class LemonSqueezyProvider implements PaymentProviderInterface {

    private string $secret;

    public function __construct() {
        $this->secret = defined('NOVA_LS_WEBHOOK_SECRET') ? NOVA_LS_WEBHOOK_SECRET : '';
    }

    /**
     * Verify HMAC-SHA256 signature from X-Signature header.
     * Uses raw body — never $_POST.
     *
     * @throws \RuntimeException on signature mismatch or missing secret
     */
    public function verify_webhook(array $headers, string $raw_body): array {
        if (empty($this->secret)) {
            throw new \RuntimeException('NOVA_LS_WEBHOOK_SECRET is not configured');
        }

        // WP_REST_Request normalizes HTTP header dashes to underscores.
        $received_sig = $headers['x-signature']
            ?? $headers['x_signature']
            ?? $headers['X-Signature']
            ?? '';
        if (empty($received_sig)) {
            throw new \RuntimeException('Missing X-Signature header');
        }

        $expected_sig = hash_hmac('sha256', $raw_body, $this->secret);

        if (!hash_equals($expected_sig, strtolower($received_sig))) {
            throw new \RuntimeException('Invalid webhook signature');
        }

        $payload = json_decode($raw_body, true);
        if (!is_array($payload)) {
            throw new \RuntimeException('Malformed JSON payload');
        }

        return $payload;
    }

    /**
     * Map LemonSqueezy event payload to entitlement changes.
     */
    public function map_event_to_entitlements(array $event): array {
        $event_name     = $event['meta']['event_name'] ?? '';
        $custom_data    = $event['meta']['custom_data'] ?? [];
        $wp_user_id     = (int) ($custom_data['wp_user_id'] ?? 0);
        $attrs            = $event['data']['attributes'] ?? [];
        $first_order_item = is_array($attrs['first_order_item'] ?? null)
            ? $attrs['first_order_item']
            : [];
        $email            = $attrs['user_email'] ?? ($custom_data['wp_user_email'] ?? '');
        $subscription_id  = $event['data']['id'] ?? '';
        $customer_id      = (string) ($attrs['customer_id'] ?? '');
        $variant_id       = (string) (
            $attrs['variant_id']
            ?? $first_order_item['variant_id']
            ?? $custom_data['variant_id']
            ?? ''
        );
        $product_id       = (string) (
            $attrs['product_id']
            ?? $first_order_item['product_id']
            ?? $custom_data['product_id']
            ?? ''
        );

        // Map variant_id to tier. Configurable via filter.
        $tier_map = apply_filters('nova_ls_variant_tier_map', []);
        $tier = $tier_map[$variant_id] ?? 'tier1';

        $product_name = (string) (
            $custom_data['product_name']
            ?? $first_order_item['product_name']
            ?? $attrs['product_name']
            ?? ''
        );
        $purchased_at = (string) ($attrs['created_at'] ?? $attrs['updated_at'] ?? '');

        $base = [
            'event_name'      => $event_name,
            'user_id'         => $wp_user_id,
            'email'           => sanitize_email($email),
            'subscription_id' => sanitize_text_field($subscription_id),
            'customer_id'     => sanitize_text_field($customer_id),
            'tier'            => $tier,
            'extra'           => [],
            'purchase'        => [
                'id'           => sanitize_text_field((string) ($event['data']['id'] ?? '')),
                'item_name'    => sanitize_text_field($product_name),
                'product_id'   => sanitize_text_field($product_id),
                'variant_id'   => sanitize_text_field($variant_id),
                'purchased_at' => sanitize_text_field($purchased_at),
                'source'       => 'lemonsqueezy',
            ],
            'action'          => 'none',
        ];

        switch ($event_name) {
            case 'subscription_created':
            case 'subscription_updated':
            case 'subscription_payment_success':
                $base['action'] = 'activate';
                break;

            case 'subscription_expired':
                $base['action'] = 'deactivate';
                $base['tier']   = 'free';
                break;

            case 'order_created':
                // Lemon Squeezy emits order_created for successful orders. Still require
                // a paid status when one is present, so unpaid orders never unlock Copa.
                $status = strtolower((string) ($attrs['status'] ?? ''));
                if ($product_id && (!$status || $status === 'paid')) {
                    $entitlement = $this->entitlement_for_product($product_id);
                    $base['action'] = 'purchase';
                    $base['extra']  = [$entitlement];
                    $base['purchase']['entitlement'] = $entitlement;
                    $base['purchase']['status'] = 'paid';
                }
                break;

            case 'order_refunded':
                if ($product_id) {
                    $entitlement = $this->entitlement_for_product($product_id);
                    $base['action'] = 'refund';
                    $base['extra']  = [$entitlement];
                    $base['purchase']['entitlement'] = $entitlement;
                    $base['purchase']['status'] = 'refunded';
                }
                break;
        }

        return $base;
    }

    /**
     * Map Lemon Squeezy product IDs to stable application entitlement keys.
     * Format: "970007:copa_ocr,969895:ailinux_premium".
     */
    private function entitlement_for_product(string $product_id): string {
        $mapping = defined('NOVA_LS_PRODUCT_ENTITLEMENTS')
            ? (string) NOVA_LS_PRODUCT_ENTITLEMENTS
            : '';

        foreach (array_filter(array_map('trim', explode(',', $mapping))) as $pair) {
            [$id, $entitlement] = array_pad(array_map('trim', explode(':', $pair, 2)), 2, '');
            if ($id === $product_id && $entitlement !== '') {
                return sanitize_key($entitlement);
            }
        }

        return 'ls_product_' . sanitize_key($product_id);
    }
}
