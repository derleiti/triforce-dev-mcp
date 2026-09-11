<?php
/**
 * Payments Service
 * Orchestrates payment providers, webhook handling, rate limiting, and logging.
 *
 * @package NovAI
 */

namespace NovAI\Services;

defined('ABSPATH') || exit;

// Manually load providers (not autoloaded due to nested namespace/path mismatch)
require_once __DIR__ . '/providers/PaymentProviderInterface.php';
require_once __DIR__ . '/providers/LemonSqueezyProvider.php';
require_once __DIR__ . '/providers/StubProvider.php';
require_once __DIR__ . '/EntitlementsService.php';

class PaymentsService {

    private static ?self $instance = null;

    /** @var \NovAI\Services\Providers\PaymentProviderInterface */
    private $provider;

    private string $provider_name;

    const LOG_FILE     = WP_CONTENT_DIR . '/ailinux-payments.log';
    const LOG_MAX_SIZE = 5 * 1024 * 1024; // 5 MB
    const RATE_LIMIT   = 60; // requests per minute
    const RATE_WINDOW  = 60; // seconds
    const IDEMPOTENCY_TTL = 172800; // 48 hours

    public static function instance(): self {
        if (self::$instance === null) {
            self::$instance = new self();
        }
        return self::$instance;
    }

    private function __construct() {
        $this->provider_name = defined('NOVA_PAYMENT_PROVIDER') ? NOVA_PAYMENT_PROVIDER : 'stub';
        $this->provider      = $this->load_provider($this->provider_name);

        add_action('rest_api_init', [$this, 'register_rest_routes']);
        add_action('wp_ajax_nova_resync_entitlements', [$this, 'ajax_resync_entitlements']);
    }

    // -------------------------------------------------------------------------
    // Provider
    // -------------------------------------------------------------------------

    private function load_provider(string $name): \NovAI\Services\Providers\PaymentProviderInterface {
        if ($name === 'lemonsqueezy' && defined('NOVA_LS_WEBHOOK_DIRECT') && NOVA_LS_WEBHOOK_DIRECT) {
            return new \NovAI\Services\Providers\LemonSqueezyProvider();
        }
        return new \NovAI\Services\Providers\StubProvider();
    }

    public function get_provider_status(): string {
        if ($this->provider_name === 'lemonsqueezy' && defined('NOVA_LS_WEBHOOK_DIRECT') && NOVA_LS_WEBHOOK_DIRECT) {
            return 'active';
        }
        if ($this->provider_name === 'stub') {
            return 'stub';
        }
        return 'disabled';
    }

    // -------------------------------------------------------------------------
    // REST Routes
    // -------------------------------------------------------------------------

    public function register_rest_routes(): void {
        register_rest_route('nova-ai/v1', '/payments/webhook/lemonsqueezy', [
            'methods'             => 'POST',
            'callback'            => [$this, 'handle_webhook'],
            'permission_callback' => '__return_true',
        ]);

        // Admin: resync entitlements for a user
        register_rest_route('nova-ai/v1', '/admin/resync-entitlements', [
            'methods'             => 'POST',
            'callback'            => [$this, 'api_admin_resync'],
            'permission_callback' => function () {
                return current_user_can('manage_options');
            },
        ]);
    }

    // -------------------------------------------------------------------------
    // Webhook Handler
    // -------------------------------------------------------------------------

