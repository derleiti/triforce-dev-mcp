<?php
/**
 * Plugin Name: Nova COPA Compatibility Layer
 * Description: Password reset fix, LemonSqueezy legacy webhooks, WP-admin user/entitlement bridge.
 */

defined('ABSPATH') || exit;

if (!defined('NOVA_LS_WEBHOOK_DIRECT')) {
    define('NOVA_LS_WEBHOOK_DIRECT', true);
}

add_action('plugins_loaded', function () {
    // The compatibility bridge must never bootstrap Nova on its own.
    // Active plugins are loaded before plugins_loaded; if this constant is absent,
    // nova-ai-frontend is inactive/unavailable and its config may not be loaded safely.
    if (!defined('NOVA_AI_PLUGIN_DIR')) {
        return;
    }

    $base = NOVA_AI_PLUGIN_DIR;

    if (is_file($base . 'config/lemonsqueezy.php')) {
        require_once $base . 'config/lemonsqueezy.php';
    }

    foreach ([
        'services/PaymentsService.php',
        'services/EntitlementsService.php',
    ] as $file) {
        if (is_file($base . $file)) {
            require_once $base . $file;
        }
    }

    if (class_exists('\\NovAI\\Services\\PaymentsService')) {
        \NovAI\Services\PaymentsService::instance();
    }
}, 20);

add_action('template_redirect', function () {
    $path = rtrim(parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH) ?: '/', '/');

    if (in_array($path, ['/reset', '/password-reset', '/forgot-password'], true)) {
        wp_safe_redirect('https://ailinux.me/wp-login.php?action=lostpassword&ailinux_wp_admin=1', 302);
        exit;
    }
}, 1);

function nova_copa_admin_allowed(): bool {
    return current_user_can('manage_options');
}

function nova_copa_headers_for_backend(): array {
    $headers = ['Content-Type' => 'application/json'];

    $secret = '';

    // Env is authoritative. Constants may be stale from wp-config.php.
    foreach (['NOVA_AI_INTERNAL_KEY', 'WEBHOOK_SECRET', 'TRIFORCE_ADMIN_SECRET', 'MCP_ADMIN_TOKEN', 'MCP_AUTH_TOKEN'] as $env_key) {
        $value = getenv($env_key);
        if (is_string($value) && $value !== '') {
            $secret = $value;
            break;
        }
    }

    if ($secret === '') {
        foreach (['NOVA_AI_INTERNAL_KEY', 'TRIFORCE_ADMIN_SECRET', 'MCP_ADMIN_TOKEN', 'MCP_AUTH_TOKEN'] as $constant) {
            if (defined($constant) && constant($constant)) {
                $secret = (string) constant($constant);
                break;
            }
        }
    }

    if ($secret === '') {
        $settings = get_option('nova_ai_settings', []);
        if (!empty($settings['internal_key'])) {
            $secret = (string) $settings['internal_key'];
        }
    }

    if ($secret !== '') {
        $headers['X-Internal-Key'] = $secret;
        $headers['X-Nova-Webhook-Secret'] = $secret;
        $headers['Authorization'] = 'Bearer ' . $secret;
    }

    return $headers;
}

function nova_copa_backend_base(): string {
    $settings = get_option('nova_ai_settings', []);
    $endpoint = $settings['api_endpoint_internal'] ?? $settings['api_endpoint'] ?? 'https://api.ailinux.me';
    return rtrim((string)$endpoint, '/');
}

function nova_copa_ensure_user(string $email, string $name = ''): int {
    $email = sanitize_email($email);
    if (!$email) {
        return 0;
    }

    $found = email_exists($email);
    if ($found) {
        return (int)$found;
    }

    $base = sanitize_user(strstr($email, '@', true)) ?: 'nova';
    $username = $base;
    $i = 1;

    while (username_exists($username)) {
        $username = $base . $i++;
    }

    $uid = wp_create_user($username, wp_generate_password(24, true), $email);
    if (is_wp_error($uid)) {
        return 0;
    }

    if ($name !== '') {
        wp_update_user([
            'ID' => $uid,
            'display_name' => sanitize_text_field($name),
        ]);
    }

    update_user_meta($uid, 'nova_ailinux_email', $email);
    update_user_meta($uid, 'nova_tier', 'free');

    return (int)$uid;
}

