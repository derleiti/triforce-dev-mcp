<?php
/**
 * Nova AI Admin Dashboard
 * Enhanced Admin Interface for AILinux.me
 *
 * @package NovAI
 * @version 4.6.4
 */

defined('ABSPATH') || exit;

$settings = get_option('nova_ai_settings', []);
$downloads_service = \NovAI\Services\DownloadsService::instance();
$early_access_service = \NovAI\Services\EarlyAccessService::instance();

// Handle settings save
if (isset($_POST['nov_ai_save_settings']) && wp_verify_nonce($_POST['_wpnonce'], 'nova_ai_settings')) {
    $settings = get_option('nova_ai_settings', []);

    if (array_key_exists('api_endpoint', $_POST)) {
        $settings['api_endpoint'] = sanitize_text_field($_POST['api_endpoint'] ?? 'http://localhost:9100');
    }
    if (array_key_exists('api_endpoint_internal', $_POST)) {
        $settings['api_endpoint_internal'] = sanitize_text_field($_POST['api_endpoint_internal'] ?? '');
    }
    if (array_key_exists('mcp_endpoint', $_POST)) {
        $settings['mcp_endpoint'] = sanitize_text_field($_POST['mcp_endpoint'] ?? 'http://localhost:9100');
    }
    if (array_key_exists('downloads_path', $_POST)) {
        $settings['downloads_path'] = sanitize_text_field($_POST['downloads_path'] ?? ABSPATH . 'downloads');
    }
    if (array_key_exists('default_model', $_POST)) {
        $settings['default_model'] = sanitize_text_field($_POST['default_model'] ?? 'gemini/gemini-2.0-flash');
    }
    if (array_key_exists('discuss_button_enabled', $_POST)) {
        $settings['discuss_button_enabled'] = isset($_POST['discuss_button_enabled']);
    }

    // Widget settings
    if (array_key_exists('widget_enabled', $_POST)) {
        $settings['widget_enabled'] = isset($_POST['widget_enabled']);
    }
    if (array_key_exists('widget_position', $_POST)) {
        $settings['widget_position'] = sanitize_text_field($_POST['widget_position'] ?? 'bottom-right');
    }
    if (array_key_exists('widget_color', $_POST)) {
        $settings['widget_color'] = sanitize_hex_color($_POST['widget_color'] ?? '#6366f1');
    }
    if (array_key_exists('widget_title', $_POST)) {
        $settings['widget_title'] = sanitize_text_field($_POST['widget_title'] ?? 'NOVA AI Chat');
    }
    if (array_key_exists('widget_welcome', $_POST)) {
        $settings['widget_welcome'] = sanitize_textarea_field($_POST['widget_welcome'] ?? 'Hallo! Wie kann ich dir helfen?');
    }
    if (array_key_exists('widget_icon', $_POST)) {
        $settings['widget_icon'] = sanitize_text_field($_POST['widget_icon'] ?? '🐻');
    }

    update_option('nova_ai_settings', $settings);
    echo '<div class="notice notice-success is-dismissible"><p>✅ Settings saved successfully!</p></div>';
}

// Handle memory clear action
if (isset($_POST['nov_ai_clear_memory']) && wp_verify_nonce($_POST['_wpnonce'], 'nov_ai_memory_action')) {
    $memory_type = sanitize_text_field($_POST['memory_type'] ?? '');
    // Memory clear will be handled via AJAX
    echo '<div class="notice notice-info"><p>🧹 Memory clear initiated...</p></div>';
}

// Handle shop product meta save
if (isset($_POST['nova_save_product_meta']) && wp_verify_nonce($_POST['_wpnonce'], 'nova_shop_admin')) {
    if (current_user_can('manage_options') && class_exists('NovAI\Services\ShopService')) {
        $product_id = sanitize_text_field($_POST['ns_product_id'] ?? '');
        if ($product_id) {
            \NovAI\Services\ShopService::instance()->save_product_meta($product_id, [
                'is_new'      => !empty($_POST['ns_is_new']),
                'is_sale'     => !empty($_POST['ns_is_sale']),
                'description' => wp_kses_post($_POST['ns_description'] ?? ''),
                'usage'       => sanitize_textarea_field($_POST['ns_usage'] ?? ''),
                'image'       => esc_url_raw($_POST['ns_image'] ?? ''),
            ]);
            echo '<div class="notice notice-success is-dismissible"><p>✅ Produkt-Meta gespeichert.</p></div>';
        }
    }
}

$ea_stats = $early_access_service->get_stats();
$active_tab = isset($_GET['tab']) ? sanitize_text_field($_GET['tab']) : 'dashboard';
?>

