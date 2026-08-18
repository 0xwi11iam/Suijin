"""OAuth Lab — OAuth 2.0 / OIDC misconfigurations. Port 5902."""
from flask import Flask, request, redirect, jsonify, render_template_string
import urllib.parse, json, secrets

app = Flask(__name__)
valid_codes = {}
AUTH_PAGE = """<html><body><h1>Authorize App</h1>
<p>App 'malicious-client' wants access to your account.</p>
<form method="POST"><button>Allow</button></form></body></html>"""

@app.route("/oauth/authorize")
def authorize():
    client_id = request.args.get("client_id","")
    redirect_uri = request.args.get("redirect_uri","")
    state = request.args.get("state","")
    if not redirect_uri: return "Missing redirect_uri", 400
    code = secrets.token_hex(16)
    valid_codes[code] = {"client_id":client_id,"redirect_uri":redirect_uri}
    # VULN: No redirect_uri validation — open redirect
    return redirect(f"{redirect_uri}?code={code}&state={state}")

@app.route("/oauth/token", methods=["POST"])
def token():
    code = request.form.get("code","")
    redirect_uri = request.form.get("redirect_uri","")
    # VULN: redirect_uri not validated against original
    if code in valid_codes: return jsonify({"access_token":"oauth_token_5902","token_type":"bearer"})
    return jsonify({"error":"invalid_grant"}), 400

@app.route("/account")
def account():
    token = request.headers.get("Authorization","").replace("Bearer ","")
    if token == "oauth_token_5902": return jsonify({"user":"admin","email":"admin@corp.com","flag":"FLAG{oauth_token_theft_5902}"})
    return "Unauthorized", 401

app.run(host="0.0.0.0", port=5902, debug=False)