function nova_copa_normalize_entitlements($raw): array {
    if (is_string($raw)) {
        $raw = [$raw => true];
    }

    if (!is_array($raw)) {
        return [];
    }

    $out = [];

    foreach ($raw as $k => $v) {
        if (is_int($k)) {
            $key = sanitize_key((string)$v);
            if ($key !== '') {
                $out[$key] = true;
            }
        } else {
            $key = sanitize_key((string)$k);
            if ($key !== '') {
                $out[$key] = (bool)$v;
            }
        }
    }

    return $out;
}

function nova_copa_set_user_state(int $uid, array $data): array {
    if (!empty($data['tier'])) {
        update_user_meta($uid, 'nova_tier', sanitize_text_field($data['tier']));
    }

    if (!empty($data['client_id'])) {
        update_user_meta($uid, 'nova_client_id', sanitize_text_field($data['client_id']));
    }

    if (!empty($data['customer_id'])) {
        update_user_meta($uid, 'nova_payment_customer_id', sanitize_text_field($data['customer_id']));
    }

    if (!empty($data['email'])) {
        update_user_meta($uid, 'nova_ailinux_email', sanitize_email($data['email']));
    }

    $existing = get_user_meta($uid, 'nova_entitlements', true);
    $existing = is_array($existing) ? nova_copa_normalize_entitlements($existing) : [];

    $incoming = nova_copa_normalize_entitlements($data['nova_entitlements'] ?? $data['entitlements'] ?? $data['extra'] ?? []);
    $merged = array_merge($existing, $incoming);

    if ($merged) {
        update_user_meta($uid, 'nova_entitlements', $merged);
    }

    return [
        'tier' => get_user_meta($uid, 'nova_tier', true) ?: 'free',
        'nova_entitlements' => (array)(get_user_meta($uid, 'nova_entitlements', true) ?: []),
    ];
}

function nova_copa_sync_backend(int $uid): array {
    $user = get_userdata($uid);
    if (!$user) {
        return [
            'ok' => false,
            'error' => 'wp_user_missing',
        ];
    }

    $payload = [
        'email' => $user->user_email,
        'name' => $user->display_name,
        'tier' => get_user_meta($uid, 'nova_tier', true) ?: 'free',
        'billing' => !empty(get_user_meta($uid, 'nova_entitlements', true)),
        'nova_entitlements' => (array)(get_user_meta($uid, 'nova_entitlements', true) ?: []),
        'source' => 'wordpress_admin_or_webhook',
    ];

    $results = [];

    foreach ([
        '/v1/admin/users/entitlements',
        '/v1/users/entitlements',
        '/v1/user/entitlements',
    ] as $path) {
        $url = nova_copa_backend_base() . $path;
        $response = wp_remote_post($url, [
            'headers' => nova_copa_headers_for_backend(),
            'body' => wp_json_encode($payload),
            'timeout' => 12,
            'blocking' => true,
        ]);

        if (is_wp_error($response)) {
            $results[] = [
                'path' => $path,
                'ok' => false,
                'error' => $response->get_error_message(),
            ];
            continue;
        }

        $code = (int) wp_remote_retrieve_response_code($response);
        $body = wp_remote_retrieve_body($response);

        $results[] = [
            'path' => $path,
            'ok' => ($code >= 200 && $code < 300),
            'http_code' => $code,
            'body' => json_decode($body, true) ?: $body,
        ];

        if ($code >= 200 && $code < 300) {
            update_option('nova_last_triforce_sync', [
                'timestamp' => time(),
                'email' => $user->user_email,
                'path' => $path,
                'http_code' => $code,
            ], false);

            return [
                'ok' => true,
                'path' => $path,
                'http_code' => $code,
                'results' => $results,
            ];
        }
    }

    update_option('nova_last_triforce_sync_error', [
        'timestamp' => time(),
        'email' => $user->user_email,
        'results' => $results,
    ], false);

    return [
        'ok' => false,
        'results' => $results,
    ];
}