<div class="wrap nov-ai-admin">
    <h1 class="nov-header">
        <span class="nov-logo">🚀</span>
        Nova AI Frontend
        <span class="nov-version">v4.6.4</span>
    </h1>

    <!-- Tab Navigation -->
    <nav class="nav-tab-wrapper nov-tabs">
        <a href="?page=nova-ai&tab=dashboard" class="nav-tab <?php echo $active_tab === 'dashboard' ? 'nav-tab-active' : ''; ?>">
            📊 Dashboard
        </a>
        <a href="?page=nova-ai&tab=settings" class="nav-tab <?php echo $active_tab === 'settings' ? 'nav-tab-active' : ''; ?>">
            ⚙️ Settings
        </a>
        <a href="?page=nova-ai&tab=system" class="nav-tab <?php echo $active_tab === 'system' ? 'nav-tab-active' : ''; ?>">
            🖥️ System Status
        </a>
        <a href="?page=nova-ai&tab=agents" class="nav-tab <?php echo $active_tab === 'agents' ? 'nav-tab-active' : ''; ?>">
            🤖 AI Agents
        </a>
        <a href="?page=nova-ai&tab=memory" class="nav-tab <?php echo $active_tab === 'memory' ? 'nav-tab-active' : ''; ?>">
            🧠 Memory
        </a>
        <a href="?page=nova-ai&tab=widget" class="nav-tab <?php echo $active_tab === 'widget' ? 'nav-tab-active' : ''; ?>">
            💬 Chat Widget
        </a>
        <?php if (current_user_can('manage_options') && wp_get_current_user()->user_email === (defined('NOVA_ADMIN_EMAIL') ? NOVA_ADMIN_EMAIL : 'admin@ailinux.me')): ?>
        <a href="?page=nova-ai&tab=auth" class="nav-tab <?php echo $active_tab === 'auth' ? 'nav-tab-active' : ''; ?>">
            🔐 Auth & Berechtigungen
        </a>
        <a href="?page=nova-ai&tab=payments" class="nav-tab <?php echo $active_tab === 'payments' ? 'nav-tab-active' : ''; ?>">
            💳 Payments
        </a>
        <a href="?page=nova-ai&tab=shop" class="nav-tab <?php echo $active_tab === 'shop' ? 'nav-tab-active' : ''; ?>">
            🛒 Shop
        </a>
        <?php endif; ?>
    </nav>

    <div class="nov-content">

        <?php if ($active_tab === 'dashboard'): ?>
        <!-- Dashboard Tab -->
        <div class="nov-tab-content" id="tab-dashboard">

            <!-- Quick Stats Row -->
            <div class="nov-stats-grid">
                <div class="nov-stat-card">
                    <div class="nov-stat-icon">🎟️</div>
                    <div class="nov-stat-content">
                        <span class="nov-stat-value"><?php echo $ea_stats['available_slots']; ?></span>
                        <span class="nov-stat-label">Available Beta Slots</span>
                    </div>
                </div>
                <div class="nov-stat-card">
                    <div class="nov-stat-icon">👥</div>
                    <div class="nov-stat-content">
                        <span class="nov-stat-value"><?php echo $ea_stats['used_slots']; ?></span>
                        <span class="nov-stat-label">Active Beta Users</span>
                    </div>
                </div>
                <div class="nov-stat-card">
                    <div class="nov-stat-icon" id="system-status-icon">⏳</div>
                    <div class="nov-stat-content">
                        <span class="nov-stat-value" id="system-status-text">Loading...</span>
                        <span class="nov-stat-label">MCP System Status</span>
                    </div>
                </div>
                <div class="nov-stat-card">
                    <div class="nov-stat-icon" id="agents-count-icon">🤖</div>
                    <div class="nov-stat-content">
                        <span class="nov-stat-value" id="agents-count">-</span>
                        <span class="nov-stat-label">Active Agents</span>
                    </div>
                </div>
            </div>

            <!-- Main Dashboard Grid -->
            <div class="nov-admin-grid">

                <!-- Quick Actions Card -->
                <div class="nov-card">
                    <h2 class="nov-card-title">⚡ Quick Actions</h2>
                    <div class="nov-quick-actions">
                        <button class="button button-primary nov-action-btn" id="refresh-status">
                            🔄 Refresh Status
                        </button>
                        <button class="button nov-action-btn" id="test-api">
                            🧪 Test API Connection
                        </button>
                        <button class="button nov-action-btn" id="view-models">
                            📋 View Available Models
                        </button>
                        <button class="button nov-action-btn" id="clear-cache">
                            🗑️ Clear Cache
                        </button>
                    </div>
                </div>

                <!-- Early Access Stats -->
                <div class="nov-card">
                    <h2 class="nov-card-title" style="color: #3fb950;">🎟️ Early Access Beta</h2>
                    <div class="nov-progress-container">
                        <div class="nov-progress-bar">
                            <div class="nov-progress-fill" style="width: <?php echo ($ea_stats['used_slots'] / $ea_stats['total_slots']) * 100; ?>%"></div>
                        </div>
                        <span class="nov-progress-text"><?php echo $ea_stats['used_slots']; ?> / <?php echo $ea_stats['total_slots']; ?> slots used</span>
                    </div>
                    <table class="nov-stats-table">
                        <tr>
                            <td>Total Slots:</td>
                            <td><strong><?php echo $ea_stats['total_slots']; ?></strong></td>
                        </tr>
                        <tr>
                            <td>Used:</td>
                            <td><strong><?php echo $ea_stats['used_slots']; ?></strong></td>
                        </tr>
                        <tr>
                            <td>Available:</td>
                            <td><strong class="nov-success"><?php echo $ea_stats['available_slots']; ?></strong></td>
                        </tr>
                        <tr>
                            <td>Status:</td>
                            <td>
                                <?php if ($ea_stats['is_open']): ?>
                                    <span class="nov-badge nov-badge-success">✅ Open</span>
                                <?php else: ?>
                                    <span class="nov-badge nov-badge-danger">❌ Waitlist</span>
                                <?php endif; ?>
                            </td>
                        </tr>
                    </table>
                </div>

            </div>

            <!-- Shortcodes Reference -->
            <div class="nov-card nov-card-full">
                <h2 class="nov-card-title" style="color: #a371f7;">📋 Available Shortcodes</h2>
                <table class="widefat nov-table">
                    <thead>
                        <tr>
                            <th>Shortcode</th>
                            <th>Description</th>
                            <th>Parameters</th>
                            <th>Action</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td><code>[ailinux_downloads]</code></td>
                            <td>File browser for downloads directory</td>
                            <td><code>path=""</code></td>
                            <td><button class="button button-small nov-copy-btn" data-copy="[ailinux_downloads]">📋 Copy</button></td>
                        </tr>
                        <tr>
                            <td><code>[ailinux_ai_playground]</code></td>
                            <td>AI Chat + Vision playground</td>
                            <td><code>default_tab="chat"</code>, <code>height="600px"</code></td>
                            <td><button class="button button-small nov-copy-btn" data-copy='[ailinux_ai_playground default_tab="chat" height="600px"]'>📋 Copy</button></td>
                        </tr>
                        <tr>
                            <td><code>[ailinux_pass]</code></td>
                            <td>Early Access registration form</td>
                            <td>None</td>
                            <td><button class="button button-small nov-copy-btn" data-copy="[ailinux_pass]">📋 Copy</button></td>
                        </tr>
                        <tr>
                            <td><code>[ailinux_chat_widget]</code></td>
                            <td>Floating chat widget</td>
                            <td><code>position="bottom-right"</code></td>
                            <td><button class="button button-small nov-copy-btn" data-copy="[ailinux_chat_widget]">📋 Copy</button></td>
                        </tr>
                    </tbody>
                </table>
            </div>

            <!-- Recent Activity Log -->
            <div class="nov-card nov-card-full">
                <h2 class="nov-card-title">📜 Recent Activity</h2>
                <div class="nov-log-container" id="activity-log">
                    <div class="nov-log-entry">
                        <span class="nov-log-time">Loading...</span>
                        <span class="nov-log-message">Fetching activity log...</span>
                    </div>
                </div>
            </div>

        </div>

        <?php elseif ($active_tab === 'settings'): ?>
        <!-- Settings Tab -->
        <div class="nov-tab-content" id="tab-settings">
            <div class="nov-admin-grid">

                <!-- API Settings Card -->
                <div class="nov-card">
                    <h2 class="nov-card-title">🔌 API Configuration</h2>
                    <form method="post">
                        <?php wp_nonce_field('nova_ai_settings'); ?>

                        <table class="form-table">
                            <tr>
                                <th><label for="api_endpoint">Backend API URL</label></th>
                                <td>
                                    <input type="url" name="api_endpoint" id="api_endpoint" class="regular-text"
                                        value="<?php echo esc_attr($settings['api_endpoint'] ?? 'http://localhost:9100'); ?>">
                                    <p class="description">Main API endpoint (e.g., http://localhost:9100)</p>
                                </td>
                            </tr>
                            <tr>
                                <th><label for="api_endpoint_internal">Backend API URL (internal)</label></th>
                                <td>
                                    <input type="text" name="api_endpoint_internal" id="api_endpoint_internal" class="regular-text"
                                        value="<?php echo esc_attr($settings['api_endpoint_internal'] ?? ''); ?>">
                                    <p class="description">Optional: internal container URL (e.g., http://host.docker.internal:9100)</p>
                                </td>
                            </tr>
                            <tr>
                                <th><label for="mcp_endpoint">MCP Server URL</label></th>
                                <td>
                                    <input type="url" name="mcp_endpoint" id="mcp_endpoint" class="regular-text"
                                        value="<?php echo esc_attr($settings['mcp_endpoint'] ?? 'http://localhost:9100'); ?>">
                                    <p class="description">MCP Server for agent orchestration</p>
                                </td>
                            </tr>
                            <tr>
                                <th><label for="downloads_path">Downloads Path</label></th>
                                <td>
                                    <input type="text" name="downloads_path" id="downloads_path" class="regular-text"
                                        value="<?php echo esc_attr($settings['downloads_path'] ?? ABSPATH . 'downloads'); ?>">
                                    <p class="description">Local path for file downloads</p>
                                </td>
                            </tr>
                        </table>

                        <p><input type="submit" name="nov_ai_save_settings" class="button button-primary" value="Save API Settings"></p>
                    </form>
                </div>

                <!-- Feature Settings Card -->
                <div class="nov-card">
                    <h2 class="nov-card-title">🎛️ Feature Settings</h2>
                    <form method="post">
                        <?php wp_nonce_field('nova_ai_settings'); ?>

                        <table class="form-table">
                            <tr>
                                <th><label for="default_model">Default AI Model</label></th>
                                <td>
                                    <select name="default_model" id="default_model" class="regular-text">
                                        <optgroup label="Google">
                                            <option value="gemini/gemini-2.0-flash" <?php selected($settings['default_model'] ?? '', 'gemini/gemini-2.0-flash'); ?>>Gemini 2.0 Flash</option>
                                            <option value="gemini/gemini-2.0-flash-thinking" <?php selected($settings['default_model'] ?? '', 'gemini/gemini-2.0-flash-thinking'); ?>>Gemini 2.0 Flash Thinking</option>
                                            <option value="gemini/gemini-exp-1206" <?php selected($settings['default_model'] ?? '', 'gemini/gemini-exp-1206'); ?>>Gemini Experimental</option>
                                        </optgroup>
                                        <optgroup label="Groq">
                                            <option value="groq/llama-3.3-70b-versatile" <?php selected($settings['default_model'] ?? '', 'groq/llama-3.3-70b-versatile'); ?>>Llama 3.3 70B</option>
                                        </optgroup>
                                        <optgroup label="Anthropic">
                                            <option value="anthropic/claude-sonnet-4-20250514" <?php selected($settings['default_model'] ?? '', 'anthropic/claude-sonnet-4-20250514'); ?>>Claude Sonnet 4</option>
                                            <option value="anthropic/claude-opus-4-20250514" <?php selected($settings['default_model'] ?? '', 'anthropic/claude-opus-4-20250514'); ?>>Claude Opus 4</option>
                                        </optgroup>
                                        <optgroup label="Local (Ollama)">
                                            <option value="ollama/llama3.2" <?php selected($settings['default_model'] ?? '', 'ollama/llama3.2'); ?>>Llama 3.2 (Local)</option>
                                            <option value="ollama/qwen2.5-coder" <?php selected($settings['default_model'] ?? '', 'ollama/qwen2.5-coder'); ?>>Qwen 2.5 Coder (Local)</option>
                                        </optgroup>
                                    </select>
                                </td>
                            </tr>
                            <tr>
                                <th>Discuss Button</th>
                                <td>
                                    <label class="nov-toggle">
                                        <input type="checkbox" name="discuss_button_enabled"
                                            <?php checked($settings['discuss_button_enabled'] ?? true); ?>>
                                        <span class="nov-toggle-slider"></span>
                                        Enable "Discuss with AI" button on posts/pages
                                    </label>
                                </td>
                            </tr>
                        </table>

                        <p><input type="submit" name="nov_ai_save_settings" class="button button-primary" value="Save Feature Settings"></p>
                    </form>
                </div>

            </div>
        </div>

        <?php elseif ($active_tab === 'system'): ?>
        <!-- System Status Tab -->
        <div class="nov-tab-content" id="tab-system">

            <!-- System Overview -->
            <div class="nov-system-header">
                <h2>🖥️ System Status Overview</h2>
                <button class="button button-primary" id="refresh-system-status">🔄 Refresh</button>
            </div>

            <div class="nov-system-grid">

                <!-- Backend Status -->
                <div class="nov-card nov-status-card">
                    <h3 class="nov-card-subtitle">🔌 Backend API</h3>
                    <div class="nov-status-indicator" id="backend-status">
                        <span class="nov-status-dot nov-loading"></span>
                        <span class="nov-status-text">Checking...</span>
                    </div>
                    <div class="nov-status-details" id="backend-details">
                        <p>Endpoint: <code><?php echo esc_html($settings['api_endpoint'] ?? 'Not configured'); ?></code></p>
                    </div>
                </div>

                <!-- MCP Server Status -->
                <div class="nov-card nov-status-card">
                    <h3 class="nov-card-subtitle">🌐 MCP Server</h3>
                    <div class="nov-status-indicator" id="mcp-status">
                        <span class="nov-status-dot nov-loading"></span>
                        <span class="nov-status-text">Checking...</span>
                    </div>
                    <div class="nov-status-details" id="mcp-details">
                        <p>Status will appear here...</p>
                    </div>
                </div>

                <!-- Ollama Status -->
                <div class="nov-card nov-status-card">
                    <h3 class="nov-card-subtitle">🦙 Ollama (Local LLM)</h3>
                    <div class="nov-status-indicator" id="ollama-status">
                        <span class="nov-status-dot nov-loading"></span>
                        <span class="nov-status-text">Checking...</span>
                    </div>
                    <div class="nov-status-details" id="ollama-details">
                        <p>Local models will appear here...</p>
                    </div>
                </div>

                <!-- Vault Status -->
                <div class="nov-card nov-status-card">
                    <h3 class="nov-card-subtitle">🔐 API Vault</h3>
                    <div class="nov-status-indicator" id="vault-status">
                        <span class="nov-status-dot nov-loading"></span>
                        <span class="nov-status-text">Checking...</span>
                    </div>
                    <div class="nov-status-details" id="vault-details">
                        <p>API keys status will appear here...</p>
                    </div>
                </div>

            </div>

            <!-- Available Models List -->
            <div class="nov-card nov-card-full">
                <h2 class="nov-card-title">📋 Available AI Models</h2>
                <div class="nov-models-controls">
                    <input type="text" id="model-search" placeholder="🔍 Search models..." class="regular-text">
                    <select id="model-provider-filter">
                        <option value="all">All Providers</option>
                        <option value="gemini">Google Gemini</option>
                        <option value="groq">Groq</option>
                        <option value="anthropic">Anthropic</option>
                        <option value="openai">OpenAI</option>
                        <option value="mistral">Mistral</option>
                        <option value="ollama">Ollama (Local)</option>
                    </select>
                </div>
                <div class="nov-models-list" id="models-list">
                    <div class="nov-loading-spinner">Loading models...</div>
                </div>
            </div>

            <!-- System Logs -->
            <div class="nov-card nov-card-full">
                <h2 class="nov-card-title">📜 System Logs</h2>
                <div class="nov-log-controls">
                    <select id="log-category">
                        <option value="all">All Categories</option>
                        <option value="api">API</option>
                        <option value="llm">LLM</option>
                        <option value="mcp">MCP</option>
                        <option value="error">Errors</option>
                        <option value="agent">Agents</option>
                    </select>
                    <select id="log-level">
                        <option value="info">Info+</option>
                        <option value="warning">Warning+</option>
                        <option value="error">Errors Only</option>
                    </select>
                    <button class="button" id="fetch-logs">🔄 Refresh Logs</button>
                </div>
                <div class="nov-log-container nov-log-large" id="system-logs">
                    <div class="nov-log-entry">Click "Refresh Logs" to load system logs...</div>
                </div>
            </div>

        </div>

        <?php elseif ($active_tab === 'agents'): ?>
        <!-- AI Agents Tab -->
        <div class="nov-tab-content" id="tab-agents">

            <div class="nov-agents-header">
                <h2>🤖 AI Agent Management</h2>
                <div class="nov-agents-actions">
                    <button class="button button-primary" id="refresh-agents">🔄 Refresh</button>
                    <button class="button" id="bootstrap-agents">🚀 Bootstrap All</button>
                </div>
            </div>

            <!-- Agents Grid -->
            <div class="nov-agents-grid" id="agents-grid">

                <!-- Claude Agent -->
                <div class="nov-agent-card" data-agent="claude">
                    <div class="nov-agent-header">
                        <span class="nov-agent-icon">🟣</span>
                        <span class="nov-agent-name">Claude</span>
                        <span class="nov-agent-status nov-loading" id="agent-claude-status">...</span>
                    </div>
                    <div class="nov-agent-body">
                        <p class="nov-agent-desc">Anthropic's Claude - Complex reasoning & analysis</p>
                        <div class="nov-agent-stats" id="agent-claude-stats">
                            <span>Tasks: -</span>
                            <span>Uptime: -</span>
                        </div>
                    </div>
                    <div class="nov-agent-actions">
                        <button class="button button-small nov-agent-start" data-agent="claude">▶️ Start</button>
                        <button class="button button-small nov-agent-stop" data-agent="claude">⏹️ Stop</button>
                        <button class="button button-small nov-agent-call" data-agent="claude">💬 Call</button>
                    </div>
                </div>

                <!-- Gemini Agent -->
                <div class="nov-agent-card" data-agent="gemini">
                    <div class="nov-agent-header">
                        <span class="nov-agent-icon">🔵</span>
                        <span class="nov-agent-name">Gemini</span>
                        <span class="nov-agent-status nov-loading" id="agent-gemini-status">...</span>
                    </div>
                    <div class="nov-agent-body">
                        <p class="nov-agent-desc">Google's Gemini - Research & coordination</p>
                        <div class="nov-agent-stats" id="agent-gemini-stats">
                            <span>Tasks: -</span>
                            <span>Uptime: -</span>
                        </div>
                    </div>
                    <div class="nov-agent-actions">
                        <button class="button button-small nov-agent-start" data-agent="gemini">▶️ Start</button>
                        <button class="button button-small nov-agent-stop" data-agent="gemini">⏹️ Stop</button>
                        <button class="button button-small nov-agent-call" data-agent="gemini">💬 Call</button>
                    </div>
                </div>

                <!-- Codex Agent -->
                <div class="nov-agent-card" data-agent="codex">
                    <div class="nov-agent-header">
                        <span class="nov-agent-icon">🟢</span>
                        <span class="nov-agent-name">Codex</span>
                        <span class="nov-agent-status nov-loading" id="agent-codex-status">...</span>
                    </div>
                    <div class="nov-agent-body">
                        <p class="nov-agent-desc">OpenAI Codex - Code generation specialist</p>
                        <div class="nov-agent-stats" id="agent-codex-stats">
                            <span>Tasks: -</span>
                            <span>Uptime: -</span>
                        </div>
                    </div>
                    <div class="nov-agent-actions">
                        <button class="button button-small nov-agent-start" data-agent="codex">▶️ Start</button>
                        <button class="button button-small nov-agent-stop" data-agent="codex">⏹️ Stop</button>
                        <button class="button button-small nov-agent-call" data-agent="codex">💬 Call</button>
                    </div>
                </div>

                <!-- OpenCode Agent -->
                <div class="nov-agent-card" data-agent="opencode">
                    <div class="nov-agent-header">
                        <span class="nov-agent-icon">🟡</span>
                        <span class="nov-agent-name">OpenCode</span>
                        <span class="nov-agent-status nov-loading" id="agent-opencode-status">...</span>
                    </div>
                    <div class="nov-agent-body">
                        <p class="nov-agent-desc">Multi-provider code assistant</p>
                        <div class="nov-agent-stats" id="agent-opencode-stats">
                            <span>Tasks: -</span>
                            <span>Uptime: -</span>
                        </div>
                    </div>
                    <div class="nov-agent-actions">
                        <button class="button button-small nov-agent-start" data-agent="opencode">▶️ Start</button>
                        <button class="button button-small nov-agent-stop" data-agent="opencode">⏹️ Stop</button>
                        <button class="button button-small nov-agent-call" data-agent="opencode">💬 Call</button>
                    </div>
                </div>

            </div>

            <!-- Agent Communication Panel -->
            <div class="nov-card nov-card-full">
                <h2 class="nov-card-title">💬 Agent Communication</h2>
                <div class="nov-agent-comm">
                    <div class="nov-agent-comm-header">
                        <select id="agent-select" class="regular-text">
                            <option value="claude">Claude</option>
                            <option value="gemini">Gemini</option>
                            <option value="codex">Codex</option>
                            <option value="opencode">OpenCode</option>
                            <option value="broadcast">📢 Broadcast to All</option>
                        </select>
                        <select id="comm-strategy">
                            <option value="direct">Direct</option>
                            <option value="parallel">Parallel</option>
                            <option value="sequential">Sequential</option>
                            <option value="consensus">Consensus</option>
                        </select>
                    </div>
                    <div class="nov-agent-chat" id="agent-chat">
                        <div class="nov-chat-welcome">
                            <p>👋 Select an agent and send a message to begin communication.</p>
                        </div>
                    </div>
                    <div class="nov-agent-input">
                        <textarea id="agent-message" placeholder="Enter message for agent..." rows="3"></textarea>
                        <button class="button button-primary" id="send-agent-message">📤 Send</button>
                    </div>
                </div>
            </div>

        </div>

        <?php elseif ($active_tab === 'memory'): ?>
        <!-- Memory Tab -->
        <div class="nov-tab-content" id="tab-memory">

            <div class="nov-memory-header">
                <h2>🧠 Knowledge Base & Memory</h2>
                <div class="nov-memory-actions">
                    <button class="button button-primary" id="refresh-memory">🔄 Refresh</button>
                    <button class="button" id="export-memory">📥 Export</button>
                </div>
            </div>

            <!-- Memory Stats -->
            <div class="nov-memory-stats">
                <div class="nov-memory-stat">
                    <span class="nov-memory-stat-icon">📚</span>
                    <span class="nov-memory-stat-value" id="memory-total">-</span>
                    <span class="nov-memory-stat-label">Total Entries</span>
                </div>
                <div class="nov-memory-stat">
                    <span class="nov-memory-stat-icon">💡</span>
                    <span class="nov-memory-stat-value" id="memory-facts">-</span>
                    <span class="nov-memory-stat-label">Facts</span>
                </div>
                <div class="nov-memory-stat">
                    <span class="nov-memory-stat-icon">🎯</span>
                    <span class="nov-memory-stat-value" id="memory-decisions">-</span>
                    <span class="nov-memory-stat-label">Decisions</span>
                </div>
                <div class="nov-memory-stat">
                    <span class="nov-memory-stat-icon">💻</span>
                    <span class="nov-memory-stat-value" id="memory-code">-</span>
                    <span class="nov-memory-stat-label">Code Snippets</span>
                </div>
            </div>

            <!-- Memory Search & Store -->
            <div class="nov-admin-grid">

                <!-- Memory Search -->
                <div class="nov-card">
                    <h3 class="nov-card-subtitle">🔍 Search Memory</h3>
                    <div class="nov-memory-search">
                        <input type="text" id="memory-search-query" placeholder="Search knowledge base..." class="regular-text">
                        <select id="memory-search-type">
                            <option value="">All Types</option>
                            <option value="fact">Facts</option>
                            <option value="decision">Decisions</option>
                            <option value="code">Code</option>
                            <option value="summary">Summaries</option>
                            <option value="todo">Todos</option>
                        </select>
                        <button class="button button-primary" id="search-memory">🔍 Search</button>
                    </div>
                    <div class="nov-memory-results" id="memory-results">
                        <p class="nov-placeholder">Enter a search query to find memories...</p>
                    </div>
                </div>

                <!-- Store Memory -->
                <div class="nov-card">
                    <h3 class="nov-card-subtitle">➕ Store New Memory</h3>
                    <form id="store-memory-form">
                        <table class="form-table">
                            <tr>
                                <th><label for="new-memory-content">Content</label></th>
                                <td>
                                    <textarea id="new-memory-content" rows="4" class="large-text" placeholder="Enter knowledge/fact to store..."></textarea>
                                </td>
                            </tr>
                            <tr>
                                <th><label for="new-memory-type">Type</label></th>
                                <td>
                                    <select id="new-memory-type">
                                        <option value="fact">💡 Fact</option>
                                        <option value="decision">🎯 Decision</option>
                                        <option value="code">💻 Code</option>
                                        <option value="summary">📝 Summary</option>
                                        <option value="todo">✅ Todo</option>
                                    </select>
                                </td>
                            </tr>
                            <tr>
                                <th><label for="new-memory-tags">Tags</label></th>
                                <td>
                                    <input type="text" id="new-memory-tags" class="regular-text" placeholder="tag1, tag2, tag3">
                                    <p class="description">Comma-separated tags for categorization</p>
                                </td>
                            </tr>
                            <tr>
                                <th><label for="new-memory-confidence">Confidence</label></th>
                                <td>
                                    <input type="range" id="new-memory-confidence" min="0" max="1" step="0.1" value="0.8">
                                    <span id="confidence-value">0.8</span>
                                </td>
                            </tr>
                        </table>
                        <button type="submit" class="button button-primary">💾 Store Memory</button>
                    </form>
                </div>

            </div>

            <!-- Memory Management -->
            <div class="nov-card nov-card-full">
                <h2 class="nov-card-title">🗑️ Memory Management</h2>
                <div class="nov-memory-management">
                    <form method="post" id="clear-memory-form">
                        <?php wp_nonce_field('nov_ai_memory_action'); ?>
                        <div class="nov-memory-clear-options">
                            <label>
                                <input type="radio" name="clear_option" value="type"> Clear by Type:
                                <select name="memory_type" id="clear-memory-type">
                                    <option value="fact">Facts</option>
                                    <option value="decision">Decisions</option>
                                    <option value="code">Code</option>
                                    <option value="summary">Summaries</option>
                                    <option value="todo">Todos</option>
                                </select>
                            </label>
                            <label>
                                <input type="radio" name="clear_option" value="age"> Clear older than:
                                <input type="number" name="clear_days" id="clear-days" value="30" min="1" max="365"> days
                            </label>
                            <label>
                                <input type="radio" name="clear_option" value="all"> Clear ALL Memory
                            </label>
                        </div>
                        <button type="submit" name="nov_ai_clear_memory" class="button button-secondary nov-danger-btn">
                            🗑️ Clear Selected Memory
                        </button>
                    </form>
                </div>
            </div>

        </div>

        <?php elseif ($active_tab === 'widget'): ?>
        <!-- Chat Widget Tab -->
        <div class="nov-tab-content" id="tab-widget">

            <div class="nov-widget-header">
                <h2>💬 Chat Widget Configuration</h2>
            </div>

            <div class="nov-admin-grid">

                <!-- Widget Settings -->
                <div class="nov-card">
                    <h3 class="nov-card-subtitle">⚙️ Widget Settings</h3>
                    <form method="post">
                        <?php wp_nonce_field('nova_ai_settings'); ?>

                        <table class="form-table">
                            <tr>
                                <th>Enable Widget</th>
                                <td>
                                    <label class="nov-toggle">
                                        <input type="checkbox" name="widget_enabled"
                                            <?php checked($settings['widget_enabled'] ?? false); ?>>
                                        <span class="nov-toggle-slider"></span>
                                        Show floating chat widget on site
                                    </label>
                                </td>
                            </tr>
                            <tr>
                                <th><label for="widget_position">Position</label></th>
                                <td>
                                    <select name="widget_position" id="widget_position">
                                        <option value="bottom-right" <?php selected($settings['widget_position'] ?? '', 'bottom-right'); ?>>↘️ Bottom Right</option>
                                        <option value="bottom-left" <?php selected($settings['widget_position'] ?? '', 'bottom-left'); ?>>↙️ Bottom Left</option>
                                        <option value="top-right" <?php selected($settings['widget_position'] ?? '', 'top-right'); ?>>↗️ Top Right</option>
                                        <option value="top-left" <?php selected($settings['widget_position'] ?? '', 'top-left'); ?>>↖️ Top Left</option>
                                    </select>
                                </td>
                            </tr>
                            <tr>
                                <th><label for="widget_color">Primary Color</label></th>
                                <td>
                                    <input type="color" name="widget_color" id="widget_color"
                                        value="<?php echo esc_attr($settings['widget_color'] ?? '#6366f1'); ?>">
                                    <span id="widget_color_hex"><?php echo esc_html($settings['widget_color'] ?? '#6366f1'); ?></span>
                                </td>
                            </tr>
                            <tr>
                                <th><label for="widget_title">Widget Title</label></th>
                                <td>
                                    <input type="text" name="widget_title" id="widget_title" class="regular-text"
                                        value="<?php echo esc_attr($settings['widget_title'] ?? 'NOVA AI Chat'); ?>">
                                </td>
                            </tr>
                            <tr>
                                <th><label for="widget_icon">Widget Icon</label></th>
                                <td>
                                    <input type="text" name="widget_icon" id="widget_icon" class="small-text"
                                        value="<?php echo esc_attr($settings['widget_icon'] ?? '🐻'); ?>">
                                    <p class="description">Emoji or text for the chat button</p>
                                </td>
                            </tr>
                            <tr>
                                <th><label for="widget_welcome">Welcome Message</label></th>
                                <td>
                                    <textarea name="widget_welcome" id="widget_welcome" rows="3" class="large-text"><?php echo esc_textarea($settings['widget_welcome'] ?? 'Hallo! Wie kann ich dir helfen?'); ?></textarea>
                                </td>
                            </tr>
                        </table>

                        <p><input type="submit" name="nov_ai_save_settings" class="button button-primary" value="Save Widget Settings"></p>
                    </form>
                </div>

                <!-- Widget Preview -->
                <div class="nov-card">
                    <h3 class="nov-card-subtitle">👁️ Live Preview</h3>
                    <div class="nov-widget-preview" id="widget-preview">
                        <div class="nov-preview-container">
                            <div class="nov-preview-widget" id="preview-widget">
                                <div class="nov-preview-chat" id="preview-chat">
                                    <div class="nov-preview-header" id="preview-header">
                                        <span id="preview-title"><?php echo esc_html($settings['widget_title'] ?? 'NOVA AI Chat'); ?></span>
                                        <span class="nov-preview-close">×</span>
                                    </div>
                                    <div class="nov-preview-messages">
                                        <div class="nov-preview-msg nov-ai-msg">
                                            <span id="preview-welcome"><?php echo esc_html($settings['widget_welcome'] ?? 'Hallo! Wie kann ich dir helfen?'); ?></span>
                                        </div>
                                    </div>
                                    <div class="nov-preview-input">
                                        <input type="text" placeholder="Nachricht eingeben..." disabled>
                                        <button disabled>➤</button>
                                    </div>
                                </div>
                                <div class="nov-preview-button" id="preview-button">
                                    <span id="preview-icon"><?php echo esc_html($settings['widget_icon'] ?? '🐻'); ?></span>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>

            </div>

            <!-- Widget Code Snippet -->
            <div class="nov-card nov-card-full">
                <h2 class="nov-card-title">📋 Manual Integration</h2>
                <p>Use one of these methods to add the chat widget to your site:</p>

                <div class="nov-code-tabs">
                    <button class="nov-code-tab active" data-tab="shortcode">Shortcode</button>
                    <button class="nov-code-tab" data-tab="php">PHP</button>
                    <button class="nov-code-tab" data-tab="js">JavaScript</button>
                </div>

                <div class="nov-code-content active" id="code-shortcode">
                    <pre><code>[ailinux_chat_widget position="<?php echo esc_attr($settings['widget_position'] ?? 'bottom-right'); ?>"]</code></pre>
                    <button class="button button-small nov-copy-btn" data-copy='[ailinux_chat_widget position="<?php echo esc_attr($settings['widget_position'] ?? 'bottom-right'); ?>"]'>📋 Copy</button>
                </div>

                <div class="nov-code-content" id="code-php">
                    <pre><code>&lt;?php echo do_shortcode('[ailinux_chat_widget]'); ?&gt;</code></pre>
                    <button class="button button-small nov-copy-btn" data-copy="<?php echo do_shortcode('[ailinux_chat_widget]'); ?>">📋 Copy</button>
                </div>

                <div class="nov-code-content" id="code-js">
                    <pre><code>&lt;script src="<?php echo esc_url(NOV_AI_PLUGIN_URL); ?>frontend/js/chat-widget.js"&gt;&lt;/script&gt;
&lt;script&gt;
    NovaChat.init({
        apiUrl: '<?php echo esc_url(admin_url('admin-ajax.php')); ?>',
        position: '<?php echo esc_attr($settings['widget_position'] ?? 'bottom-right'); ?>',
        color: '<?php echo esc_attr($settings['widget_color'] ?? '#6366f1'); ?>'
    });
&lt;/script&gt;</code></pre>
                    <button class="button button-small nov-copy-btn" data-copy="NovaChat.init({...})">📋 Copy</button>
                </div>
            </div>

        </div>

        <?php elseif ($active_tab === 'shop'): ?>
        <!-- ===== SHOP TAB ===== -->
        <?php
        $nova_admin_email = defined('NOVA_ADMIN_EMAIL') ? NOVA_ADMIN_EMAIL : 'admin@ailinux.me';
        if (wp_get_current_user()->user_email !== $nova_admin_email):
            echo '<div class="notice notice-error"><p>Zugriff verweigert.</p></div>';
        else:
            $shop_svc    = class_exists('NovAI\Services\ShopService') ? \NovAI\Services\ShopService::instance() : null;
            $ls_api_key  = defined('NOVA_LS_API_KEY')   ? NOVA_LS_API_KEY   : '';
            $ls_store_id = defined('NOVA_LS_STORE_ID')  ? NOVA_LS_STORE_ID  : '';
            $ls_test     = defined('NOVA_LS_TEST_MODE') && NOVA_LS_TEST_MODE;
            $ls_provider = defined('NOVA_PAYMENT_PROVIDER') ? NOVA_PAYMENT_PROVIDER : 'stub';
            $masked_key  = $ls_api_key ? substr($ls_api_key, 0, 10) . str_repeat('*', 20) : '(nicht gesetzt)';
            $products    = $shop_svc ? $shop_svc->get_products() : [];
            $all_discounts = $shop_svc ? $shop_svc->get_all_discounts() : [];
            $product_meta_all = $shop_svc ? $shop_svc->get_product_meta_all() : [];
            $admin_nonce  = wp_create_nonce('nova_admin_action');
            $shop_nonce   = wp_create_nonce('nova_shop_admin');
            $rest_base    = rest_url('nova-ai/v1');
        ?>
        <div class="nov-tab-content" id="tab-shop">

            <!-- 1. API Status -->
            <h2>🛒 Shop – LemonSqueezy Integration</h2>
            <div class="nov-stats-grid" style="grid-template-columns:repeat(3,1fr);margin-bottom:2rem">
                <div class="nov-stat-card">
                    <div class="nov-stat-icon">🔑</div>
                    <div class="nov-stat-content">
                        <span class="nov-stat-value" style="font-size:.85rem;word-break:break-all"><?php echo esc_html($masked_key); ?></span>
                        <span class="nov-stat-label">API Key (NOVA_LS_API_KEY)</span>
                    </div>
                </div>
                <div class="nov-stat-card">
                    <div class="nov-stat-icon">🏪</div>
                    <div class="nov-stat-content">
                        <span class="nov-stat-value"><?php echo $ls_store_id ? esc_html($ls_store_id) : '<span style="color:#ef4444">nicht gesetzt</span>'; ?></span>
                        <span class="nov-stat-label">Store ID (NOVA_LS_STORE_ID)</span>
                    </div>
                </div>
                <div class="nov-stat-card">
                    <div class="nov-stat-icon"><?php echo $ls_test ? '🧪' : '🚀'; ?></div>
                    <div class="nov-stat-content">
                        <span class="nov-stat-value" style="color:<?php echo $ls_test ? '#f59e0b' : '#10b981'; ?>">
                            <?php echo $ls_test ? 'TEST' : 'LIVE'; ?>
                        </span>
                        <span class="nov-stat-label">Provider: <?php echo esc_html($ls_provider); ?></span>
                    </div>
                </div>
            </div>

            <div style="display:flex;gap:.75rem;align-items:center;margin-bottom:2rem;flex-wrap:wrap">
                <button id="ns-test-conn" class="button button-primary"
                        data-nonce="<?php echo esc_attr($admin_nonce); ?>"
                        data-ajax="<?php echo esc_url(admin_url('admin-ajax.php')); ?>">
                    🔌 Verbindung testen
                </button>
                <button id="ns-clear-cache" class="button"
                        data-nonce="<?php echo esc_attr($admin_nonce); ?>"
                        data-ajax="<?php echo esc_url(admin_url('admin-ajax.php')); ?>">
                    🔄 Cache leeren
                </button>
                <span id="ns-action-result" style="color:#94a3b8"></span>
            </div>

            <!-- 2. Produkt-Verwaltung -->
            <h3>Produkte (<?php echo count($products); ?>)</h3>
            <?php if (empty($products) && $shop_svc && $shop_svc->is_configured()): ?>
                <p style="color:#94a3b8">Keine Produkte gefunden. Prüfe Store ID und ob Produkte veröffentlicht sind.</p>
            <?php elseif (!$shop_svc || !$shop_svc->is_configured()): ?>
                <p style="color:#ef4444">API Key oder Store ID fehlt – bitte in <code>config/lemonsqueezy.php</code> eintragen.</p>
            <?php else: ?>
            <table class="wp-list-table widefat fixed striped" style="margin-bottom:2rem">
                <thead>
                    <tr>
                        <th width="50">Bild</th>
                        <th>Name</th>
                        <th width="100">Preis</th>
                        <th width="80">Status</th>
                        <th width="70">NEU</th>
                        <th width="70">SALE</th>
                        <th width="100">Aktion</th>
                    </tr>
                </thead>
                <tbody>
                <?php foreach ($products as $p):
                    $thumb   = esc_url($p['admin_image'] ?: $p['thumb_url']);
                    $p_meta  = $product_meta_all[$p['id']] ?? [];
                ?>
                <tr>
                    <td><?php if ($thumb): ?><img src="<?php echo $thumb; ?>" width="48" height="36" style="object-fit:cover;border-radius:4px"><?php else: ?>–<?php endif; ?></td>
                    <td><strong><?php echo esc_html($p['name']); ?></strong><br><small style="color:#64748b"><?php echo esc_html($p['slug']); ?></small></td>
                    <td><?php echo esc_html($p['price_formatted'] ?: '–'); ?></td>
                    <td><span style="color:<?php echo $p['status'] === 'published' ? '#10b981' : '#f59e0b'; ?>"><?php echo esc_html($p['status']); ?></span></td>
                    <td style="text-align:center"><?php echo $p['is_new'] ? '✅' : '–'; ?></td>
                    <td style="text-align:center"><?php echo $p['is_sale'] ? '✅' : '–'; ?></td>
                    <td>
                        <button class="button button-small ns-edit-product"
                                data-id="<?php echo esc_attr($p['id']); ?>"
                                data-name="<?php echo esc_attr($p['name']); ?>">
                            ✏️ Details
                        </button>
                    </td>
                </tr>
                <?php endforeach; ?>
                </tbody>
            </table>
            <?php endif; ?>

            <!-- 3. Produkt-Details Editor -->
            <div id="ns-product-editor" style="display:none;background:#13131e;border:1px solid #1e1e2e;border-radius:12px;padding:1.5rem;margin-bottom:2rem">
                <h3 id="ns-editor-title" style="margin-top:0">Produkt bearbeiten</h3>
                <form method="post">
                    <?php wp_nonce_field('nova_shop_admin'); ?>
                    <input type="hidden" name="nova_save_product_meta" value="1">
                    <input type="hidden" name="ns_product_id" id="ns-editor-id" value="">

                    <table class="form-table" style="color:#e2e8f0">
                        <tr>
                            <th>NEU-Badge</th>
                            <td><label><input type="checkbox" name="ns_is_new" id="ns-editor-is-new" value="1"> Als "NEU" markieren</label></td>
                        </tr>
                        <tr>
                            <th>SALE-Badge</th>
                            <td><label><input type="checkbox" name="ns_is_sale" id="ns-editor-is-sale" value="1"> Als "SALE" markieren</label></td>
                        </tr>
                        <tr>
                            <th>Beschreibung</th>
                            <td>
                                <textarea name="ns_description" id="ns-editor-desc" rows="4"
                                          class="large-text"
                                          placeholder="HTML-Beschreibung für den Shop (überschreibt LS-Beschreibung)"></textarea>
                            </td>
                        </tr>
                        <tr>
                            <th>Nutzungshinweis</th>
                            <td>
                                <textarea name="ns_usage" id="ns-editor-usage" rows="2"
                                          class="large-text"
                                          placeholder="z.B. Kompatibel mit Linux (alle Distros) und Windows 10/11"></textarea>
                            </td>
                        </tr>
                        <tr>
                            <th>Bild-URL</th>
                            <td>
                                <input type="url" name="ns_image" id="ns-editor-image"
                                       class="large-text"
                                       placeholder="https://... (überschreibt LemonSqueezy-Vorschaubild)">
                            </td>
                        </tr>
                    </table>
                    <div style="display:flex;gap:.5rem;margin-top:1rem">
                        <input type="submit" class="button button-primary" value="💾 Save">
                        <button type="button" class="button" id="ns-editor-cancel">Abbrechen</button>
                    </div>
                </form>
            </div>

            <!-- 4. Discounts -->
            <h3>Discounts (<?php echo count($all_discounts); ?>)</h3>
            <?php if (!empty($all_discounts)): ?>
            <table class="wp-list-table widefat fixed striped" style="margin-bottom:2rem">
                <thead>
                    <tr>
                        <th>Name</th>
                        <th>Code</th>
                        <th>Betrag</th>
                        <th>Status</th>
                        <th>Gültig bis</th>
                        <th>Nutzungen</th>
                    </tr>
                </thead>
                <tbody>
                <?php foreach ($all_discounts as $d):
                    $amount_fmt = $d['amount_type'] === 'percent'
                        ? $d['amount'] . '%'
                        : number_format($d['amount'] / 100, 2) . ' €';
                    $is_active  = $d['status'] === 'published';
                ?>
                <tr>
                    <td><?php echo esc_html($d['name']); ?></td>
                    <td><code><?php echo esc_html($d['code']); ?></code></td>
                    <td><?php echo esc_html($amount_fmt); ?></td>
                    <td><span style="color:<?php echo $is_active ? '#10b981' : '#64748b'; ?>"><?php echo esc_html($d['status']); ?></span></td>
                    <td><?php echo $d['expires_at'] ? esc_html(date_i18n(get_option('date_format'), strtotime($d['expires_at']))) : '–'; ?></td>
                    <td><?php echo esc_html($d['uses_count']); ?><?php if ($d['is_limited_uses'] && $d['max_redemptions']): ?>/<?php echo esc_html($d['max_redemptions']); ?><?php endif; ?></td>
                </tr>
                <?php endforeach; ?>
                </tbody>
            </table>
            <?php else: ?>
                <p style="color:#94a3b8">Keine Discounts gefunden.</p>
            <?php endif; ?>

            <!-- 5. Shortcode-Info -->
            <h3>Shortcodes</h3>
            <table class="wp-list-table widefat fixed striped">
                <thead><tr><th>Shortcode</th><th>Beschreibung</th><th></th></tr></thead>
                <tbody>
                    <tr>
                        <td><code>[ailinux_shop]</code></td>
                        <td>Shop-Grid mit allen Produkten (3 Spalten)</td>
                        <td><button class="button button-small nov-copy-btn" data-copy="[ailinux_shop]">📋 Copy</button></td>
                    </tr>
                    <tr>
                        <td><code>[ailinux_shop layout="list"]</code></td>
                        <td>Listenansicht</td>
                        <td><button class="button button-small nov-copy-btn" data-copy='[ailinux_shop layout="list"]'>📋 Copy</button></td>
                    </tr>
                    <tr>
                        <td><code>[ailinux_shop highlight="new" columns="2"]</code></td>
                        <td>NEU-Produkte zuerst, 2 Spalten</td>
                        <td><button class="button button-small nov-copy-btn" data-copy='[ailinux_shop highlight="new" columns="2"]'>📋 Copy</button></td>
                    </tr>
                    <tr>
                        <td><code>[ailinux_shop highlight="sale" columns="2"]</code></td>
                        <td>SALE-Produkte zuerst, 2 Spalten</td>
                        <td><button class="button button-small nov-copy-btn" data-copy='[ailinux_shop highlight="sale" columns="2"]'>📋 Copy</button></td>
                    </tr>
                </tbody>
            </table>
        </div>

        <script>
        (function(){
            // ── Test Connection ──────────────────────────────────────────
            document.getElementById('ns-test-conn').addEventListener('click', function(){
                var btn    = this;
                var result = document.getElementById('ns-action-result');
                btn.disabled = true;
                result.textContent = '…';
                fetch(btn.dataset.ajax + '?action=nova_ls_test_connection&nonce=' + encodeURIComponent(btn.dataset.nonce))
                .then(function(r){ return r.json(); })
                .then(function(d){
                    if (d.success) {
                        result.textContent = '✅ Verbunden: ' + d.data.name + ' (' + d.data.currency + ')';
                        result.style.color = '#10b981';
                    } else {
                        result.textContent = '❌ ' + (d.data || 'Error');
                        result.style.color = '#ef4444';
                    }
                })
                .catch(function(e){ result.textContent = '❌ ' + e.message; result.style.color='#ef4444'; })
                .finally(function(){ btn.disabled = false; });
            });

            // ── Clear Cache ──────────────────────────────────────────────
            document.getElementById('ns-clear-cache').addEventListener('click', function(){
                var btn    = this;
                var result = document.getElementById('ns-action-result');
                btn.disabled = true;
                fetch(btn.dataset.ajax + '?action=nova_ls_clear_cache&nonce=' + encodeURIComponent(btn.dataset.nonce))
                .then(function(r){ return r.json(); })
                .then(function(d){
                    result.textContent = d.success ? '✅ Cache cleared' : '❌ Error';
                    result.style.color = d.success ? '#10b981' : '#ef4444';
                    if (d.success) setTimeout(function(){ window.location.reload(); }, 800);
                })
                .finally(function(){ btn.disabled = false; });
            });

            // ── Product Editor ───────────────────────────────────────────
            var productMeta = <?php echo wp_json_encode($product_meta_all); ?>;
            var editor = document.getElementById('ns-product-editor');

            document.querySelectorAll('.ns-edit-product').forEach(function(btn){
                btn.addEventListener('click', function(){
                    var pid  = btn.dataset.id;
                    var name = btn.dataset.name;
                    var meta = productMeta[pid] || {};

                    document.getElementById('ns-editor-title').textContent = '✏️ ' + name;
                    document.getElementById('ns-editor-id').value      = pid;
                    document.getElementById('ns-editor-is-new').checked = !!meta.is_new;
                    document.getElementById('ns-editor-is-sale').checked= !!meta.is_sale;
                    document.getElementById('ns-editor-desc').value    = meta.description || '';
                    document.getElementById('ns-editor-usage').value   = meta.usage || '';
                    document.getElementById('ns-editor-image').value   = meta.image || '';

                    editor.style.display = 'block';
                    editor.scrollIntoView({behavior: 'smooth', block: 'start'});
                });
            });

            document.getElementById('ns-editor-cancel').addEventListener('click', function(){
                editor.style.display = 'none';
            });
        })();
        </script>
        <?php endif; ?>

        <?php elseif ($active_tab === 'auth'): ?>
        <!-- ===== AUTH & BERECHTIGUNGEN TAB ===== -->
        <?php
        $nova_admin_email = defined('NOVA_ADMIN_EMAIL') ? NOVA_ADMIN_EMAIL : 'admin@ailinux.me';
        $current_email    = wp_get_current_user()->user_email;
        if ($current_email !== $nova_admin_email) :
            echo '<div class="notice notice-error"><p>Zugriff verweigert.</p></div>';
        else:
            // Handle tier save
            if (isset($_POST['nova_set_tier']) && wp_verify_nonce($_POST['_wpnonce'], 'nova_admin_action')) {
                $uid   = (int) ($_POST['nova_uid'] ?? 0);
                $tier  = sanitize_text_field($_POST['nova_tier'] ?? '');
                $allowed_tiers = ['free', 'nova_beta', 'tier1', 'tier2', 'tier3', 'nova_lifetime'];
                if ($uid && in_array($tier, $allowed_tiers, true)) {
                    $target = get_userdata($uid);
                    if ($target && $target->user_email !== $nova_admin_email) {
                        update_user_meta($uid, 'nova_tier', $tier);
                        echo '<div class="notice notice-success is-dismissible"><p>✅ Tier für ' . esc_html($target->user_email) . ' auf <strong>' . esc_html($tier) . '</strong> gesetzt.</p></div>';
                    } else {
                        error_log('[NovAI Auth] Blocked admin tier change attempt for uid ' . $uid);
                        echo '<div class="notice notice-error"><p>Aktion blockiert.</p></div>';
                    }
                }
            }
            $wp_users = get_users(['fields' => ['ID', 'user_email', 'display_name'], 'number' => 200]);
            $tier_opts = ['free', 'nova_beta', 'tier1', 'tier2', 'tier3', 'nova_lifetime'];
            $admin_nonce = wp_create_nonce('nova_admin_action');
            $rest_base   = rest_url('nova-ai/v1');
            $wp_nonce    = wp_create_nonce('wp_rest');
        ?>
        <div class="nov-tab-content" id="tab-auth">
            <h2>Auth &amp; Berechtigungen</h2>
            <p style="color:#94a3b8">Einziger Administrator: <code><?php echo esc_html($nova_admin_email); ?></code></p>

            <table class="wp-list-table widefat fixed striped" style="margin-top:1rem">
                <thead>
                    <tr>
                        <th>ID</th>
                        <th>Email</th>
                        <th>Name</th>
                        <th>Nova Tier</th>
                        <th>Tier setzen</th>
                        <th>Session</th>
                    </tr>
                </thead>
                <tbody>
                <?php foreach ($wp_users as $u):
                    $current_tier = get_user_meta($u->ID, 'nova_tier', true) ?: 'free';
                    $is_admin_row = ($u->user_email === $nova_admin_email);
                ?>
                <tr>
                    <td><?php echo (int) $u->ID; ?></td>
                    <td><?php echo esc_html($u->user_email); ?><?php if ($is_admin_row) echo ' <span style="color:#f59e0b">(Admin)</span>'; ?></td>
                    <td><?php echo esc_html($u->display_name); ?></td>
                    <td><strong><?php echo esc_html($current_tier); ?></strong></td>
                    <td>
                        <?php if (!$is_admin_row): ?>
                        <form method="post" style="display:inline-flex;gap:.4rem;align-items:center">
                            <?php wp_nonce_field('nova_admin_action'); ?>
                            <input type="hidden" name="nova_uid" value="<?php echo (int) $u->ID; ?>">
                            <select name="nova_tier" class="regular-text" style="height:28px;padding:0 .4rem">
                                <?php foreach ($tier_opts as $t): ?>
                                <option value="<?php echo esc_attr($t); ?>" <?php selected($current_tier, $t); ?>><?php echo esc_html($t); ?></option>
                                <?php endforeach; ?>
                            </select>
                            <input type="submit" name="nova_set_tier" class="button button-small" value="Setzen">
                        </form>
                        <?php else: echo '<em style="color:#64748b">–</em>'; endif; ?>
                    </td>
                    <td>
                        <?php if (!$is_admin_row): ?>
                        <button class="button button-small nova-invalidate-btn"
                                data-uid="<?php echo (int) $u->ID; ?>"
                                data-nonce="<?php echo esc_attr($wp_nonce); ?>"
                                data-url="<?php echo esc_url($rest_base . '/admin/invalidate-session'); ?>">
                            Session löschen
                        </button>
                        <?php else: echo '<em style="color:#64748b">–</em>'; endif; ?>
                    </td>
                </tr>
                <?php endforeach; ?>
                </tbody>
            </table>
        </div>
        <script>
        document.querySelectorAll('.nova-invalidate-btn').forEach(function(btn){
            btn.addEventListener('click', function(){
                if (!confirm('Session für User ' + btn.dataset.uid + ' wirklich invalidieren?')) return;
                btn.disabled = true;
                fetch(btn.dataset.url, {
                    method: 'POST',
                    headers: {'X-WP-Nonce': btn.dataset.nonce, 'Content-Type': 'application/json'},
                    body: JSON.stringify({user_id: parseInt(btn.dataset.uid)})
                })
                .then(function(r){ return r.json(); })
                .then(function(d){
                    btn.textContent = d.success ? '✓ Done' : '✗ Error';
                })
                .catch(function(){ btn.textContent = '✗ Error'; });
            });
        });
        </script>

        <?php endif; ?>
        <?php elseif ($active_tab === 'payments'): ?>
        <!-- ===== PAYMENTS TAB ===== -->
        <?php
        $nova_admin_email = defined('NOVA_ADMIN_EMAIL') ? NOVA_ADMIN_EMAIL : 'admin@ailinux.me';
        if (wp_get_current_user()->user_email !== $nova_admin_email) :
            echo '<div class="notice notice-error"><p>Zugriff verweigert.</p></div>';
        else:
            $payments_svc  = class_exists('NovAI\Services\PaymentsService') ? \NovAI\Services\PaymentsService::instance() : null;
            $provider_name = defined('NOVA_PAYMENT_PROVIDER') ? NOVA_PAYMENT_PROVIDER : 'stub';
            $status        = $payments_svc ? $payments_svc->get_provider_status() : 'disabled';
            $last_webhook  = $payments_svc ? $payments_svc->get_last_webhook() : [];
            $log_tail      = $payments_svc ? $payments_svc->get_log_tail(50) : '(PaymentsService nicht geladen)';
            $wp_nonce      = wp_create_nonce('wp_rest');
            $rest_base     = rest_url('nova-ai/v1');
            $status_color  = $status === 'active' ? '#10b981' : ($status === 'stub' ? '#f59e0b' : '#ef4444');
        ?>
        <div class="nov-tab-content" id="tab-payments">
            <h2>Payments</h2>

            <div class="nov-stats-grid" style="grid-template-columns:repeat(3,1fr)">
                <div class="nov-stat-card">
                    <div class="nov-stat-icon">🔌</div>
                    <div class="nov-stat-content">
                        <span class="nov-stat-value" style="color:<?php echo esc_attr($status_color); ?>"><?php echo esc_html(strtoupper($status)); ?></span>
                        <span class="nov-stat-label">Provider: <?php echo esc_html($provider_name); ?></span>
                    </div>
                </div>
                <div class="nov-stat-card">
                    <div class="nov-stat-icon">📨</div>
                    <div class="nov-stat-content">
                        <span class="nov-stat-value"><?php echo $last_webhook ? esc_html($last_webhook['event_name'] ?? '–') : '–'; ?></span>
                        <span class="nov-stat-label">Letztes Event<?php echo $last_webhook ? (' · ' . esc_html(date('d.m.Y H:i', $last_webhook['timestamp'] ?? 0))) : ''; ?></span>
                    </div>
                </div>
                <div class="nov-stat-card">
                    <div class="nov-stat-icon">🔁</div>
                    <div class="nov-stat-content">
                        <div style="display:flex;gap:.5rem;align-items:center;flex-wrap:wrap">
                            <input type="number" id="nova-resync-uid" placeholder="WP User ID" style="width:120px;height:32px;padding:0 .5rem" min="1">
                            <button id="nova-resync-btn" class="button button-primary"
                                    data-nonce="<?php echo esc_attr($wp_nonce); ?>"
                                    data-url="<?php echo esc_url($rest_base . '/admin/resync-entitlements'); ?>">
                                Resync Entitlements
                            </button>
                            <span id="nova-resync-result"></span>
                        </div>
                        <span class="nov-stat-label">Entitlements zum Backend synchronisieren</span>
                    </div>
                </div>
            </div>

            <h3 style="margin-top:2rem">Webhook-Endpunkt</h3>
            <code style="display:block;padding:1rem;background:#0a0a0f;border-radius:8px;margin-bottom:1rem;word-break:break-all">
                POST <?php echo esc_url(rest_url('nova-ai/v1/payments/webhook/lemonsqueezy')); ?>
            </code>

            <h3>Payments Log <span style="font-weight:400;font-size:.875rem;color:#64748b">(letzte 50 Zeilen)</span></h3>
            <pre style="background:#0a0a0f;color:#94a3b8;padding:1rem;border-radius:8px;overflow:auto;max-height:400px;font-size:.8rem;line-height:1.5"><?php echo $log_tail; ?></pre>
        </div>
        <script>
        document.getElementById('nova-resync-btn').addEventListener('click', function(){
            var btn = this;
            var uid = parseInt(document.getElementById('nova-resync-uid').value);
            var result = document.getElementById('nova-resync-result');
            if (!uid) { result.textContent = '⚠️ Bitte User ID eingeben'; return; }
            btn.disabled = true;
            result.textContent = '…';
            fetch(btn.dataset.url, {
                method: 'POST',
                headers: {'X-WP-Nonce': btn.dataset.nonce, 'Content-Type': 'application/json'},
                body: JSON.stringify({user_id: uid})
            })
            .then(function(r){ return r.json(); })
            .then(function(d){ result.textContent = d.status === 'ok' ? '✓ Synced' : '✗ ' + JSON.stringify(d); })
            .catch(function(e){ result.textContent = '✗ Error: ' + e.message; })
            .finally(function(){ btn.disabled = false; });
        });
        </script>
        <?php endif; ?>

        <?php endif; ?>

    </div>

</div>

<!-- Agent Call Modal -->
<div class="nov-modal" id="agent-call-modal" style="display: none;">
    <div class="nov-modal-content">
        <div class="nov-modal-header">
            <h3>💬 Call Agent: <span id="modal-agent-name"></span></h3>
            <button class="nov-modal-close">&times;</button>
        </div>
        <div class="nov-modal-body">
            <textarea id="modal-agent-message" rows="4" placeholder="Enter your message..."></textarea>
        </div>
        <div class="nov-modal-footer">
            <button class="button" id="modal-cancel">Cancel</button>
            <button class="button button-primary" id="modal-send">Send Message</button>
        </div>
    </div>
</div>

<!-- Models Modal -->
<div class="nov-modal" id="models-modal" style="display: none;">
    <div class="nov-modal-content nov-modal-large">
        <div class="nov-modal-header">
            <h3>📋 Available AI Models</h3>
            <button class="nov-modal-close">&times;</button>
        </div>
        <div class="nov-modal-body">
            <div id="modal-models-list">Loading...</div>
        </div>
    </div>
</div>

<script>
// Pass config to JavaScript
var novAdminConfig = {
    ajaxUrl: '<?php echo admin_url('admin-ajax.php'); ?>',
    nonce: '<?php echo wp_create_nonce('nov_ai_admin'); ?>',
    apiEndpoint: '<?php echo esc_url($settings['api_endpoint'] ?? 'http://localhost:9100'); ?>',
    mcpEndpoint: '<?php echo esc_url($settings['mcp_endpoint'] ?? 'http://localhost:9100'); ?>'
};
</script>