    public function handle_webhook(\WP_REST_Request $request): \WP_REST_Response {
        // Rate limit
        if (!$this->check_rate_limit()) {
            $this->log('WARN', 'Rate limit exceeded', ['ip' => $_SERVER['REMOTE_ADDR'] ?? '']);
            return new \WP_REST_Response(['error' => 'Too Many Requests'], 429);
        }

        // Raw body (never $_POST)
        $raw_body = $request->get_body();
        if (empty($raw_body)) {
            return new \WP_REST_Response(['error' => 'Empty body'], 400);
        }

        // All headers lowercase for provider
        $headers = [];
        foreach ($request->get_headers() as $key => $values) {
            $headers[strtolower($key)] = is_array($values) ? implode(', ', $values) : $values;
        }

        // Verify signature + parse
        try {
            $event = $this->provider->verify_webhook($headers, $raw_body);
        } catch (\RuntimeException $e) {
            $this->log('ERROR', 'Signature verification failed: ' . $e->getMessage());
            return new \WP_REST_Response(['error' => 'Unauthorized'], 401);
        }

        $event_name = $event['meta']['event_name'] ?? 'unknown';
        $event_test_mode = (bool) ($event['meta']['test_mode'] ?? false);
        $configured_test_mode = defined('NOVA_LS_TEST_MODE') ? (bool) NOVA_LS_TEST_MODE : true;

        if ($event_test_mode !== $configured_test_mode) {
            $this->log('ERROR', 'Webhook mode mismatch', [
                'event' => $event_name,
                'event_test_mode' => $event_test_mode,
                'configured_test_mode' => $configured_test_mode,
            ]);
            return new \WP_REST_Response(['error' => 'Webhook mode mismatch'], 409);
        }

        // Lemon Squeezy does not provide a separate delivery UUID in this payload.
        // Hash the exact signed body so retries deduplicate, while later updates of
        // the same subscription/order remain processable.
        $event_id = hash('sha256', $event_name . '|' . $raw_body);

        if ($this->is_duplicate_event($event_id)) {
            $this->log('INFO', 'Duplicate event skipped', ['event_id' => $event_id, 'event' => $event_name]);
            return new \WP_REST_Response(['status' => 'already_processed'], 200);
        }

        // Map to entitlements
        try {
            $ents = $this->provider->map_event_to_entitlements($event);
        } catch (\Exception $e) {
            $this->log('ERROR', 'Entitlement mapping failed: ' . $e->getMessage(), ['event' => $event_name]);
            return new \WP_REST_Response(['error' => 'Processing error'], 500);
        }

        // Resolve WP user
        $user_id = EntitlementsService::resolve_user_id((int) $ents['user_id'], $ents['email']);

        $this->log('INFO', 'Webhook received', [
            'event'   => $event_name,
            'user_id' => $user_id,
            'action'  => $ents['action'],
            'tier'    => $ents['tier'],
        ]);

        if (($ents['action'] ?? 'none') === 'none') {
            $this->mark_event_processed($event_id);
            $this->log('INFO', 'Webhook event ignored (no entitlement action)', ['event' => $event_name]);
            return new \WP_REST_Response(['status' => 'ignored'], 202);
        }

        if ($user_id <= 0) {
            $this->log('ERROR', 'Could not resolve WP user', [
                'event'       => $event_name,
                'hint_uid'    => $ents['user_id'],
                'email'       => $ents['email'],
            ]);
            return new \WP_REST_Response(['error' => 'User attribution failed'], 422);
        }

        if (!$this->apply_entitlements($user_id, $ents)) {
            $this->log('ERROR', 'Entitlement backend sync failed', [
                'event' => $event_name,
                'user_id' => $user_id,
            ]);
            return new \WP_REST_Response(['error' => 'Entitlement sync failed'], 503);
        }

        if (in_array(($ents['action'] ?? ''), ['purchase', 'refund'], true)) {
            EntitlementsService::record_purchase_event($user_id, is_array($ents['purchase'] ?? null) ? $ents['purchase'] : [], $ents['action']);
        }

        $this->mark_event_processed($event_id);

        // Track last webhook
        update_option('nova_last_webhook', [
            'timestamp'  => time(),
            'event_name' => $event_name,
            'event_id'   => $event_id,
            'user_id'    => $user_id,
            'action'     => $ents['action'],
        ], false);

        return new \WP_REST_Response(['status' => 'ok'], 200);
    }

    // -------------------------------------------------------------------------
    // Entitlement Application
    // -------------------------------------------------------------------------

    private function apply_entitlements(int $user_id, array $ents): bool {
        switch ($ents['action']) {
            case 'activate':
                EntitlementsService::set_entitlements($user_id, [
                    'tier'            => $ents['tier'],
                    'subscription_id' => $ents['subscription_id'],
                    'customer_id'     => $ents['customer_id'],
                ]);
                break;

            case 'deactivate':
                EntitlementsService::downgrade_to_free($user_id);
                break;

            case 'purchase':
                EntitlementsService::set_entitlements($user_id, [
                    'customer_id' => $ents['customer_id'],
                    'extra'       => $ents['extra'],
                ]);
                break;

            case 'refund':
                EntitlementsService::remove_entitlements($user_id, $ents['extra']);
                break;

            default:
                return true;
        }

        return EntitlementsService::sync_to_backend($user_id);
    }

