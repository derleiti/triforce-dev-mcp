<?php
/**
 * AILinux Account Suite — [ailinux_account_suite]
 * Vollständige Account-Management-Suite im WordPress-Theme.
 *
 * @package NovAI
 * @version 1.0.0
 */
namespace NovAI\Services;
defined('ABSPATH') || exit;

class AccountSuiteService {
    private static ?self $instance = null;
    public static function instance(): self {
        if (self::$instance === null) self::$instance = new self();
        return self::$instance;
    }
    private function __construct() {
        add_shortcode('ailinux_account_suite', [$this, 'render']);
        add_action('wp_enqueue_scripts',       [$this, 'enqueue_assets']);
        // Exclude account-suite JS from Cloudflare Rocket Loader
        add_filter('script_loader_tag', function($tag, $handle) {
            if (in_array($handle, ['nova-account-suite', 'cf-turnstile'])) {
                return str_replace(' src=', ' data-cfasync="false" src=', $tag);
            }
            return $tag;
        }, 10, 2);
        add_action('rest_api_init',            [$this, 'register_rest_routes']);
    }

    public function enqueue_assets(): void {
        global $post;
        if (!$post || !has_shortcode($post->post_content, 'ailinux_account_suite')) return;
        $v  = @filemtime(NOVA_AI_PLUGIN_DIR.'assets/account-suite.js')  ?: NOVA_AI_VERSION;
        $cv = @filemtime(NOVA_AI_PLUGIN_DIR.'assets/account-suite.css') ?: NOVA_AI_VERSION;
        wp_enqueue_style ('nova-account-suite', NOVA_AI_PLUGIN_URL.'assets/account-suite.css', [], $cv);
        wp_enqueue_script('nova-account-suite', NOVA_AI_PLUGIN_URL.'assets/account-suite.js',  [], $v, true);
        // Cloudflare Turnstile for login/register forms
        if (!is_user_logged_in()) {
            wp_enqueue_script('cf-turnstile', 'https://challenges.cloudflare.com/turnstile/v0/api.js', [], null, true);
        }
        $settings = get_option('nova_ai_settings', []);

        $nas_config = [

            'apiBase'      => rest_url('nova-ai/v1'),

            'nonce'        => wp_create_nonce('wp_rest'),

            'loginUrl'     => home_url('/account/'),

            'triforceApi'  => rtrim($settings['api_endpoint'] ?? 'https://api.ailinux.me', '/') . '/v1/auth',

            'isLoggedIn'   => is_user_logged_in(),

            'shopUrl'      => 'https://ailinux.me/shop',

            'wpLoginSync'  => rest_url('nova-ai/v1/auth/wp-login'),

            'turnstileKey' => get_option('cfturnstile_key', ''),

        ];

        add_action('wp_footer', function() use ($nas_config) {

            echo '<script data-cfasync="false">var novaAccountConfig=' . wp_json_encode($nas_config) . ';</script>';

        }, 5);
    }

    public function register_rest_routes(): void {
        $ns = 'nova-ai/v1';
        $logged_in = function() { return is_user_logged_in(); };
        register_rest_route($ns, '/subscription',        ['methods'=>'GET',  'callback'=>[$this,'api_get_subscription'],   'permission_callback'=>$logged_in]);
        register_rest_route($ns, '/subscription/cancel', ['methods'=>'POST', 'callback'=>[$this,'api_cancel_subscription'],'permission_callback'=>$logged_in]);
        register_rest_route($ns, '/purchases',           ['methods'=>'GET',  'callback'=>[$this,'api_get_purchases'],      'permission_callback'=>$logged_in]);
        register_rest_route($ns, '/profile/update',      ['methods'=>'POST', 'callback'=>[$this,'api_update_profile'],     'permission_callback'=>$logged_in]);
    }