function nova_copa_verify_ls_signature(WP_REST_Request $request): bool {
    $secret = defined('NOVA_LS_WEBHOOK_SECRET') ? (string)NOVA_LS_WEBHOOK_SECRET : '';
    if ($secret === '') {
        $secret = getenv('LEMONSQUEEZY_WEBHOOK_SECRET') ?: '';
    }

    if ($secret === '') {
        return false;
    }

    $sig = $request->get_header('x-signature');
    if (!$sig) {
        return false;
    }

    $expected = hash_hmac('sha256', $request->get_body(), $secret);
    return hash_equals($expected, strtolower(trim($sig)));
}

function nova_copa_shared_secret_ok(WP_REST_Request $request): bool {
    $given = $request->get_header('x-nova-webhook-secret') ?: $request->get_header('x-internal-key');

    if (!is_string($given) || $given === '') {
        return false;
    }

    $candidates = [];

    foreach (['NOVA_LS_WEBHOOK_SECRET', 'NOVA_AI_INTERNAL_KEY'] as $constant) {
        if (defined($constant) && constant($constant)) {
            $candidates[] = (string) constant($constant);
        }
    }

    foreach (['NOVA_LS_WEBHOOK_SECRET', 'LEMONSQUEEZY_WEBHOOK_SECRET', 'NOVA_AI_INTERNAL_KEY', 'WEBHOOK_SECRET'] as $env_key) {
        $value = getenv($env_key);
        if (is_string($value) && $value !== '') {
            $candidates[] = $value;
        }
    }

    foreach (array_unique($candidates) as $secret) {
        if ($secret !== '' && hash_equals($secret, $given)) {
            return true;
        }
    }

    return false;
}

function nova_copa_handle_payment(WP_REST_Request $request): WP_REST_Response {
    $signed = $request->get_header('x-signature');

    if ($signed && !nova_copa_verify_ls_signature($request)) {
        return new WP_REST_Response(['ok' => false, 'error' => 'invalid_signature'], 401);
    }

    if (!$signed && !nova_copa_admin_allowed() && !nova_copa_shared_secret_ok($request)) {
        return new WP_REST_Response(['ok' => false, 'error' => 'unauthorized'], 401);
    }

    $payload = json_decode($request->get_body() ?: '{}', true);
    if (!is_array($payload)) {
        return new WP_REST_Response(['ok' => false, 'error' => 'invalid_json'], 400);
    }

    $attrs = $payload['data']['attributes'] ?? [];

    $email = sanitize_email(
        $attrs['user_email']
        ?? $payload['email']
        ?? $payload['user_email']
        ?? $payload['customer_email']
        ?? ''
    );

    if (!$email) {
        return new WP_REST_Response(['ok' => false, 'error' => 'missing_email'], 400);
    }

    $name = sanitize_text_field(
        $attrs['user_name']
        ?? $attrs['customer_name']
        ?? $payload['name']
        ?? $payload['customer_name']
        ?? ''
    );

    $uid = nova_copa_ensure_user($email, $name);
    if ($uid <= 0) {
        return new WP_REST_Response(['ok' => false, 'error' => 'user_create_failed'], 500);
    }

    $product_id = (string)($attrs['product_id'] ?? '');
    $variant_id = (string)($attrs['variant_id'] ?? '');

    $product_name = strtolower((string)(
        $attrs['first_order_item']['product_name']
        ?? $attrs['product_name']
        ?? $payload['product_name']
        ?? ''
    ));

    $variant_name = strtolower((string)(
        $attrs['first_order_item']['variant_name']
        ?? $attrs['variant_name']
        ?? $payload['variant_name']
        ?? ''
    ));

    $blob = strtolower($product_id . ' ' . $variant_id . ' ' . $product_name . ' ' . $variant_name);

    $entitlements = nova_copa_normalize_entitlements(
        $payload['nova_entitlements'] ?? $payload['entitlements'] ?? []
    );

    if (!$entitlements || str_contains($blob, 'copa') || str_contains($blob, 'ocr')) {
        $entitlements['copa_ocr'] = true;
    }

    if ($product_id !== '') {
        $entitlements['ls_product_' . sanitize_key($product_id)] = true;
    }

    if ($variant_id !== '') {
        $entitlements['ls_variant_' . sanitize_key($variant_id)] = true;
    }

    $state = nova_copa_set_user_state($uid, [
        'email' => $email,
        'tier' => sanitize_text_field($payload['tier'] ?? 'free'),
        'customer_id' => sanitize_text_field($attrs['customer_id'] ?? $payload['customer_id'] ?? ''),
        'nova_entitlements' => $entitlements,
    ]);

    $sync = nova_copa_sync_backend($uid);

    update_option('nova_last_webhook', [
        'timestamp' => time(),
        'event_name' => $payload['meta']['event_name'] ?? 'payment_success',
        'event_id' => sanitize_text_field($payload['data']['id'] ?? $payload['id'] ?? $payload['order_id'] ?? ''),
        'user_id' => $uid,
        'action' => 'purchase',
    ], false);

    return new WP_REST_Response([
        'ok' => true,
        'user_id' => $uid,
        'email' => $email,
        'state' => $state,
        'sync' => $sync ?? null,
    ], 200);
}

