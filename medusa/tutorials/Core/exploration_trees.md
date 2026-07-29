# Technology Exploration Trees — Complete Reference

When you fingerprint a technology, follow its branching tree. Every technology has specific paths that are unique to it. Test them all.

---

## Apache HTTP Server

```
Apache detected → 
├── /server-status → Apache mod_status (often misconfigured)
├── /server-info → Apache mod_info
├── .htaccess → Check if accessible (misconfiguration)
├── .htpasswd → Backup password file
├── /cgi-bin/ → CGI scripts directory
├── /icons/ → Apache default icons (confirms Apache)
├── /manual/ → Apache documentation (confirms version)
├── /~root/ → User directory traversal
├── /%0a/ → Path normalization bypass
├── CHECK: Server header for exact version → search_cve
├── CHECK: Does OPTIONS return allowed methods?
└── CHECK: TRACE method? → Cross-Site Tracing (CST)
```

## Nginx

```
Nginx detected →
├── /nginx_status → Nginx status page
├── Path traversal on aliases: /../etc/passwd
├── /undefined → Nginx default 404 vs custom 404
├── CHECK: proxy_pass misconfiguration → SSRF
├── CHECK: merge_slashes=off → path traversal
├── CHECK: X-Accel-Redirect header injection
└── CHECK: Lua module (OpenResty) → /lua/
```

## IIS (Microsoft)

```
IIS detected →
├── /owa/auth/ → Exchange Outlook Web Access
├── trace.axd → ASP.NET tracing
├── /aspnet_client/ → ASP.NET version fingerprint
├── web.config → Configuration file access
├── /_vti_bin/ → FrontPage extensions
├── /Autodiscover/ → Exchange Autodiscover
├── /ecp/ → Exchange Control Panel
├── CHECK: Microsoft-HTTPAPI/2.0 in headers
├── CHECK: X-Powered-By: ASP.NET
└── CHECK: WebDAV enabled? → OPTIONS / → PROPFIND
```

## Flask (Python)

```
Flask/Werkzeug detected →
├── /console → Werkzeug debugger console (if debug=True)
├── Error pages → Look for Werkzeug tracebacks with file paths
├── CHECK: Server header → Python/3.x Werkzeug/X.Y.Z
├── CHECK: Does submitting invalid data return a traceback?
├── CHECK: /static/ directory listing
├── CHECK: /.env file accessible?
└── CHECK: Secret key in session cookie → flask-unsign
```

## Django (Python)

```
Django detected →
├── /admin → Django admin panel
├── /api/ → Django REST Framework (if installed)
├── DEBUG=True → traceback pages with settings, DB creds
├── /static/ → Django static files
├── /media/ → Django media uploads
├── CHECK: CSRF token in forms → confirms Django
├── CHECK: Session cookie name → sessionid=
└── CHECK: /graphql (if Graphene-Django installed)
```

## Node.js / Express

```
Node.js/Express detected →
├── /node_modules/ → Directory listing?
├── package.json → Dependencies + versions
├── .env → Environment variables (DB creds, API keys)
├── Source maps → .js.map files reveal TypeScript source
├── /graphql → GraphQL endpoint (Apollo Server)
├── /api/ → REST API
├── CHECK: X-Powered-By: Express
├── CHECK: ETag header → weak vs strong
└── CHECK: /robots.txt → often lists API routes
```

## PHP (Generic)

```
PHP detected →
├── phpinfo.php → PHP info page (versions, modules, paths)
├── /vendor/ → Composer directory listing
├── composer.json → Dependencies
├── .env → Laravel environment
├── /storage/logs/laravel.log → Laravel logs
├── /wp-admin → WordPress
├── /administrator → Joomla
├── /user/login → Drupal
├── CHECK: X-Powered-By: PHP/x.y.z
├── CHECK: PHPSESSID cookie
└── CHECK: index.php in URL → framework routing
```

## WordPress