    public function api_get_subscription(\WP_REST_Request $r): \WP_REST_Response {
        if (!is_user_logged_in()) return new \WP_REST_Response(['ok'=>false,'error'=>'not_logged_in'], 401);
        $uid  = get_current_user_id();
        $tier = get_user_meta($uid,'nova_tier',true) ?: 'free';
        $sid  = get_user_meta($uid,'nova_payment_subscription_id',true) ?: '';
        $cid  = get_user_meta($uid,'nova_client_id',true) ?: '';
        if ($cid) {
            $s    = get_option('nova_ai_settings',[]);
            $base = $s['api_endpoint_internal'] ?? $s['api_endpoint'] ?? 'https://api.ailinux.me';
            $resp = wp_remote_get(rtrim($base,'/')  .'/v1/tiers/subscription/'.urlencode($cid), ['timeout'=>8]);
            if (!is_wp_error($resp) && wp_remote_retrieve_response_code($resp)===200)
                return new \WP_REST_Response(['ok'=>true,'data'=>json_decode(wp_remote_retrieve_body($resp),true)??[],'source'=>'live']);
        }
        return new \WP_REST_Response(['ok'=>true,'data'=>['tier'=>$tier,'subscription_id'=>$sid,'status'=>$sid?'active':'none'],'source'=>'cached']);
    }

    public function api_cancel_subscription(\WP_REST_Request $r): \WP_REST_Response {
        if (!is_user_logged_in()) return new \WP_REST_Response(['ok'=>false,'error'=>'not_logged_in'], 401);
        $uid  = get_current_user_id();
        $tier = get_user_meta($uid,'nova_tier',true) ?: 'free';
        if ($tier === 'free') return new \WP_REST_Response(['ok'=>false,'error'=>'already_free'], 400);

        $sid = get_user_meta($uid,'nova_payment_subscription_id',true) ?: '';

        // Gifted/manual accounts: no subscription_id → downgrade directly
        if (!$sid) {
            update_user_meta($uid, 'nova_tier', 'free');
            return new \WP_REST_Response(['ok'=>true,'message'=>'Subscription cancelled','source'=>'manual']);
        }

        // LemonSqueezy accounts: cancel via API
        $s    = get_option('nova_ai_settings',[]);
        $base = $s['api_endpoint_internal'] ?? $s['api_endpoint'] ?? 'https://api.ailinux.me';
        $resp = wp_remote_post(rtrim($base,'/').'/v1/tiers/cancel',
            ['body'=>wp_json_encode(['subscription_id'=>$sid]),'headers'=>['Content-Type'=>'application/json'],'timeout'=>10]);
        if (is_wp_error($resp)) return new \WP_REST_Response(['ok'=>false,'error'=>$resp->get_error_message()], 502);
        $code = wp_remote_retrieve_response_code($resp);
        if ($code===200) { delete_user_meta($uid,'nova_payment_subscription_id'); update_user_meta($uid,'nova_tier','free'); }
        return new \WP_REST_Response(['ok'=>$code===200,'data'=>json_decode(wp_remote_retrieve_body($resp),true)??[]], $code);
    }

    public function api_get_purchases(\WP_REST_Request $r): \WP_REST_Response {
        if (!is_user_logged_in()) return new \WP_REST_Response(['ok'=>false,'error'=>'not_logged_in'], 401);
        $uid = get_current_user_id();
        $purchases = EntitlementsService::get_purchase_history($uid);
        $dl = wp_remote_get(NOVA_AI_BACKEND.'/health', ['timeout'=>6]);
        $files = (!is_wp_error($dl) && wp_remote_retrieve_response_code($dl)===200) ? (json_decode(wp_remote_retrieve_body($dl),true)['files'] ?? []) : [];
        return new \WP_REST_Response(['ok'=>true,'purchases'=>$purchases,'downloads'=>$files,'source'=>'wordpress']);
    }

    public function api_update_profile(\WP_REST_Request $r): \WP_REST_Response {
        if (!is_user_logged_in()) return new \WP_REST_Response(['ok'=>false,'error'=>'not_logged_in'], 401);
        $uid = get_current_user_id();
        $p   = $r->get_json_params();
        $up  = ['ID'=>$uid];
        if (!empty($p['display_name'])) $up['display_name'] = sanitize_text_field($p['display_name']);
        if (!empty($p['new_password']) && strlen($p['new_password'])>=8) $up['user_pass'] = $p['new_password'];
        if (count($up)<=1) return new \WP_REST_Response(['ok'=>false,'error'=>'nothing_to_update'], 400);
        $res = wp_update_user($up);
        if (is_wp_error($res)) return new \WP_REST_Response(['ok'=>false,'error'=>$res->get_error_message()], 400);
        return new \WP_REST_Response(['ok'=>true,'message'=>'Profile updated']);
    }