function nova_copa_admin_upsert(WP_REST_Request $request): WP_REST_Response {
    $p = $request->get_json_params() ?: [];
    $email = sanitize_email($p['email'] ?? '');

    if (!$email) {
        return new WP_REST_Response(['ok' => false, 'error' => 'email_required'], 400);
    }

    $uid = nova_copa_ensure_user($email, sanitize_text_field($p['name'] ?? ''));
    if ($uid <= 0) {
        return new WP_REST_Response(['ok' => false, 'error' => 'user_create_failed'], 500);
    }

    $state = nova_copa_set_user_state($uid, [
        'email' => $email,
        'tier' => sanitize_text_field($p['tier'] ?? 'free'),
        'client_id' => sanitize_text_field($p['client_id'] ?? ''),
        'customer_id' => sanitize_text_field($p['customer_id'] ?? ''),
        'nova_entitlements' => $p['nova_entitlements'] ?? $p['entitlements'] ?? [],
    ]);

    $sync = nova_copa_sync_backend($uid);

    return new WP_REST_Response([
        'ok' => true,
        'user_id' => $uid,
        'email' => $email,
        'state' => $state,
        'sync' => $sync ?? null,
    ], 200);
}

function nova_copa_admin_entitlements(WP_REST_Request $request): WP_REST_Response {
    $p = $request->get_json_params() ?: [];
    $email = sanitize_email($p['email'] ?? '');

    if (!$email) {
        return new WP_REST_Response(['ok' => false, 'error' => 'email_required'], 400);
    }

    $uid = nova_copa_ensure_user($email, sanitize_text_field($p['name'] ?? ''));

    $entitlements = $p['nova_entitlements'] ?? $p['entitlements'] ?? [];

    if (isset($p['entitlement'])) {
        $entitlements = [
            sanitize_key((string)$p['entitlement']) => !empty($p['value']),
        ];
    }

    $state = nova_copa_set_user_state($uid, [
        'email' => $email,
        'tier' => sanitize_text_field($p['tier'] ?? ''),
        'nova_entitlements' => $entitlements,
    ]);

    nova_copa_sync_backend($uid);

    return new WP_REST_Response([
        'ok' => true,
        'user_id' => $uid,
        'email' => $email,
        'state' => $state,
    ], 200);
}


