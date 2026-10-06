
<?php
$payload = json_decode((string)getenv('AILINUX_CF_DNS_PAYLOAD'), true);

function acfd_emit($data) {
    echo "AILINUX_CF_DNS_JSON:" . wp_json_encode($data);
    exit;
}

function acfd_sanitize($rec) {
    $out = [];
    foreach (['id','type','name','content','proxied','ttl','priority','comment','created_on','modified_on'] as $k) {
        if (array_key_exists($k, $rec)) {
            $out[$k] = $rec[$k];
        }
    }
    if (strtoupper((string)($out['type'] ?? '')) === 'TXT' && array_key_exists('content', $out)) {
        $out['content'] = '<redacted TXT content; length=' . strlen((string)$out['content']) . '>';
    }
    return $out;
}

if (!is_array($payload)) {
    acfd_emit(['ok'=>false,'code'=>'INVALID_PAYLOAD','error'=>'invalid payload']);
}
if (!class_exists('\\SPC\\Services\\Settings_Store')) {
    acfd_emit(['ok'=>false,'code'=>'BACKEND_UNAVAILABLE','error'=>'Cloudflare credential backend unavailable']);
}

$settings = \SPC\Services\Settings_Store::get_instance();
$email = (string)$settings->get_cloudflare_api_email();
$key = (string)$settings->get_cloudflare_api_key();

if ($email === '' || $key === '') {
    acfd_emit(['ok'=>false,'code'=>'CREDENTIALS_MISSING','error'=>'Cloudflare credentials are not configured']);
}

$config = (array)get_option('swcfpc_config', []);
$zone_map = (array)($config['cf_zoneid_list'] ?? []);
$zone = strtolower(rtrim((string)($payload['zone'] ?? ''), '.'));
$zone_id = (string)($zone_map[$zone] ?? '');

if ($zone_id === '') {
    acfd_emit([
        'ok'=>false,
        'code'=>'ZONE_NOT_CONFIGURED',
        'error'=>'Zone is not configured in the server-side Cloudflare credential store',
        'zone'=>$zone,
        'available_zones'=>array_values(array_keys($zone_map)),
    ]);
}

$headers = [
    'X-Auth-Email'=>$email,
    'X-Auth-Key'=>$key,
    'Content-Type'=>'application/json',
];

function acfd_request($method, $url, $headers) {
    $resp = wp_remote_request($url, [
        'method'=>$method,
        'timeout'=>20,
        'redirection'=>3,
        'headers'=>$headers,
    ]);
    if (is_wp_error($resp)) {
        return ['ok'=>false,'code'=>'HTTP_CLIENT_ERROR','error'=>$resp->get_error_message()];
    }
    $http = (int)wp_remote_retrieve_response_code($resp);
    $body = json_decode((string)wp_remote_retrieve_body($resp), true);
    if ($http < 200 || $http >= 300 || !is_array($body) || empty($body['success'])) {
        return ['ok'=>false,'code'=>'CLOUDFLARE_API_ERROR','error'=>'Cloudflare API request failed','http_status'=>$http];
    }
    return ['ok'=>true,'http_status'=>$http,'body'=>$body];
}

$base = 'https://api.cloudflare.com/client/v4/zones/' . rawurlencode($zone_id) . '/dns_records';
$op = (string)($payload['op'] ?? '');

if ($op === 'list') {
    $query = ['per_page'=>max(1, min(100, (int)($payload['per_page'] ?? 100)))];
    if (!empty($payload['name'])) $query['name'] = (string)$payload['name'];
    if (!empty($payload['type'])) $query['type'] = strtoupper((string)$payload['type']);
    $r = acfd_request('GET', $base . '?' . http_build_query($query, '', '&', PHP_QUERY_RFC3986), $headers);
    if (empty($r['ok'])) acfd_emit($r);
    $records = [];
    foreach ((array)($r['body']['result'] ?? []) as $rec) {
        $records[] = acfd_sanitize((array)$rec);
    }
    acfd_emit(['ok'=>true,'zone'=>$zone,'count'=>count($records),'records'=>$records]);
}

$record_id = (string)($payload['record_id'] ?? '');
if ($record_id === '') {
    acfd_emit(['ok'=>false,'code'=>'RECORD_ID_REQUIRED','error'=>'record_id is required']);
}

$get_url = $base . '/' . rawurlencode($record_id);
$get = acfd_request('GET', $get_url, $headers);
if (empty($get['ok'])) acfd_emit($get);
$record = (array)($get['body']['result'] ?? []);

if ($op === 'get') {
    acfd_emit(['ok'=>true,'zone'=>$zone,'record'=>acfd_sanitize($record)]);
}

if ($op === 'delete') {
    $expected_name = strtolower(rtrim((string)($payload['expected_name'] ?? ''), '.'));
    $expected_type = strtoupper((string)($payload['expected_type'] ?? ''));
    $actual_name = strtolower(rtrim((string)($record['name'] ?? ''), '.'));
    $actual_type = strtoupper((string)($record['type'] ?? ''));

    if ($actual_name !== $expected_name || $actual_type !== $expected_type) {
        acfd_emit([
            'ok'=>false,
            'code'=>'IDENTITY_MISMATCH',
            'error'=>'Record identity mismatch; deletion refused',
            'actual'=>['name'=>$actual_name,'type'=>$actual_type],
        ]);
    }

    if (in_array($actual_type, ['NS','SOA'], true)) {
        acfd_emit(['ok'=>false,'code'=>'PROTECTED_RECORD_TYPE','error'=>'Protected DNS record type; deletion refused']);
    }

    if (empty($payload['confirm'])) {
        acfd_emit(['ok'=>true,'deleted'=>false,'dry_run'=>true,'zone'=>$zone,'record'=>acfd_sanitize($record)]);
    }

    $del = acfd_request('DELETE', $get_url, $headers);
    if (empty($del['ok'])) acfd_emit($del);

    acfd_emit([
        'ok'=>true,
        'deleted'=>true,
        'zone'=>$zone,
        'record'=>[
            'id'=>(string)($record['id'] ?? $record_id),
            'name'=>$actual_name,
            'type'=>$actual_type,
        ],
    ]);
}

acfd_emit(['ok'=>false,'code'=>'UNSUPPORTED_OPERATION','error'=>'Unsupported operation']);