    public function render($atts): string {
        $atts      = shortcode_atts(['class'=>'','redirect_after_login'=>''], $atts);
        $li        = is_user_logged_in();
        $user      = $li ? wp_get_current_user() : null;
        $tier      = $li ? (get_user_meta($user->ID,'nova_tier',true) ?: 'free') : 'free';
        $is_admin  = $li && in_array('administrator',(array)($user->roles??[]));
        $redirect  = esc_url($atts['redirect_after_login'] ?: home_url('/'));
        ob_start(); ?>
<div class="nas-wrap<?= $atts['class']?' '.esc_attr($atts['class']):'' ?>" id="nova-account-suite" data-logged-in="<?= $li?'1':'0' ?>" data-redirect="<?= $redirect ?>">

<?php if (!$li): ?>
<div class="nas-auth-panel" id="nas-auth">
  <div class="nas-auth-inner">
    <div class="nas-logo"><span>🤖</span><h2>AILinux Account</h2><p>TriForce AI Platform</p></div>
    <div class="nas-tabs" role="tablist">
      <button class="nas-tab active" data-tab="login">Sign In</button>
      <button class="nas-tab" data-tab="register">Sign Up</button>
    </div>
    <div class="nas-google-login">
      <a class="nas-google-btn" href="<?= esc_url(add_query_arg([
          'google'   => '1',
          'redirect' => home_url('/account/'),
      ], 'https://login.ailinux.me/')) ?>" data-no-swup>
        <svg aria-hidden="true" viewBox="0 0 24 24" width="20" height="20">
          <path fill="#4285F4" d="M21.6 12.23c0-.71-.06-1.4-.18-2.06H12v3.9h5.38a4.6 4.6 0 0 1-2 3.02v2.53h3.24c1.9-1.75 2.98-4.32 2.98-7.39Z"/>
          <path fill="#34A853" d="M12 22c2.7 0 4.98-.9 6.64-2.43l-3.24-2.53c-.9.6-2.05.96-3.4.96-2.61 0-4.82-1.76-5.61-4.13H3.04v2.61A10 10 0 0 0 12 22Z"/>
          <path fill="#FBBC05" d="M6.39 13.87A6.02 6.02 0 0 1 6.08 12c0-.65.11-1.28.31-1.87V7.52H3.04A10 10 0 0 0 2 12c0 1.61.39 3.14 1.04 4.48l3.35-2.61Z"/>
          <path fill="#EA4335" d="M12 6c1.47 0 2.79.5 3.83 1.5l2.87-2.87A9.63 9.63 0 0 0 12 2a10 10 0 0 0-8.96 5.52l3.35 2.61C7.18 7.76 9.39 6 12 6Z"/>
        </svg>
        <span>Continue with Google</span>
      </a>
      <div class="nas-auth-divider"><span>or</span></div>
    </div>
    <div id="nas-msg" class="nas-msg" role="alert" style="display:none"></div>
    <form id="nas-login-form" class="nas-form" novalidate>
      <div class="nas-field"><label>Email</label><input type="email" id="nas-email" placeholder="you@example.com" required autocomplete="email"></div>
      <div class="nas-field nas-pw-wrap"><label>Password</label><input type="password" id="nas-pass" placeholder="••••••••" required autocomplete="current-password"><button type="button" class="nas-pw-toggle">👁</button></div>
      <div class="cf-turnstile" data-sitekey="<?php echo esc_attr(get_option('cfturnstile_key', '')); ?>" data-theme="dark" data-size="normal"></div>
      <button type="submit" class="nas-btn-primary" id="nas-login-btn">Sign In</button>
    </form>
    <form id="nas-register-form" class="nas-form" style="display:none" novalidate>
      <div class="nas-field"><label>Email</label><input type="email" id="nas-reg-email" placeholder="you@example.com" required autocomplete="email"></div>
      <div class="nas-field nas-pw-wrap"><label>Password <small>(min. 8 characters)</small></label><input type="password" id="nas-reg-pass" placeholder="••••••••" minlength="8" required autocomplete="new-password"><button type="button" class="nas-pw-toggle">👁</button></div>
      <div class="nas-field"><label>Name <small>(optional)</small></label><input type="text" id="nas-reg-name" placeholder="Your Name" autocomplete="name"></div>
      <div class="nas-field"><label>Invite Code <small>(optional)</small></label><input type="text" id="nas-reg-code" placeholder="AILINUX2026"></div>
      <div class="cf-turnstile" data-sitekey="<?php echo esc_attr(get_option('cfturnstile_key', '')); ?>" data-theme="dark" data-size="normal"></div>
      <button type="submit" class="nas-btn-primary" id="nas-reg-btn">Create Account</button>
    </form>
  </div>