    // -------------------------------------------------------------------------
    // Admin REST: Resync
    // -------------------------------------------------------------------------

    public function api_admin_resync(\WP_REST_Request $request): \WP_REST_Response {
        $user_id = (int) $request->get_param('user_id');
        if (!$user_id || !get_userdata($user_id)) {
            return new \WP_REST_Response(['error' => 'Invalid user_id'], 400);
        }
        EntitlementsService::sync_to_backend($user_id);
        $this->log('INFO', 'Admin triggered resync', ['user_id' => $user_id]);
        return new \WP_REST_Response(['status' => 'ok', 'user_id' => $user_id], 200);
    }

    // -------------------------------------------------------------------------
    // Admin AJAX: Resync
    // -------------------------------------------------------------------------

    public function ajax_resync_entitlements(): void {
        check_ajax_referer('nova_admin_action', 'nonce');
        if (!current_user_can('manage_options')) {
            wp_die('Unauthorized', 403);
        }
        $user_id = (int) ($_POST['user_id'] ?? 0);
        if (!$user_id || !get_userdata($user_id)) {
            wp_send_json_error('Invalid user_id');
        }
        EntitlementsService::sync_to_backend($user_id);
        $this->log('INFO', 'Admin AJAX resync', ['user_id' => $user_id]);
        wp_send_json_success(['user_id' => $user_id]);
    }

    // -------------------------------------------------------------------------
    // Rate Limiting
    // -------------------------------------------------------------------------

    private function check_rate_limit(): bool {
        $ip  = sanitize_text_field($_SERVER['REMOTE_ADDR'] ?? 'unknown');
        $key = 'nova_webhook_rate_' . md5($ip);
        $count = (int) get_transient($key);

        if ($count >= self::RATE_LIMIT) {
            return false;
        }

        // Increment counter; set expiry only on first hit
        if ($count === 0) {
            set_transient($key, 1, self::RATE_WINDOW);
        } else {
            set_transient($key, $count + 1, self::RATE_WINDOW);
        }

        return true;
    }

    // -------------------------------------------------------------------------
    // Idempotency
    // -------------------------------------------------------------------------

    private function is_duplicate_event(string $event_id): bool {
        if ($event_id === '') {
            return false;
        }
        return (bool) get_transient('nova_ls_evt_' . md5($event_id));
    }

    private function mark_event_processed(string $event_id): void {
        if ($event_id === '') {
            return;
        }
        set_transient('nova_ls_evt_' . md5($event_id), 1, self::IDEMPOTENCY_TTL);
    }

    // -------------------------------------------------------------------------
    // Logging
    // -------------------------------------------------------------------------

    public function log(string $level, string $message, array $context = []): void {
        $log_file = self::LOG_FILE;

        // Rotate at 5 MB
        if (file_exists($log_file) && filesize($log_file) > self::LOG_MAX_SIZE) {
            @rename($log_file, $log_file . '.old');
        }

        $entry = sprintf(
            '[%s] [%s] %s %s',
            date('Y-m-d H:i:s'),
            strtoupper($level),
            $message,
            $context ? json_encode($context) : ''
        );

        @file_put_contents($log_file, $entry . PHP_EOL, FILE_APPEND | LOCK_EX);
    }

    /**
     * Read last N lines of the payments log.
     */
    public function get_log_tail(int $lines = 50): string {
        $log_file = self::LOG_FILE;
        if (!file_exists($log_file)) {
            return '(no log file yet)';
        }

        $all = file($log_file, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES);
        if ($all === false) {
            return '(unreadable)';
        }

        $tail = array_slice($all, -$lines);
        return implode("\n", array_map('esc_html', $tail));
    }

    /**
     * Get the last recorded webhook info from options.
     */
    public function get_last_webhook(): array {
        return get_option('nova_last_webhook', []);
    }
}