```
WordPress detected →
├── /wp-json/wp/v2/users → User enumeration (no auth!)
├── /wp-admin → Admin panel
├── /wp-content/uploads/ → Uploads (check for listing)
├── /wp-content/plugins/ → Plugin enumeration
├── /wp-content/themes/ → Theme enumeration
├── xmlrpc.php → XML-RPC (brute-force amplifier)
├── wp-config.php~ → Backup config (passwords!)
├── wp-config.php.bak → Backup config
├── /?author=1 → User ID enumeration
├── CHECK: wp-content/debug.log
└── Use wpscan if available
```

## Java / Spring Boot

```
Java/Spring detected →
├── /actuator → Spring Boot Actuator endpoints
├── /actuator/env → Environment variables
├── /actuator/heapdump → Memory dump (credentials!)
├── /actuator/mappings → All API routes
├── /actuator/beans → All loaded beans
├── /swagger-ui.html → Swagger docs
├── /api-docs → OpenAPI spec
├── /jmx-console → JMX management (old JBoss)
├── /web-console → JBoss console
├── /invoker/JMXInvokerServlet → JBoss RCE
├── .jsp files → JSP endpoints
└── CHECK: Server: Apache-Coyote, X-Application-Context
```

## Ruby on Rails

```
Rails detected →
├── /rails/info → Rails info page
├── /rails/mailers → ActionMailer previews
├── /assets/ → Asset pipeline
├── config/database.yml → Database config
├── config/secrets.yml → Secret key base
├── CHECK: session cookie → _session_id
├── CHECK: X-Runtime header
└── CHECK: /sidekiq → Sidekiq job queue dashboard
```

## GraphQL

```
GraphQL detected →
├── ?query={__schema{types{name,fields{name}}}} → Schema introspection
├── ?query={__type(name:"Query"){name,fields{name,args{name,type{name}}}}} → Discover queries
├── Batching attacks → multiple queries in one request
├── Alias-based batching → bypass rate limiting
├── Depth attacks → deeply nested query (DoS)
└── CHECK: Did introspection succeed? → full API map available
```

## Databases (general)

```
Database port open (3306 MySQL, 5432 PostgreSQL, 1433 MSSQL, 27017 MongoDB) →
├── MySQL 3306 → Check for anonymous access: mysql -h TARGET -u root
├── PostgreSQL 5432 → Check trust auth: psql -h TARGET -U postgres
├── MSSQL 1433 → Check sa account: sqsh -S TARGET -U sa
├── MongoDB 27017 → Check no auth: mongo TARGET
├── Redis 6379 → Check no auth: redis-cli -h TARGET
├── CHECK: Is port accessible from your position?
└── Log to write_note if database is accessible — this is critical
```

## Cloud / S3

```
If target uses AWS/GCP/Azure →
├── Check for S3 buckets: TARGET.s3.amazonaws.com, s3://TARGET
├── Check for open buckets: assets.TARGET.com, static.TARGET.com, media.TARGET.com
├── GCP storage: TARGET.storage.googleapis.com
├── Azure: TARGET.blob.core.windows.net
├── CHECK: CORS misconfiguration on cloud storage
├── CHECK: S3 bucket policy → read/write access
└── CHECK: IAM role metadata: 169.254.169.254
```

## SMTP (port 25/587/465)

```
SMTP detected →
├── VRFY user → User enumeration: VRFY admin, VRFY root
├── EXPN → Expand mailing lists
├── RCPT TO → Recipient enumeration
├── Open relay test: MAIL FROM: <> RCPT TO: <external@test.com>
└── CHECK: Banner for mail server version
```

## FTP (port 21)

```
FTP detected →
├── Anonymous login: ftp TARGET (user: anonymous, pass: anything)
├── If anonymous works → check for readable/writable directories
├── CHECK: Banner for version → search_cve
├── CHECK: PASV vs PORT mode
└── CHECK: Can you upload files? → potential for web shell
```

## SSH (port 22)

```
SSH detected →
├── Banner grab: ssh -v TARGET 2>&1 → version in debug output
├── search_cve for SSH version
├── Check for weak algorithms: ssh -oKexAlgorithms=+diffie-hellman-group1-sha1
├── CHECK: Does it accept password auth? (vs key-only)
└── CHECK: Does it accept root login?
```