</div>
<?php else: ?>
<div class="nas-dashboard" id="nas-dashboard">
  <aside class="nas-sidebar">
    <div class="nas-sidebar-user">
      <div class="nas-avatar"><?= esc_html(strtoupper(substr($user->display_name?:$user->user_email,0,1))) ?></div>
      <div class="nas-sidebar-info">
        <strong><?= esc_html($user->display_name?:explode('@',$user->user_email)[0]) ?></strong>
        <small><?= esc_html($user->user_email) ?></small>
        <span class="nas-tier-badge nas-tier-<?= esc_attr($tier) ?>"><?= esc_html(strtoupper($tier)) ?></span>
      </div>
    </div>
    <nav class="nas-nav">
      <button class="nas-nav-item active" data-panel="overview"><span>🏠</span> Overview</button>
      <button class="nas-nav-item" data-panel="subscription"><span>💳</span> Subscription</button>
      <button class="nas-nav-item" data-panel="downloads"><span>📦</span> Downloads</button>
      <button class="nas-nav-item" data-panel="settings"><span>⚙️</span> Settings</button>
      <?php if ($is_admin && is_admin()): ?>
      <div class="nas-nav-separator">— Admin Suite —</div>
      <button class="nas-nav-item" data-panel="admin"><span>📊</span> Admin HQ</button>
      <button class="nas-nav-item" data-panel="system"><span>🖥</span> System</button>
      <button class="nas-nav-item" data-panel="agents"><span>🤖</span> Agents</button>
      <button class="nas-nav-item" data-panel="mcp"><span>🌐</span> MCP Tools</button>
      <button class="nas-nav-item" data-panel="vault"><span>🔑</span> Vault</button>
      <button class="nas-nav-item" data-panel="logs"><span>📜</span> Logs</button>
      <?php endif; ?>
    </nav>
    <div class="nas-sidebar-footer">
      <button class="nas-logout-btn" id="nas-logout">🚪 Sign Out</button>
    </div>
  </aside>
  <main class="nas-main">
    <section class="nas-panel active" id="nas-panel-overview">
      <h2 class="nas-panel-title">Overview</h2>
      <div class="nas-cards">
        <div class="nas-card nas-card-tier">
          <div class="nas-card-label">Current Plan</div>
          <div class="nas-card-value"><span class="nas-tier-badge nas-tier-<?= esc_attr($tier) ?>"><?= esc_html(strtoupper($tier)) ?></span></div>
          <?php if ($tier==='free'): ?><a href="https://ailinux.me/shop" class="nas-upgrade-link">⬆ Upgrade to Subscriber (€35/month)</a><?php endif; ?>
        </div>
        <div class="nas-card"><div class="nas-card-label">Email</div><div class="nas-card-value" style="font-size:.9rem;word-break:break-all"><?= esc_html($user->user_email) ?></div></div>
        <div class="nas-card"><div class="nas-card-label">Client-ID</div><div class="nas-card-value" id="nas-client-id-val" style="font-size:.85rem">Loading…</div></div>
        <div class="nas-card"><div class="nas-card-label">Backend</div><div class="nas-card-value" id="nas-backend-status">⏳</div></div>
      </div>
      <div class="nas-features-box">
        <h3>Your Plan Includes</h3>
        <div class="nas-features-grid" id="nas-features-list"><span>⏳</span></div>
      </div>

    </section>
    <section class="nas-panel" id="nas-panel-subscription">
      <h2 class="nas-panel-title">Subscription</h2>
      <div id="nas-sub-loading" class="nas-loading-box">⏳ Loading…</div>
      <div id="nas-sub-content" style="display:none">
        <div class="nas-cards" id="nas-sub-cards"></div>
        <?php if ($tier !== 'free'): ?>
        <div style="margin:2rem 0">
          <div class="nas-card" style="border:1px solid #2a2a3a">
            <div class="nas-card-label">Current Plan</div>
            <div class="nas-card-value"><span class="nas-tier-badge nas-tier-<?= esc_attr($tier) ?>"><?= esc_html(strtoupper($tier)) ?></span></div>
          </div>
          <div id="nas-cancel-section" style="margin-top:1.5rem;padding-top:1.5rem;border-top:1px solid #2a2a3a">
            <h3 style="color:#ef4444;margin-bottom:.5rem">Cancel Subscription</h3>
            <p class="nas-muted" style="margin-bottom:1rem">Your access remains active until the end of the billing period.</p>
            <button class="nas-btn-danger" id="nas-cancel-sub-btn">Cancel Subscription</button>
          </div>
        </div>
        <?php else: ?>
        <h3 style="margin:2rem 0 1rem">Choose a Plan</h3>
        <div class="nas-plan-grid">
          <div class="nas-plan-card"><div class="nas-plan-name">Free</div><div class="nas-plan-price">0 €/mo</div><ul class="nas-plan-features"><li>✅ Ollama + Groq + Gemini</li><li>✅ Chat, Vision, MCP Tools</li><li>✅ Nova Playground</li><li>✅ 200k Tokens/week</li></ul></div>
          <div class="nas-plan-card nas-plan-featured"><div class="nas-plan-badge">⭐ Recommended</div><div class="nas-plan-name">Subscriber</div><div class="nas-plan-price">35 €/mo</div><ul class="nas-plan-features"><li>✅ 600+ Models (incl. GPT-4o, Claude)</li><li>✅ Swarm Intelligence</li><li>✅ Federation Access</li><li>✅ Multi-Agent Tasks</li><li>✅ 5M Tokens/week</li></ul><a href="https://ailinux.me/shop" class="nas-btn-primary">Subscribe Now</a></div>
        </div>
        <?php endif; ?>
      </div>
    </section>
    <section class="nas-panel" id="nas-panel-downloads">
      <h2 class="nas-panel-title">Downloads &amp; Purchases</h2>
      <div id="nas-dl-loading" class="nas-loading-box">⏳ Loading…</div>
      <div id="nas-dl-content" style="display:none">
        <div id="nas-purchases-list"></div>
        <div id="nas-downloads-table"></div>
      </div>
    </section>
    <section class="nas-panel" id="nas-panel-settings">
      <h2 class="nas-panel-title">Settings</h2>
      <div class="nas-settings-section">
        <h3>Edit Profile</h3>
        <div id="nas-settings-msg" class="nas-msg" style="display:none"></div>
        <form id="nas-settings-form" class="nas-form">
          <div class="nas-field"><label>Display Name</label><input type="text" id="nas-set-name" value="<?= esc_attr($user->display_name) ?>"></div>
          <div class="nas-field nas-pw-wrap"><label>New Password <small>(leave empty = no change)</small></label><input type="password" id="nas-set-pw" placeholder="Min. 8 characters" minlength="8"><button type="button" class="nas-pw-toggle">👁</button></div>
          <button type="submit" class="nas-btn-primary">Save Changes</button>
        </form>
      </div>
      <div class="nas-settings-section">
        <h3>API Access</h3>
        <p class="nas-muted">Client-ID:</p>
        <div class="nas-code-row"><code id="nas-api-client-id">Loading…</code><button class="nas-copy-btn" data-target="nas-api-client-id">📋</button></div>
        <p class="nas-muted" style="margin-top:.75rem">Login-Endpoint:</p>
        <div class="nas-code-row"><code>https://api.ailinux.me/v1/client/login</code><button class="nas-copy-btn" data-clipboard="https://api.ailinux.me/v1/client/login">📋</button></div>
      </div>
    </section>
    <?php if ($is_admin && is_admin()): ?>
    <section class="nas-panel" id="nas-panel-admin">
      <h2 class="nas-panel-title">📊 Admin HQ</h2>
      <div id="nas-admin-overview"><div class="nas-loading-box">⏳ Loading admin overview…</div></div>
      <div class="nas-admin-quick-links" style="margin-top:1.5rem">
        <a href="<?= esc_url(admin_url('admin.php?page=nova-ai')) ?>" class="nas-card nas-card-link" style="display:block;margin-bottom:.5rem"><div class="nas-card-label">🚀 Nova AI WP-Dashboard</div><div class="nas-card-value">Full WordPress admin interface →</div></a>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:.5rem;margin-top:.5rem">
          <a href="<?= esc_url(admin_url()) ?>" class="nas-card nas-card-link"><div class="nas-card-label">⚙ WP Admin</div></a>
          <a href="https://api.ailinux.me/docs" target="_blank" class="nas-card nas-card-link"><div class="nas-card-label">📚 API Docs</div></a>
        </div>
      </div>
    </section>
    <section class="nas-panel" id="nas-panel-system">
      <h2 class="nas-panel-title">🖥 System Status</h2>
      <button class="nas-btn-sm nas-btn-outline" id="nas-sys-refresh" style="margin-bottom:1rem" onclick="(function(){var k='system_'+(document.getElementById('nova-account-suite')?.id||'r');delete window._nasLoaded&&(window._nasLoaded[k]=undefined);})()">🔄 Refresh</button>
      <div id="nas-system-content"><div class="nas-loading-box">⏳ Loading…</div></div>
    </section>
    <section class="nas-panel" id="nas-panel-agents">
      <h2 class="nas-panel-title">🤖 Agents</h2>
      <div id="nas-agents-content"><div class="nas-loading-box">⏳ Loading Agents…</div></div>
    </section>
    <section class="nas-panel" id="nas-panel-mcp">
      <h2 class="nas-panel-title">🌐 MCP Tools</h2>
      <input type="text" id="nas-mcp-filter" placeholder="🔍 Search tools…" class="nas-input-text" style="width:100%;max-width:400px;margin-bottom:1rem">
      <div id="nas-mcp-content"><div class="nas-loading-box">⏳ Loading MCP Tools…</div></div>
      <div id="nas-mcp-call-section" style="margin-top:2rem;padding-top:1.5rem;border-top:1px solid var(--nas-border,#2a2a3a)">
        <h3>▶ Tool ausführen</h3>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-bottom:1rem">
          <div><label style="font-size:.85rem;color:var(--nas-muted,#888);display:block;margin-bottom:.25rem">Tool-Name</label><input type="text" id="nas-mcp-call-tool" placeholder="tool_name" class="nas-input-text" style="width:100%"></div>
          <div><label style="font-size:.85rem;color:var(--nas-muted,#888);display:block;margin-bottom:.25rem">Args (JSON)</label><input type="text" id="nas-mcp-args" placeholder="{}" value="{}" class="nas-input-text" style="width:100%"></div>
        </div>
        <button class="nas-btn-primary" id="nas-mcp-call-btn">▶ Ausführen</button>
        <pre id="nas-mcp-result" class="nas-result-pre" style="margin-top:1rem;max-height:300px;overflow-y:auto"></pre>
      </div>
    </section>
    <section class="nas-panel" id="nas-panel-vault">
      <h2 class="nas-panel-title">🔑 API Vault</h2>
      <div id="nas-vault-content"><div class="nas-loading-box">⏳ Loading…</div></div>
      <div style="margin-top:1.5rem;padding-top:1.5rem;border-top:1px solid var(--nas-border,#2a2a3a)">
        <h3>🔐 Key setzen / aktualisieren</h3>
        <div class="nas-settings-section" style="padding:0">
          <div class="nas-field" style="margin-bottom:.75rem"><label>Key Name</label><input type="text" id="nas-vault-key-name" placeholder="ANTHROPIC_API_KEY" class="nas-input-text" style="width:100%"></div>
          <div class="nas-field" style="margin-bottom:.75rem"><label>Value</label><input type="password" id="nas-vault-key-value" placeholder="sk-…" class="nas-input-text" style="width:100%"></div>
          <button class="nas-btn-primary" id="nas-vault-set-btn">💾 Save</button>
          <div id="nas-vault-msg" class="nas-msg" style="display:none;margin-top:.75rem"></div>
        </div>
      </div>
    </section>
    <section class="nas-panel" id="nas-panel-logs">
      <h2 class="nas-panel-title">📜 System Logs</h2>
      <div style="display:flex;gap:.75rem;align-items:center;margin-bottom:1rem;flex-wrap:wrap">
        <select id="nas-logs-cat" class="nas-input-text" style="min-width:120px">
          <option value="all">Alle</option>
          <option value="api">API</option>
          <option value="llm">LLM</option>
          <option value="mcp">MCP</option>
          <option value="error">Errors</option>
          <option value="agent">Agents</option>
        </select>
        <button class="nas-btn-sm nas-btn-outline" id="nas-logs-refresh">🔄 Refresh</button>
      </div>
      <div id="nas-logs-content"><div class="nas-loading-box">⏳ Loading logs…</div></div>
    </section>
    <?php endif; ?>
  </main>
</div>
<?php endif; ?>
</div>
        <?php return ob_get_clean();
    }
}
