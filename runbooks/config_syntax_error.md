# Configuration file syntax error

## Symptoms
- nginx: `[emerg] unexpected "}" in /etc/nginx/nginx.conf:12` or `[emerg] unknown directive`.
- YAML/JSON apps: `yaml.scanner.ScannerError`, `JSONDecodeError`.
- Container exits with code 1 immediately after start.

## How to confirm
- `docker logs` shows the parser error with the file and line number.
- nginx can test its own config: `nginx -t`.

## Common causes
- Missing semicolon or brace in nginx config.
- Tabs/indentation mistakes in YAML.
- Mounted config file edited by hand without validation.

## Fix
- Fix the line reported in the error, validate (`nginx -t`), then recreate the container.