function nova_copa_auth_validate(WP_REST_Request $request): WP_REST_Response {
    if (!nova_copa_shared_secret_ok($request)) {
        return new WP_REST_Response(['ok' => false, 'error' => 'unauthorized'], 401);
    }

    $p = $request->get_json_params() ?: [];
    $email = sanitize_email($p['email'] ?? '');
    $password = (string)($p['password'] ?? '');

    if (!$email || $password === '') {
        return new WP_REST_Response(['ok' => false, 'error' => 'email_password_required'], 400);
    }

    $user = get_user_by('email', $email);

    if (!$user) {
        return new WP_REST_Response(['ok' => false, 'error' => 'invalid_credentials'], 401);
    }

    $auth = wp_authenticate($user->user_login, $password);

    if (is_wp_error($auth)) {
        return new WP_REST_Response(['ok' => false, 'error' => 'invalid_credentials'], 401);
    }

    return new WP_REST_Response([
        'ok' => true,
        'user' => [
            'id' => $user->ID,
            'email' => $user->user_email,
            'name' => $user->display_name ?: $user->user_login,
            'tier' => get_user_meta($user->ID, 'nova_tier', true) ?: 'free',
            // Organizational authority is separate from subscription tier.
            // These values are derived server-side from WordPress state and
            // consumed by TriForce only after this shared-secret + password
            // validation succeeds.
            'wordpress_roles' => array_values((array)$user->roles),
            'wordpress_can_admin' => user_can($user, 'manage_options'),
            'authority_role' => sanitize_key((string)get_user_meta($user->ID, 'nova_authority_role', true)),
            'nova_entitlements' => (array)(get_user_meta($user->ID, 'nova_entitlements', true) ?: []),
        ],
    ], 200);
}

add_action('rest_api_init', function () {
    $ns = 'nova-ai/v1';

    // The canonical signed Lemon Squeezy route is owned by PaymentsService.
    // Keep only legacy aliases here so this compatibility layer cannot override
    // idempotency, paid-status checks, product mapping, or refund handling.
    foreach ([
        '/webhook/payment-success',
        '/payments/payment-success',
        '/payment-success',
        '/legacy/payment-success',
        '/lemonsqueezy/webhook',
        '/lemon/webhook',
    ] as $route) {
        register_rest_route($ns, $route, [
            'methods' => 'POST',
            'callback' => 'nova_copa_handle_payment',
            'permission_callback' => '__return_true',
        ], true);
    }

    register_rest_route($ns, '/auth/validate', [
        'methods' => 'POST',
        'callback' => 'nova_copa_auth_validate',
        'permission_callback' => '__return_true',
    ], true);

    register_rest_route($ns, '/admin/users/upsert', [
        'methods' => 'POST',
        'callback' => 'nova_copa_admin_upsert',
        'permission_callback' => 'nova_copa_admin_allowed',
    ], true);

    register_rest_route($ns, '/admin/users/entitlements', [
        'methods' => 'POST',
        'callback' => 'nova_copa_admin_entitlements',
        'permission_callback' => 'nova_copa_admin_allowed',
    ], true);
}, 30);


add_action('wp_login', function ($user_login, $user) {
    if (!$user || empty($user->ID) || empty($user->user_email)) {
        return;
    }

    $uid = (int) $user->ID;
    $email = sanitize_email($user->user_email);

    if (!$email) {
        return;
    }

    update_user_meta($uid, 'nova_ailinux_email', $email);

    foreach ([
        'nova_removed',
        'nova_deleted',
        'nova_account_removed',
        'account_removed',
        'deleted',
        'removed',
    ] as $meta_key) {
        delete_user_meta($uid, $meta_key);
    }

    // Preserve existing entitlements. If missing, keep free state.
    if (get_user_meta($uid, 'nova_tier', true) === '') {
        update_user_meta($uid, 'nova_tier', 'free');
    }

    if (function_exists('nova_copa_sync_backend')) {
        nova_copa_sync_backend($uid);
    }
}, 20, 2);

