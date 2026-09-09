<?php
/**
 * Plugin Name: AILinux TriForce Settings Bridge
 * Description: Gemeinsame, validierte WordPress-Settings fuer AILinux/TriForce-Plugins.
 * Version: 1.0.0
 */
if (!defined('ABSPATH')) { exit; }
const AILINUX_TRIFORCE_OPTION = 'ailinux_triforce_settings';
const AILINUX_TRIFORCE_MASK = '********';
function ailinux_triforce_defaults(): array {
    $legacy = get_option('nova_ai_settings', []);
    if (!is_array($legacy)) { $legacy = []; }
    return [
        'api_endpoint' => $legacy['api_endpoint'] ?? 'https://api.ailinux.me',
        'api_endpoint_internal' => $legacy['api_endpoint_internal'] ?? '',
        'mcp_endpoint' => $legacy['mcp_endpoint'] ?? ($legacy['api_endpoint'] ?? 'https://api.ailinux.me'),
        'login_url' => $legacy['login_url'] ?? 'https://login.ailinux.me',
        'internal_key' => $legacy['internal_key'] ?? '',
        'mcp_user' => defined('NOVA_MCP_USER') ? NOVA_MCP_USER : (getenv('MCP_OAUTH_USER') ?: ''),
        'mcp_pass' => defined('NOVA_MCP_PASS') ? NOVA_MCP_PASS : (getenv('MCP_OAUTH_PASS') ?: ''),
        'webhook_secret' => defined('AILINUX_WEBHOOK_SECRET') ? AILINUX_WEBHOOK_SECRET : (getenv('WEBHOOK_SECRET') ?: ''),
    ];
}
function ailinux_triforce_settings(): array {
    $stored = get_option(AILINUX_TRIFORCE_OPTION, []);
    if (!is_array($stored)) { $stored = []; }
    return array_replace(ailinux_triforce_defaults(), $stored);
}
function ailinux_triforce_setting(string $key, string $default = ''): string {
    $settings = ailinux_triforce_settings();
    $value = $settings[$key] ?? $default;
    return is_scalar($value) ? (string)$value : $default;
}
function ailinux_triforce_api_base(bool $internal = false): string {
    $key = $internal ? 'api_endpoint_internal' : 'api_endpoint';
    $value = trim(ailinux_triforce_setting($key));
    if ($internal && $value === '') { $value = trim(ailinux_triforce_setting('api_endpoint', 'https://api.ailinux.me')); }
    return rtrim($value, '/');
}
function ailinux_triforce_api_v1(bool $internal = false): string {
    $base = ailinux_triforce_api_base($internal);
    return preg_match('~/v1$~', $base) ? $base : $base . '/v1';
}
function ailinux_triforce_mcp_base(): string {
    $value = trim(ailinux_triforce_setting('mcp_endpoint'));
    return rtrim($value !== '' ? $value : ailinux_triforce_api_base(false), '/');
}
function ailinux_triforce_normalize_url($value): string {
    $value = trim((string)$value);
    if ($value === '') { return ''; }
    $value = esc_url_raw($value, ['http', 'https']);
    return $value === '' ? '' : rtrim($value, '/');
}
function ailinux_triforce_sanitize_settings($input): array {
    $current = ailinux_triforce_settings();
    $input = is_array($input) ? $input : [];
    $out = $current;
    foreach (['api_endpoint','api_endpoint_internal','mcp_endpoint','login_url'] as $key) {
        if (array_key_exists($key, $input)) { $out[$key] = ailinux_triforce_normalize_url($input[$key]); }
    }
    if (array_key_exists('mcp_user', $input)) { $out['mcp_user'] = sanitize_text_field((string)$input['mcp_user']); }
    foreach (['internal_key','mcp_pass','webhook_secret'] as $key) {
        if (!array_key_exists($key, $input)) { continue; }
        $value = trim((string)$input[$key]);
        if ($value === '' || hash_equals(AILINUX_TRIFORCE_MASK, $value)) { continue; }
        $out[$key] = sanitize_text_field($value);
    }
    return $out;
}
function ailinux_triforce_sync_legacy_nova(array $settings): void {
    $nova = get_option('nova_ai_settings', []);
    if (!is_array($nova)) { $nova = []; }
    $map = ['api_endpoint'=>'api_endpoint','api_endpoint_internal'=>'api_endpoint_internal','mcp_endpoint'=>'mcp_endpoint','login_url'=>'login_url','internal_key'=>'internal_key'];
    $changed = false;
    foreach ($map as $shared => $legacy) {
        if (array_key_exists($shared, $settings) && ($nova[$legacy] ?? null) !== $settings[$shared]) { $nova[$legacy] = $settings[$shared]; $changed = true; }
    }
    if ($changed) { update_option('nova_ai_settings', $nova, false); }
}
add_action('admin_init', function(): void {
    register_setting('ailinux_triforce', AILINUX_TRIFORCE_OPTION, ['type'=>'array','sanitize_callback'=>'ailinux_triforce_sanitize_settings','default'=>[]]);
});
add_action('update_option_' . AILINUX_TRIFORCE_OPTION, function($old, $new): void { if (is_array($new)) { ailinux_triforce_sync_legacy_nova($new); } }, 10, 2);
function ailinux_triforce_register_admin_page(): void {
    add_options_page('TriForce Settings', 'TriForce Settings', 'manage_options', 'ailinux-triforce-settings', 'ailinux_triforce_render_admin_page');
    add_submenu_page('ailinux', 'TriForce Settings', 'TriForce Settings', 'manage_options', 'ailinux-triforce-settings', 'ailinux_triforce_render_admin_page');
}
add_action('admin_menu', 'ailinux_triforce_register_admin_page', 99);
function ailinux_triforce_test_connection(): array {
    $base = ailinux_triforce_api_base(true);
    if ($base === '') { return ['ok'=>false,'message'=>'Kein API-Endpoint konfiguriert.']; }
    $headers = [];
    $key = ailinux_triforce_setting('internal_key');
    if ($key !== '') { $headers['X-Internal-Key'] = $key; }
    $response = wp_remote_get($base . '/health', ['timeout'=>8,'headers'=>$headers]);
    if (is_wp_error($response)) { return ['ok'=>false,'message'=>$response->get_error_message()]; }
    $code = wp_remote_retrieve_response_code($response);
    return ['ok'=>$code >= 200 && $code < 300,'message'=>'HTTP ' . $code . ' · ' . $base . '/health'];
}
function ailinux_triforce_render_admin_page(): void {
    if (!current_user_can('manage_options')) { return; }
    $settings = ailinux_triforce_settings();
    $test = null;
    if (isset($_POST['ailinux_triforce_test'])) { check_admin_referer('ailinux_triforce_test_connection'); $test = ailinux_triforce_test_connection(); }
    ?>
    <div class="wrap"><h1>TriForce Settings</h1>
      <p>Gemeinsame Integrationswerte fuer Nova AI Frontend, AILinux User Management und weitere AILinux-Plugins. Die Serverkonfiguration selbst bleibt in <code>/etc/triforce/triforce.env</code>.</p>
      <?php if ($test): ?><div class="notice <?php echo $test['ok'] ? 'notice-success' : 'notice-error'; ?> is-dismissible"><p><?php echo esc_html($test['message']); ?></p></div><?php endif; ?>
      <form method="post" action="options.php"><?php settings_fields('ailinux_triforce'); ?>
      <table class="form-table" role="presentation">
      <tr><th><label for="tf-api">TriForce API (oeffentlich)</label></th><td><input id="tf-api" class="regular-text" type="url" name="<?php echo esc_attr(AILINUX_TRIFORCE_OPTION); ?>[api_endpoint]" value="<?php echo esc_attr($settings['api_endpoint']); ?>"></td></tr>
      <tr><th><label for="tf-api-internal">TriForce API (intern)</label></th><td><input id="tf-api-internal" class="regular-text" type="text" name="<?php echo esc_attr(AILINUX_TRIFORCE_OPTION); ?>[api_endpoint_internal]" value="<?php echo esc_attr($settings['api_endpoint_internal']); ?>"><p class="description">Leer = oeffentliche API verwenden.</p></td></tr>
      <tr><th><label for="tf-mcp">MCP Endpoint</label></th><td><input id="tf-mcp" class="regular-text" type="url" name="<?php echo esc_attr(AILINUX_TRIFORCE_OPTION); ?>[mcp_endpoint]" value="<?php echo esc_attr($settings['mcp_endpoint']); ?>"></td></tr>
      <tr><th><label for="tf-login">AILinux Login URL</label></th><td><input id="tf-login" class="regular-text" type="url" name="<?php echo esc_attr(AILINUX_TRIFORCE_OPTION); ?>[login_url]" value="<?php echo esc_attr($settings['login_url']); ?>"></td></tr>
      <tr><th><label for="tf-internal-key">Internal Key</label></th><td><input id="tf-internal-key" class="regular-text" type="password" autocomplete="new-password" name="<?php echo esc_attr(AILINUX_TRIFORCE_OPTION); ?>[internal_key]" value="<?php echo !empty($settings['internal_key']) ? esc_attr(AILINUX_TRIFORCE_MASK) : ''; ?>"><p class="description">Leer/******** behaelt das vorhandene Secret.</p></td></tr>
      <tr><th><label for="tf-mcp-user">MCP Benutzer</label></th><td><input id="tf-mcp-user" class="regular-text" type="text" name="<?php echo esc_attr(AILINUX_TRIFORCE_OPTION); ?>[mcp_user]" value="<?php echo esc_attr($settings['mcp_user']); ?>"></td></tr>
      <tr><th><label for="tf-mcp-pass">MCP Passwort</label></th><td><input id="tf-mcp-pass" class="regular-text" type="password" autocomplete="new-password" name="<?php echo esc_attr(AILINUX_TRIFORCE_OPTION); ?>[mcp_pass]" value="<?php echo !empty($settings['mcp_pass']) ? esc_attr(AILINUX_TRIFORCE_MASK) : ''; ?>"></td></tr>
      <tr><th><label for="tf-webhook">Webhook Secret</label></th><td><input id="tf-webhook" class="regular-text" type="password" autocomplete="new-password" name="<?php echo esc_attr(AILINUX_TRIFORCE_OPTION); ?>[webhook_secret]" value="<?php echo !empty($settings['webhook_secret']) ? esc_attr(AILINUX_TRIFORCE_MASK) : ''; ?>"><p class="description">Ohne Secret werden signierte AILinux-User-Webhooks verweigert.</p></td></tr>
      </table><?php submit_button('TriForce Settings speichern'); ?></form><hr>
      <form method="post"><?php wp_nonce_field('ailinux_triforce_test_connection'); ?><input type="hidden" name="ailinux_triforce_test" value="1"><?php submit_button('Verbindung testen','secondary','submit',false); ?></form>
    </div><?php
}
